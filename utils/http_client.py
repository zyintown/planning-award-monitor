"""HTTP 请求封装：超时、有限重试、退避和最终 URL 记录。"""

import time
from dataclasses import dataclass

import requests

from utils.logger import get_logger


logger = get_logger()

_ssl_warning_shown = False


@dataclass(frozen=True)
class HttpFetchResult:
    text: str
    final_url: str
    status_code: int


def fetch_response(
    url: str,
    timeout: int = 15,
    retry_times: int = 3,
    retry_delay: float = 2,
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    encoding: str = None,
    verify_ssl: bool = True,
) -> HttpFetchResult | None:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    if not verify_ssl:
        global _ssl_warning_shown
        if not _ssl_warning_shown:
            try:
                from urllib3.exceptions import InsecureRequestWarning
                import warnings
                warnings.filterwarnings("ignore", category=InsecureRequestWarning)
            except ImportError:
                pass
            _ssl_warning_shown = True
            logger.info("已禁用 SSL 证书验证（用于证书过期或自签名网站）")

    for attempt in range(1, retry_times + 1):
        try:
            response = requests.get(
                url, headers=headers, timeout=timeout, verify=verify_ssl,
            )
            if response.status_code == 429 or 500 <= response.status_code < 600:
                response.raise_for_status()
            if 400 <= response.status_code < 500:
                response.raise_for_status()

            if encoding:
                response.encoding = encoding
            elif response.encoding and response.encoding.lower() == "iso-8859-1":
                response.encoding = response.apparent_encoding

            logger.debug(f"请求成功: {url} (第{attempt}次尝试)")
            return HttpFetchResult(response.text, response.url, response.status_code)
        except requests.exceptions.SSLError as exc:
            logger.warning(f"SSL验证失败，不重复请求: {url} - {exc}")
            break
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            retryable = status == 429 or (status is not None and 500 <= status < 600)
            logger.warning(
                f"请求失败: {url} (第{attempt}/{retry_times}次) - HTTP {status}"
            )
            if not retryable:
                break
            wait_seconds = retry_delay * (2 ** (attempt - 1))
            if exc.response is not None and exc.response.headers.get("Retry-After"):
                try:
                    wait_seconds = min(60.0, float(exc.response.headers["Retry-After"]))
                except ValueError:
                    pass
            if attempt < retry_times:
                time.sleep(wait_seconds)
        except (requests.Timeout, requests.ConnectionError) as exc:
            logger.warning(
                f"请求失败: {url} (第{attempt}/{retry_times}次) - {type(exc).__name__}: {exc}"
            )
            if attempt < retry_times:
                time.sleep(retry_delay * (2 ** (attempt - 1)))
        except requests.RequestException as exc:
            logger.warning(f"请求失败且不重试: {url} - {type(exc).__name__}: {exc}")
            break

    logger.error(f"请求全部失败: {url}")
    return None


def fetch_html(
    url: str,
    timeout: int = 15,
    retry_times: int = 3,
    retry_delay: float = 2,
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    encoding: str = None,
    verify_ssl: bool = True,
) -> str | None:
    result = fetch_response(
        url=url,
        timeout=timeout,
        retry_times=retry_times,
        retry_delay=retry_delay,
        user_agent=user_agent,
        encoding=encoding,
        verify_ssl=verify_ssl,
    )
    return result.text if result else None
