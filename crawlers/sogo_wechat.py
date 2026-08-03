"""搜狗微信搜索爬虫 - 按公众号名称搜索最新文章"""

import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote, urljoin
from bs4 import BeautifulSoup
from crawlers.base import BaseCrawler, Article, CrawlResult
from utils.http_client import fetch_html
from utils.logger import get_logger

logger = get_logger()

# 搜狗微信搜索URL模板
SOGO_SEARCH_URL = "https://weixin.sogou.com/weixin?type=2&query={}&ie=utf8"


class SogoWechatCrawler(BaseCrawler):
    """搜狗微信搜索爬虫

    按公众号名称搜索文章，提取搜索结果中的文章列表。
    搜狗有反爬机制，失败时返回空列表。
    """

    def __init__(
        self,
        name: str,
        keyword: str,
        config: dict,
        account_aliases: list[str] | None = None,
        search_queries: list[str] | None = None,
        max_pages: int = 1,
        interval_seconds: float = 0,
        require_account_match: bool = False,
    ):
        super().__init__(name=name, url="", config=config)
        self.keyword = keyword
        self.account_aliases = account_aliases or [name, keyword]
        self.search_queries = search_queries or [keyword]
        self.max_pages = max(1, int(max_pages))
        self.interval_seconds = max(0, float(interval_seconds))
        self.require_account_match = require_account_match
        self._fail_count = 0
        self._last_parse_metrics = {
            "raw_result_count": 0,
            "account_matched_count": 0,
        }

    def fetch(self) -> list[Article]:
        """兼容旧调用方式，仅返回文章列表。"""
        return self.fetch_result().articles

    def fetch_result(self) -> CrawlResult:
        """搜狗渠道结构化结果。"""
        started_at = time.monotonic()
        logger.info(f"开始搜狗微信搜索: {self.keyword}")
        try:
            articles = []
            pages_fetched = 0
            failed_pages = 0
            raw_result_count = 0
            account_matched_count = 0
            total_requests = len(self.search_queries) * self.max_pages
            request_index = 0
            for query in self.search_queries:
                for page in range(1, self.max_pages + 1):
                    request_index += 1
                    html = self._request_page(query=query, page=page)
                    if not html:
                        failed_pages += 1
                        if request_index < total_requests and self.interval_seconds > 0:
                            time.sleep(self.interval_seconds)
                        continue
                    pages_fetched += 1
                    page_articles = self._parse(html)
                    page_metrics = self._last_parse_metrics
                    raw_result_count += page_metrics["raw_result_count"]
                    account_matched_count += page_metrics["account_matched_count"]
                    articles.extend(page_articles)
                    if request_index < total_requests and self.interval_seconds > 0:
                        time.sleep(self.interval_seconds)

            if not articles and failed_pages:
                self._fail_count += 1
                return CrawlResult(
                    self.name,
                    "wechat",
                    "failed",
                    [],
                    time.monotonic() - started_at,
                    error="搜狗请求失败或响应为空",
                    metrics={
                        "raw_result_count": raw_result_count,
                        "account_matched_count": account_matched_count,
                        "window_kept_count": 0,
                    },
                )
            if not articles:
                self._fail_count += 1
                status = "empty"
            else:
                status = "success"
                logger.info(f"搜狗搜索完成: {self.keyword} ({len(articles)}条)")
            return CrawlResult(
                self.name,
                "wechat",
                status,
                articles,
                time.monotonic() - started_at,
                pages_fetched=pages_fetched,
                error=(f"{failed_pages}个页面请求失败" if failed_pages else ""),
                metrics={
                    "raw_result_count": raw_result_count,
                    "account_matched_count": account_matched_count,
                    "window_kept_count": len(articles),
                },
            )
        except Exception as e:
            logger.warning(f"搜狗搜索异常: {self.keyword} - {type(e).__name__}: {e}")
            self._fail_count += 1
            return CrawlResult(
                self.name,
                "wechat",
                "failed",
                [],
                time.monotonic() - started_at,
                error=f"{type(e).__name__}: {e}",
                metrics={
                    "raw_result_count": 0,
                    "account_matched_count": 0,
                    "window_kept_count": 0,
                },
            )

    @property
    def fail_count(self) -> int:
        return self._fail_count

    def _request_page(
        self,
        url: str = None,
        query: str | None = None,
        page: int = 1,
    ) -> str | None:
        # 搜狗自行构造搜索URL，忽略传入的url参数；page从1开始。
        search_url = SOGO_SEARCH_URL.format(quote(query or self.keyword))
        if page > 1:
            search_url += f"&page={page}"
        crawler_config = self.config.get("crawler", {})
        return fetch_html(
            url=search_url,
            timeout=crawler_config.get("timeout", 15),
            retry_times=2,  # 搜狗减少重试次数，避免触发更严格的封禁
            retry_delay=5,  # 搜狗重试间隔更长
            user_agent=crawler_config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
        )

    def _parse(self, html: str) -> list[Article]:
        soup = BeautifulSoup(html, "lxml")
        articles = []
        raw_result_count = 0
        account_matched_count = 0

        # 搜狗微信搜索结果结构：<div class="txt-box"> ... <h3><a>标题</a></h3> ...
        news_items = soup.select("div.txt-box")

        for item in news_items:
            a_tag = item.select_one("h3 a") or item.select_one("a")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")

            if not title or not href:
                continue
            raw_result_count += 1

            # 搜狗的链接是相对路径，需要拼接
            if href.startswith("/"):
                full_url = urljoin("https://weixin.sogou.com", href)
            else:
                full_url = href

            # 提取摘要
            summary = ""
            summary_tag = item.select_one("p.txt-info") or item.select_one("p")
            if summary_tag:
                summary = summary_tag.get_text(strip=True)

            # 提取公众号名称
            account_name = ""
            account_tag = item.select_one("span.all-time-y2") or item.select_one("a.account")
            if account_tag:
                account_name = account_tag.get_text(strip=True)

            # 搜索关键词可能命中其他公众号转载；能识别账号名时必须核对来源。
            account_matches = bool(account_name) and self._matches_expected_account(account_name)
            if (account_name and not account_matches) or (
                self.require_account_match and not account_matches
            ):
                logger.debug(f"公众号身份不匹配，跳过: {account_name} / {title}")
                continue
            account_matched_count += 1

            # 提取日期（搜狗通过 timeConvert('unix时间戳') JS渲染）
            publish_date = ""
            time_tag = item.select_one("span.s2") or item.select_one("span.time")
            if time_tag:
                # 方式1: 从 script 中提取 Unix 时间戳
                script_text = time_tag.find("script")
                raw_text = script_text.string if script_text else time_tag.get_text(strip=True)
                ts_match = re.search(r"timeConvert\(['\"]?(\d{10})['\"]?\)", raw_text or "")
                if ts_match:
                    publish_date = time.strftime("%Y-%m-%d", time.localtime(int(ts_match.group(1))))
                else:
                    # 方式2: 纯文本日期
                    date_match = re.search(r"(\d{4}[-./年]\d{1,2}[-./月]\d{1,2})", raw_text or "")
                    if date_match:
                        publish_date = (
                            date_match.group(1)
                            .replace("年", "-")
                            .replace("月", "-")
                            .replace("日", "")
                            .replace(".", "-")
                            .replace("/", "-")
                        )

            articles.append(
                Article(
                    title=title,
                    url=full_url,
                    source=f"微信公众号:{account_name or self.keyword}",
                    publish_date=publish_date,
                    summary=summary,
                )
            )

        self._last_parse_metrics = {
            "raw_result_count": raw_result_count,
            "account_matched_count": account_matched_count,
        }
        return articles

    def _matches_expected_account(self, account_name: str) -> bool:
        normalized = re.sub(r"\s+", "", account_name).lower()
        return any(
            normalized == re.sub(r"\s+", "", alias).lower()
            for alias in self.account_aliases
            if alias
        )


def filter_backfill_window(
    articles: list[Article],
    days: int = 3,
    today: date | None = None,
) -> list[Article]:
    """保留补抓窗口内文章；日期缺失时保守保留，避免静默漏报。"""
    reference_date = today or date.today()
    cutoff = reference_date - timedelta(days=max(1, days) - 1)
    kept = []
    for article in articles:
        if not article.publish_date:
            kept.append(article)
            continue
        try:
            published = datetime.strptime(article.publish_date, "%Y-%m-%d").date()
        except ValueError:
            kept.append(article)
            continue
        if cutoff <= published <= reference_date:
            kept.append(article)
    return kept
