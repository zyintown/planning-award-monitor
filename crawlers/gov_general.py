"""政府网站通用爬虫 - 适配典型 gov.cn 页面结构"""

import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from crawlers.base import BaseCrawler, Article
from utils.http_client import fetch_html
from utils.logger import get_logger

logger = get_logger()


class GovGeneralCrawler(BaseCrawler):
    """政府网站通用爬虫

    适配典型结构：<ul><li><a href="...">标题</a><span>日期</span></li></ul>
    或 <table><tr><td><a>标题</a></td><td>日期</td></tr></table>
    """

    def _request_page(self) -> str | None:
        crawler_config = self.config.get("crawler", {})
        return fetch_html(
            url=self.url,
            timeout=crawler_config.get("timeout", 15),
            retry_times=crawler_config.get("retry_times", 3),
            retry_delay=crawler_config.get("retry_delay", 2),
            user_agent=crawler_config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
        )

    def _parse(self, html: str) -> list[Article]:
        soup = BeautifulSoup(html, "lxml")
        articles = []

        # 策略: 查找列表中的链接（最常见的政府网站结构）
        items = soup.select("ul li a") + soup.select("table td a") + soup.select("div.list a")

        seen_urls = set()
        for a_tag in items:
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")

            if not title or not href:
                continue
            if len(title) < 6:  # 过滤太短的（可能是导航按钮）
                continue
            if href.startswith("javascript:") or href.startswith("#"):
                continue

            full_url = urljoin(self.url, href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            # 尝试从父元素中提取日期
            publish_date = self._extract_date(a_tag)

            articles.append(
                Article(
                    title=title,
                    url=full_url,
                    source=self.name,
                    publish_date=publish_date,
                    summary="",
                )
            )

        return articles

    def _extract_date(self, a_tag) -> str:
        """从a标签的父元素中提取日期"""
        parent = a_tag.parent
        if parent:
            text = parent.get_text()
            # 匹配 YYYY-MM-DD 或 YYYY.MM.DD 或 YYYY年MM月DD日
            date_match = re.search(
                r"(\d{4}[-./年]\d{1,2}[-./月]\d{1,2})", text
            )
            if date_match:
                date_str = date_match.group(1)
                # 统一为 YYYY-MM-DD
                date_str = date_str.replace("年", "-").replace("月", "-").replace("日", "")
                date_str = date_str.replace(".", "-").replace("/", "-")
                return date_str
        return ""
