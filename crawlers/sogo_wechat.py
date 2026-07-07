"""搜狗微信搜索爬虫 - 按公众号名称搜索最新文章"""

import re
from urllib.parse import quote, urljoin
from bs4 import BeautifulSoup
from crawlers.base import BaseCrawler, Article
from utils.http_client import fetch_html
from utils.logger import get_logger

logger = get_logger()

# 搜狗微信搜索URL模板
SOGO_SEARCH_URL = "https://weixin.sogou.com/weixin?type=2&query={}&ie=utf8"


class SogoWechatCrawler(BaseCrawler):
    """搜狗微信搜索爬虫

    按公众号名称搜索文章，提取搜索结果中的文章列表。
    搜狗有反爬机制，失败时返回空列表，由上层决定是否降级到feeddd。
    """

    def __init__(self, name: str, keyword: str, config: dict):
        super().__init__(name=name, url="", config=config)
        self.keyword = keyword
        self._fail_count = 0

    def fetch(self) -> list[Article]:
        """重写fetch，搜狗需要特殊处理"""
        logger.info(f"开始搜狗微信搜索: {self.keyword}")
        try:
            html = self._request_page()
            if not html:
                self._fail_count += 1
                return []
            articles = self._parse(html)
            if not articles:
                self._fail_count += 1
            else:
                logger.info(f"搜狗搜索完成: {self.keyword} ({len(articles)}条)")
            return articles
        except Exception as e:
            logger.warning(f"搜狗搜索异常: {self.keyword} - {type(e).__name__}: {e}")
            self._fail_count += 1
            return []

    @property
    def fail_count(self) -> int:
        return self._fail_count

    def _request_page(self) -> str | None:
        search_url = SOGO_SEARCH_URL.format(quote(self.keyword))
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

        # 搜狗微信搜索结果结构：<div class="news-box"> ... <h3><a href="...">标题</a></h3> ...
        news_items = soup.select("div.news-box") or soup.select("div.txt-box")

        for item in news_items:
            a_tag = item.select_one("h3 a") or item.select_one("a")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")

            if not title or not href:
                continue

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

            # 提取公众号名称和日期
            account_name = ""
            account_tag = item.select_one("a.account")
            if account_tag:
                account_name = account_tag.get_text(strip=True)

            # 提取日期（搜狗显示的是相对时间或绝对日期）
            publish_date = ""
            time_tag = item.select_one("span.s2") or item.select_one("span.time")
            if time_tag:
                date_text = time_tag.get_text(strip=True)
                date_match = re.search(r"(\d{4}[-./年]\d{1,2}[-./月]\d{1,2})", date_text)
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

        return articles
