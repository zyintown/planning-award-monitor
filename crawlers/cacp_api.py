"""中国城市规划协会 JSONP API 爬虫"""

import re
import json
from crawlers.base import BaseCrawler, Article
from utils.logger import get_logger

logger = get_logger()


class CacpApiCrawler(BaseCrawler):
    """中国城市规划协会 API 爬虫

    通过 cacp.org.cn 的 JSONP 数据接口获取文章列表。
    接口返回格式: jsonpCallback({"success":{"list":[...]}})
    每页返回7条，默认抓取10页。
    """

    def _parse(self, text: str) -> list[Article]:
        """解析 JSONP 响应，提取文章列表。"""
        articles = []

        try:
            # 去除 JSONP 回调包装: jsonpCallback({...}) -> {...}
            json_match = re.search(r"\((.*)\)", text, re.DOTALL)
            if not json_match:
                raise ValueError(f"JSONP 回调格式未匹配: {self.name}")

            json_str = json_match.group(1).replace("'", '"')
            data = json.loads(json_str, strict=False)
            item_list = data.get("success", {}).get("list", [])

            for item in item_list:
                title = item.get("title", "")
                url = item.get("url", "")
                if not title or not url:
                    continue

                # 清理 URL（去除 :80 端口）
                url = url.replace(":80/", "/")

                publish_date = item.get("releaseDate", "")
                summary = item.get("description", "")

                articles.append(Article(
                    title=title,
                    url=url,
                    source=self.name,
                    publish_date=publish_date,
                    summary=summary,
                ))

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {self.name} - {e}")
            raise
        except Exception as e:
            logger.warning(f"解析异常: {self.name} - {type(e).__name__}: {e}")
            raise

        return articles
