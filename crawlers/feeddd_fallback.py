"""feeddd降级爬虫 - 搜狗不可用时的备用公众号文章源"""

import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from crawlers.base import BaseCrawler, Article
from utils.http_client import fetch_html
from utils.logger import get_logger

logger = get_logger()

# feeddd搜索URL
FEEDDD_SEARCH_URL = "https://feeddd.org/feeds/search?keyword={}"


class FeedddFallbackCrawler(BaseCrawler):
    """feeddd降级爬虫

    通过 feeddd.org 搜索公众号文章。
    feeddd 是免费服务，不保证长期稳定，作为搜狗的降级方案。
    """

    def __init__(self, name: str, keyword: str, config: dict):
        super().__init__(name=name, url="", config=config)
        self.keyword = keyword

    def fetch(self) -> list[Article]:
        logger.info(f"feeddd降级搜索: {self.keyword}")
        try:
            html = self._request_page()
            if not html:
                return []
            articles = self._parse(html)
            logger.info(f"feeddd搜索完成: {self.keyword} ({len(articles)}条)")
            return articles
        except Exception as e:
            logger.warning(f"feeddd搜索异常: {self.keyword} - {type(e).__name__}: {e}")
            return []

    def _request_page(self) -> str | None:
        search_url = FEEDDD_SEARCH_URL.format(self.keyword)
        crawler_config = self.config.get("crawler", {})
        return fetch_html(
            url=search_url,
            timeout=crawler_config.get("timeout", 15),
            retry_times=2,
            retry_delay=3,
            user_agent=crawler_config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
        )

    def _parse(self, html: str) -> list[Article]:
        soup = BeautifulSoup(html, "lxml")
        articles = []

        # feeddd 页面结构可能变化，采用宽松解析
        items = soup.select("div.feed-item") or soup.select("article") or soup.select("div.item")

        for item in items:
            a_tag = item.select_one("a")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")

            if not title or not href:
                continue

            full_url = urljoin("https://feeddd.org", href)

            summary = ""
            summary_tag = item.select_one("p") or item.select_one("div.summary")
            if summary_tag:
                summary = summary_tag.get_text(strip=True)

            publish_date = ""
            date_tag = item.select_one("time") or item.select_one("span.date")
            if date_tag:
                date_text = date_tag.get_text(strip=True)
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
                    source=f"微信公众号:{self.keyword}",
                    publish_date=publish_date,
                    summary=summary,
                )
            )

        return articles
