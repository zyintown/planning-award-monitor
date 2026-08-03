"""文章标题与 URL 规范化工具。"""

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_QUERY_KEYS = {
    "from",
    "from_source",
    "spm",
}


def normalize_title(title: str) -> str:
    """生成用于去重比较的标题，保留正文语义但消除格式差异。"""
    normalized = unicodedata.normalize("NFKC", title or "").strip().lower()
    return re.sub(r"\s+", "", normalized)


def canonicalize_url(url: str) -> str:
    """移除 fragment 和常见追踪参数，并稳定 query 参数顺序。"""
    raw = (url or "").strip()
    if not raw:
        return ""

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    filtered_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower.startswith("utm_") or key_lower in TRACKING_QUERY_KEYS:
            continue
        filtered_query.append((key, value))
    filtered_query.sort(key=lambda pair: (pair[0], pair[1]))

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((scheme, netloc, path, urlencode(filtered_query), ""))


def make_dedup_key(title: str, url: str, source: str, publish_date: str = "") -> str:
    """生成审计用稳定指纹；实际窗口去重仍由数据库时间条件控制。"""
    value = "|".join(
        [
            (source or "").strip(),
            normalize_title(title),
            canonicalize_url(url),
            (publish_date or "").strip(),
        ]
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
