"""中国风景园林学会 API 爬虫 - 逆向签名算法"""

import hashlib
import time
import json
import requests
from urllib.parse import urlparse, parse_qs
from crawlers.base import BaseCrawler, Article
from utils.logger import get_logger
from utils.http_client import fetch_html

logger = get_logger()

# AES解密得到的签名密钥
SIGN_SECRET = "KKFSn7jU"
# 固定参数
SITE_ID = 349
SH_ID = 684


def _gen_sign(params: dict, timestamp: int) -> str:
    """生成请求签名。

    算法：
    1. 参数按key排序，拼成 key=value&key=value
    2. 拼接 timestamp 和 secret
    3. MD5取小写hex
    """
    sorted_keys = sorted(params.keys())
    parts = []
    for k in sorted_keys:
        v = params[k]
        if v is not None and str(v) != "" and str(v).strip() != "":
            parts.append(f"{k}={v}")
    sign_str = "&".join(parts)
    full_str = sign_str + str(timestamp) + SIGN_SECRET
    return hashlib.md5(full_str.encode("utf-8")).hexdigest()


class ChslaApiCrawler(BaseCrawler):
    """中国风景园林学会 API 爬虫

    通过逆向 xiehuiyi.com 平台前端JS签名算法，
    直接调用JSON API获取文章列表。
    签名通过HTTP headers传递（sign, signTimeStamp）。
    """

    def _request_page(self, url: str = None) -> str | None:
        """重写请求方法，添加签名headers。"""
        if url is None:
            url = self.url

        # 从URL解析query参数
        parsed = urlparse(url)
        params = {}
        for k, v in parse_qs(parsed.query).items():
            params[k] = v[0] if len(v) == 1 else v

        # 确保必要参数
        params.setdefault("shId", SH_ID)
        params.setdefault("xhId", SH_ID)
        params.setdefault("siteId", SITE_ID)

        timestamp = int(time.time() * 1000)
        sign = _gen_sign(params, timestamp)

        crawler_config = self.config.get("crawler", {})
        ua = crawler_config.get("user_agent", "Mozilla/5.0")

        headers = {
            "User-Agent": ua,
            "Origin": "http://www.chsla.org.cn",
            "Referer": "http://www.chsla.org.cn/",
            "sign": sign,
            "signTimeStamp": str(timestamp),
        }

        # 构造不带query的URL
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        for attempt in range(1, crawler_config.get("retry_times", 3) + 1):
            try:
                resp = requests.get(base_url, params=params, headers=headers,
                                   timeout=crawler_config.get("timeout", 15))
                resp.raise_for_status()
                return resp.text
            except Exception as e:
                logger.warning(f"请求失败: {self.name} (第{attempt}次) - {e}")
                if attempt < crawler_config.get("retry_times", 3):
                    time.sleep(crawler_config.get("retry_delay", 2))

        return None

    def _parse(self, text: str) -> list[Article]:
        """解析JSON响应。"""
        articles = []
        try:
            data = json.loads(text)
            if data.get("errno") != 0:
                errmsg = data.get("errmsg", "unknown")
                raise RuntimeError(f"API error: {self.name} - {errmsg}")

            item_list = data.get("data", {}).get("list", [])
            for item in item_list:
                title = item.get("title", "").strip()
                if not title:
                    continue

                article_id = item.get("id", "")
                # 构造文章URL
                url = f"http://www.chsla.org.cn/article/{article_id}"

                publish_date = item.get("publishTime", "")[:10] if item.get("publishTime") else ""

                articles.append(Article(
                    title=title,
                    url=url,
                    source=self.name,
                    publish_date=publish_date,
                ))

        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败: {self.name} - {e}")
            raise

        return articles
