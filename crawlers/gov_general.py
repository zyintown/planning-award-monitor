"""政府网站通用爬虫"""

import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from crawlers.base import BaseCrawler, Article
from utils.logger import get_logger

logger = get_logger()

class GovGeneralCrawler(BaseCrawler):

    def _parse(self, html):
        soup = BeautifulSoup(html, "lxml")
        articles = []
        selectors = self.pagination.get("selectors", [])
        if not selectors:
            selectors = ["ul li a", "table td a", "div.list a"]
        items = []
        for sel in selectors:
            items += soup.select(sel)
        base_domain = urlparse(self.url).netloc
        allowed_domains = self.pagination.get("allowed_domains", [])
        seen_urls = set()
        for a_tag in items:
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            if not title or not href:
                continue
            if len(title) < 6:
                continue
            if href.startswith("javascript:") or href.startswith("#"):
                continue
            full_url = urljoin(getattr(self, "_current_page_url", self.url), href)
            if full_url in seen_urls:
                continue
            link_domain = urlparse(full_url).netloc
            if link_domain and not self._domain_allowed(link_domain, base_domain, allowed_domains):
                continue
            if self._is_navigation_link(a_tag):
                continue
            seen_urls.add(full_url)
            publish_date = self._extract_date(a_tag)
            articles.append(Article(title=title, url=full_url, source=self.name, publish_date=publish_date, summary=""))
        return articles

    def _domain_allowed(self, link_domain, base_domain, allowed_domains):
        if link_domain == base_domain:
            return True
        for d in allowed_domains:
            if link_domain == d or link_domain.endswith("." + d):
                return True
        root = base_domain.split(".")
        if len(root) >= 2:
            root_domain = ".".join(root[-2:])
            if link_domain.endswith("." + root_domain) or link_domain == root_domain:
                return True
        return False

    def _is_navigation_link(self, a_tag):
        for parent in a_tag.parents:
            if parent.name in ("nav", "header", "footer"):
                return True
            parent_class = " ".join(parent.get("class", []))
            parent_id = parent.get("id", "")
            combined = (parent_class + " " + parent_id).lower()
            if any(kw in combined for kw in ["nav", "menu", "header", "footer", "sidebar", "foot", "bottom", "friend", "copyright", "icp"]):
                return True
        return False

    def _extract_date(self, a_tag):
        parent = a_tag.parent
        if parent:
            text = parent.get_text()
            date_match = re.search(r"(\d{4}[-./年]\d{1,2}[-./月]\d{1,2})", text)
            if date_match:
                date_str = date_match.group(1)
                date_str = date_str.replace("年", "-").replace("月", "-").replace("日", "")
                date_str = date_str.replace(".", "-").replace("/", "-")
                return date_str
        return ""
