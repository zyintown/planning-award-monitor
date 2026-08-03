"""学会/协会网站通用爬虫 - 适配各协会网站不同结构"""

import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from crawlers.base import BaseCrawler, Article
from utils.logger import get_logger

logger = get_logger()


class OrgGeneralCrawler(BaseCrawler):
    """学会/协会网站通用爬虫

    学会协会网站结构差异较大，采用更宽松的解析策略：
    1. 查找所有带href的a标签
    2. 过滤导航/底部等无关链接
    3. 按标题长度和关键词相关性筛选
    """

    def _parse(self, html: str) -> list[Article]:
        soup = BeautifulSoup(html, "lxml")
        articles = []

        # 支持配置selectors精确限定抓取区域
        selectors = self.pagination.get("selectors", [])
        if selectors:
            all_links = []
            for sel in selectors:
                all_links += soup.select(sel)
        else:
            all_links = soup.find_all("a", href=True)

        seen_urls = set()
        current_page_url = getattr(self, "_current_page_url", self.url)
        base_domain = urlparse(self.url).netloc.lower()
        allowed_domains = [
            domain.lower() for domain in self.pagination.get("allowed_domains", [])
        ]
        title_keywords = self.pagination.get("title_keywords", [])
        for a_tag in all_links:
            h_tags = a_tag.find_all(["h4","h3","h2","h5"])
            if h_tags:
                title = h_tags[-1].get_text(strip=True)
            else:
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

            full_url = urljoin(current_page_url, href)
            if full_url in seen_urls:
                continue
            link_domain = urlparse(full_url).netloc.lower()
            if link_domain and not self._domain_allowed(
                link_domain, base_domain, allowed_domains
            ):
                continue
            # 排除站外导航链接（但保留文章链接）
            if self._is_navigation_link(a_tag):
                continue
            seen_urls.add(full_url)

            # 默认召回列表中的全部正文链接，避免在爬虫层静默漏掉同义表达。
            # 个别全站列表可显式配置 title_keywords 缩小范围。
            if title_keywords and not any(kw in title for kw in title_keywords):
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

    def _domain_allowed(
        self, link_domain: str, base_domain: str, allowed_domains: list[str]
    ) -> bool:
        if link_domain == base_domain or link_domain.endswith("." + base_domain):
            return True
        return any(
            link_domain == domain or link_domain.endswith("." + domain)
            for domain in allowed_domains
        )

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
        """从a标签内或父元素中提取日期，支持panel_date结构和标准格式"""
        # panel_date结构: h2是日, p是年月
        panel = a_tag.find(class_="panel_date")
        if panel:
            nums = [x.get_text(strip=True) for x in panel.find_all(["h2","p"])]
            if len(nums) >= 2:
                day = nums[0].zfill(2)
                ym = nums[1].replace("/", "-")
                return ym + "-" + day
        # 标准日期格式
        text = a_tag.get_text()
        date_match = re.search(r'(\d{4}[-./年]\d{1,2}[-./月]\d{1,2})', text)
        if date_match:
            date_str = date_match.group(1)
            date_str = date_str.replace("年", "-").replace("月", "-").replace("日", "")
            date_str = date_str.replace(".", "-").replace("/", "-")
            return date_str
        parent = a_tag.parent
        if parent:
            text = parent.get_text()
            date_match = re.search(r'(\d{4}[-./年]\d{1,2}[-./月]\d{1,2})', text)
            if date_match:
                date_str = date_match.group(1)
                date_str = date_str.replace("年", "-").replace("月", "-").replace("日", "")
                date_str = date_str.replace(".", "-").replace("/", "-")
                return date_str
        return ""
