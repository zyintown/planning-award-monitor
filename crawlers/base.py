"""爬虫基类 - 模板方法模式，定义统一接口"""

from dataclasses import dataclass
from utils.logger import get_logger

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

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "publish_date": self.publish_date,
            "summary": self.summary,
            "raw_content": self.raw_content,
        }


class BaseCrawler:
    """所有爬虫的基类，子类需实现 _request_page 和 _parse"""

    def __init__(self, name: str, url: str, config: dict):
        self.name = name
        self.url = url
        self.config = config  # 完整config字典

    def fetch(self) -> list[Article]:
        """抓取页面，返回文章列表。失败返回空列表。"""
        logger.info(f"开始抓取: {self.name}")
        try:
            html = self._request_page()
            if not html:
                logger.warning(f"抓取失败（页面为空）: {self.name}")
                return []
            articles = self._parse(html)
            logger.info(f"抓取完成: {self.name} ({len(articles)}条)")
            return articles
        except Exception as e:
            logger.warning(f"抓取异常: {self.name} - {type(e).__name__}: {e}")
            return []

    def _request_page(self) -> str | None:
        """子类实现：请求页面，返回HTML文本"""
        raise NotImplementedError

    def _parse(self, html: str) -> list[Article]:
        """子类实现：解析HTML，返回Article列表"""
        raise NotImplementedError
