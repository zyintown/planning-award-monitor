"""爬虫基类 - 模板方法模式，定义统一接口"""

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from utils.logger import get_logger
from utils.http_client import fetch_html, fetch_response

logger = get_logger()


@dataclass
class Article:
    """统一文章数据结构"""
    title: str
    url: str
    source: str
    publish_date: str = ""
    summary: str = ""
    raw_content: str = ""
    db_id: int | None = None
    award_name: str = ""
    deadline_text: str = ""
    deadline_date: str = ""
    applicant_scope: list[str] = field(default_factory=list)
    created_at: str = ""
    status: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "publish_date": self.publish_date,
            "summary": self.summary,
            "raw_content": self.raw_content,
            "award_name": self.award_name,
            "deadline_text": self.deadline_text,
            "deadline_date": self.deadline_date,
            "applicant_scope": list(self.applicant_scope),
        }


@dataclass
class CrawlResult:
    """单个渠道一次抓取的结构化结果。"""

    source: str
    source_type: str
    status: str
    articles: list[Article]
    duration_seconds: float
    pages_fetched: int = 0
    error: str = ""
    metrics: dict = field(default_factory=dict)

    @property
    def has_error(self) -> bool:
        return self.status in {"failed", "partial", "anomaly"}


class BaseCrawler:
    """所有爬虫的基类。

    子类需实现 _parse()，可选重写 _request_page() 和 _find_next_page()。
    fetch() 自动翻页：抓第一页 → 找"下一页"链接 → 跟进 → 重复，
    直到无下一页、达到 max_pages 或本页全部是已抓过的重复文章。
    """

    def __init__(self, name: str, url: str, config: dict,
                 pagination: dict = None, max_pages_override: int = None,
                 ssl_verify: bool = True):
        self.name = name
        self.url = url
        self.config = config  # 完整config字典
        self.pagination = pagination or {}
        self.max_pages_override = max_pages_override
        self.ssl_verify = ssl_verify

    def fetch(self) -> list[Article]:
        """兼容旧调用方式，仅返回文章列表。"""
        return self.fetch_result().articles

    def fetch_result(self) -> CrawlResult:
        """抓取页面并返回可区分失败、空结果和部分成功的结构化结果。"""
        started_at = time.monotonic()
        logger.info(f"开始抓取: {self.name}")
        crawler_config = self.config.get("crawler", {})
        max_pages = self.max_pages_override or crawler_config.get("max_pages", 3)
        mode = self.pagination.get("mode", "auto")

        # none 模式：仅抓单页
        if mode == "none":
            max_pages = 1

        all_articles: list[Article] = []
        seen_urls: set[str] = set()
        current_url = self.url
        status = "success"
        error = ""
        pages_fetched = 0

        for page_num in range(1, max_pages + 1):
            try:
                # construct 模式：直接构造每页URL
                if mode == "construct":
                    current_url = self._build_page_url(page_num)

                self._current_page_url = current_url
                html = self._request_page(current_url)
                if not html:
                    status = "failed" if not all_articles else "partial"
                    error = f"第{page_num}页请求失败或响应为空"
                    if page_num == 1:
                        logger.warning(f"抓取失败（页面为空）: {self.name}")
                    break

                pages_fetched += 1
                articles = self._parse(html)
                if not articles:
                    if page_num == 1:
                        status = "empty"
                    break

                # 统计本页新文章
                new_count = 0
                for a in articles:
                    if a.url not in seen_urls:
                        seen_urls.add(a.url)
                        all_articles.append(a)
                        new_count += 1

                # 第2页起若全部重复，说明已追上历史数据
                if new_count == 0 and page_num > 1:
                    logger.debug(f"{self.name} 第{page_num}页全部重复，停止翻页")
                    break

                logger.debug(
                    f"{self.name} 第{page_num}页: {len(articles)}条 (新增{new_count}条)"
                )

                if page_num >= max_pages:
                    break

                # construct 模式不需要查找下一页链接，直接进入下一页
                if mode == "construct":
                    continue

                # auto 模式：从HTML中查找下一页链接
                next_url = self._find_next_page(html, current_url)
                if not next_url or next_url == current_url:
                    break

                current_url = next_url

            except Exception as e:
                status = "failed" if not all_articles else "partial"
                error = f"第{page_num}页 {type(e).__name__}: {e}"
                logger.warning(
                    f"抓取异常: {self.name} 第{page_num}页 - {type(e).__name__}: {e}"
                )
                break

        logger.info(f"抓取完成: {self.name} ({len(all_articles)}条)")
        return CrawlResult(
            source=self.name,
            source_type="website",
            status=status,
            articles=all_articles,
            duration_seconds=time.monotonic() - started_at,
            pages_fetched=pages_fetched,
            error=error,
            metrics={
                "raw_result_count": len(all_articles),
                "account_matched_count": len(all_articles),
                "window_kept_count": len(all_articles),
            },
        )

    def _build_page_url(self, page_num: int) -> str:
        """根据pagination配置构造指定页码的URL。"""
        p = self.pagination
        url_template = p.get("url_template", self.url)
        first_page_url = p.get("first_page_url")
        n_start = p.get("n_start", 1)
        n_step = p.get("n_step", 1)

        if page_num == 1 and first_page_url:
            return first_page_url

        if first_page_url:
            n = n_start + (page_num - 2) * n_step
        else:
            n = n_start + (page_num - 1) * n_step

        return url_template.replace("{n}", str(n))

    def _request_page(self, url: str = None) -> str | None:
        """请求指定URL页面，返回HTML文本。子类一般不需重写。"""
        if url is None:
            url = self.url
        crawler_config = self.config.get("crawler", {})
        return fetch_html(
            url=url,
            timeout=crawler_config.get("timeout", 15),
            retry_times=crawler_config.get("retry_times", 3),
            retry_delay=crawler_config.get("retry_delay", 2),
            user_agent=crawler_config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
            verify_ssl=self.ssl_verify,
        )

    def _find_next_page(self, html: str, current_url: str) -> str | None:
        """从HTML中查找下一页URL，找不到返回None。子类可重写以适配特殊分页。"""
        soup = BeautifulSoup(html, "lxml")

        # 策略1: 查找"下一页"等文本链接（最常见的政府网站分页方式）
        next_texts = ("下一页", "下页", "next", "Next", "NEXT", "»", "›")
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            if text in next_texts:
                href = a.get("href", "")
                if href and not href.startswith(("javascript:", "#")):
                    return urljoin(current_url, href)

        # 策略2: 查找 rel="next" 链接
        a = soup.find("a", rel="next", href=True)
        if a:
            href = a.get("href", "")
            if href and not href.startswith(("javascript:", "#")):
                return urljoin(current_url, href)

        # 策略3: 查找 class 含 "next" 的分页链接
        for a in soup.find_all("a", href=True):
            classes = " ".join(a.get("class", []))
            if "next" in classes.lower():
                href = a.get("href", "")
                if href and not href.startswith(("javascript:", "#")):
                    return urljoin(current_url, href)

        return None

    def _parse(self, html: str) -> list[Article]:
        """子类实现：解析HTML，返回Article列表"""
        raise NotImplementedError


