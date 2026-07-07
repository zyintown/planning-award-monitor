"""HTTP请求封装 - 含重试、超时、UA伪装"""

import time
import requests
from utils.logger import get_logger

logger = get_logger()


def fetch_html(
    url: str,
    timeout: int = 15,
    retry_times: int = 3,
    retry_delay: float = 2,
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    encoding: str = None,
) -> str | None:
    """请求页面，返回HTML文本。失败返回None。

    Args:
        url: 目标URL
        timeout: 超时秒数
        retry_times: 重试次数
        retry_delay: 重试间隔秒数
        user_agent: User-Agent字符串
        encoding: 强制编码（部分政府网站需要），None则自动检测

    Returns:
        HTML文本或None（全部失败时）
    """
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    for attempt in range(1, retry_times + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()

            if encoding:
                resp.encoding = encoding
            else:
                # 尝试从内容判断编码，fallback到utf-8
                if resp.encoding and resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding

            logger.debug(f"请求成功: {url} (第{attempt}次尝试)")
            return resp.text

        except requests.RequestException as e:
            logger.warning(
                f"请求失败: {url} (第{attempt}/{retry_times}次) - {type(e).__name__}: {e}"
            )
            if attempt < retry_times:
                time.sleep(retry_delay)

    logger.error(f"请求全部失败: {url}")
    return None
