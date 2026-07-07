"""学会/协会网站通用爬虫 - 适配各协会网站不同结构"""

import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from crawlers.base import BaseCrawler, Article
from utils.http_client import fetch_html
from utils.logger import get_logger

logger = get_logger()


class OrgGeneralCrawler(BaseCrawler):
    """学会/协会网站通用爬虫

    学会协会网站结构差异较大，采用更宽松的解析策略：
    1. 查找所有带href的a标签
    2. 过滤导航/底部等无关链接
    3. 按标题长度和关键词相关性筛选
    """

    # 与报奖相关的标题关键词，用于过滤无关文章
    RELEVANT_KEYWORDS = [
        "奖", "申报", "推荐", "提名", "评选", "通知", "公告",
        "开展", "组织", "征集", "报名", "转发", "公示",
    ]

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

        # 查找所有a标签
        all_links = soup.find_all("a", href=True)

        seen_urls = set()
        for a_tag in all_links:
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")

            if not title or not href:
                continue
            if len(title) < 8:  # 协会网站过滤更严格
                continue
            if href.startswith("javascript:") or href.startswith("#"):
                continue
            if href.startswith("mailto:") or href.startswith("tel:"):
                continue

            full_url = urljoin(self.url, href)
            if full_url in seen_urls:
                continue
            # 排除站外导航链接（但保留文章链接）
            if self._is_navigation_link(a_tag):
                continue
            seen_urls.add(full_url)

            # 检查是否与报奖相关
            if not any(kw in title for kw in self.RELEVANT_KEYWORDS):
                continue

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

    def _is_navigation_link(self, a_tag) -> bool:
        """判断是否为导航/菜单链接（而非文章链接）"""
        # 检查父元素是否为 nav, menu 等导航区域
        for parent in a_tag.parents:
            if parent.name in ("nav", "header", "footer"):
                return True
            parent_class = " ".join(parent.get("class", []))
            if any(
                nav_kw in parent_class.lower()
                for nav_kw in ["nav", "menu", "header", "footer", "sidebar"]
            ):
                return True
        return False

    def _extract_date(self, a_tag) -> str:
        """从a标签的父元素中提取日期"""
        parent = a_tag.parent
        if parent:
            text = parent.get_text()
            date_match = re.search(
                r"(\d{4}[-./年]\d{1,2}[-./月]\d{1,2})", text
            )
            if date_match:
                date_str = date_match.group(1)
                date_str = date_str.replace("年", "-").replace("月", "-").replace("日", "")
                date_str = date_str.replace(".", "-").replace("/", "-")
                return date_str
        return ""