def enrich_article(
    article: Article,
    config: dict,
    fetcher=fetch_response,
) -> tuple[Article, str]:
    """为新增文章抓取详情正文；失败时保留原文章并返回错误信息。"""
    detail_config = config.get("crawler", {}).get("detail", {})
    crawler_config = config.get("crawler", {})
    max_content_length = int(detail_config.get("max_content_length", 5000))
    summary_length = int(detail_config.get("summary_length", 500))

    # 对配置中声明证书过期的域名跳过 SSL 验证
    ssl_skip_domains = crawler_config.get("ssl_skip_domains", [])
    article_domain = urlparse(article.url).netloc.lower()
    verify_ssl = not any(
        article_domain == d.lower() or article_domain.endswith("." + d.lower())
        for d in ssl_skip_domains
    )

    try:
        fetched = fetcher(
            url=article.url,
            timeout=detail_config.get("timeout", crawler_config.get("timeout", 15)),
            retry_times=detail_config.get("retry_times", 2),
            retry_delay=crawler_config.get("retry_delay", 2),
            user_agent=crawler_config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
            verify_ssl=verify_ssl,
        )
        if not fetched:
            return article, "详情页请求失败或响应为空"
        if isinstance(fetched, str):
            html = fetched
        else:
            html = fetched.text
            if fetched.final_url:
                article.url = fetched.final_url

        soup = BeautifulSoup(html, "lxml")
        for unwanted in soup.select("script, style, nav, header, footer, aside, noscript"):
            unwanted.decompose()

        content_node = None
        selectors = detail_config.get(
            "selectors",
            [
                "article",
                "main",
                ".article-content",
                ".contentDetail",
                ".content",
                ".TRS_Editor",
                ".news-content",
                "#content",
            ],
        )
        for selector in selectors:
            candidate = soup.select_one(selector)
            if candidate and len(candidate.get_text(strip=True)) >= 30:
                content_node = candidate
                break
        if content_node is None:
            content_node = soup.body or soup

        content = re.sub(r"\s+", " ", content_node.get_text(" ", strip=True)).strip()
        if not content:
            return article, "详情页未提取到正文"

        article.raw_content = content[:max_content_length]
        # 列表页摘要常包含导航或截断文本；详情成功后必须以正文首段重建摘要。
        article.summary = article.raw_content[:summary_length]
        return article, ""
    except Exception as exc:
        return article, f"详情抓取异常 {type(exc).__name__}: {exc}"
