"""事件级归并与来源优先级规则。

这些规则只使用现有文章字段和配置，不新增数据库表或字段。
"""

import re
from datetime import datetime, timedelta

from crawlers.base import Article
from utils.normalization import normalize_title


SOURCE_TIER_PRIORITY = {
    "official_website": 400,
    "official_wechat": 300,
    "authoritative_secondary": 200,
    "aggregator": 100,
}

_PHASES = (
    ("application", ("申报", "推荐", "提名", "报名", "征集")),
    ("selection", ("评选", "评审")),
    ("result", ("公示", "名单", "获奖", "表彰")),
)


def _compact(value: str) -> str:
    value = normalize_title(value or "")
    return re.sub(r"[，。！？：；、（）()【】\[\]‘’“”\"'…·,.;:!?\s]+", "", value)


def _award_identity(value: str) -> str:
    """奖项名单独归一化；年度由事件键的 year 段表达。"""
    compact = _compact(value)
    return re.sub(r"(?:19|20)\d{2}(?:年度|年)?", "", compact)


def _year(article: Article) -> str:
    text = " ".join((article.award_name, article.title, article.publish_date))
    match = re.search(r"(?:19|20)\d{2}", text)
    return match.group(0) if match else "unknown"


def _phase(article: Article) -> str:
    text = f"{article.title}{article.summary}"
    for phase, keywords in _PHASES:
        if any(keyword in text for keyword in keywords):
            return phase
    return "application"


def jurisdiction_for_article(article: Article) -> str:
    """从标题和来源提取粗粒度行政辖区；无法判断时按全国处理。"""
    # 摘要可能包含聚合站转载的其他地区信息，不能作为事件辖区依据。
    text = f"{article.title}{article.source}"
    patterns = (
        r"(?:北京|上海|天津|重庆)市",
        r"(?:内蒙古|广西|西藏|宁夏|新疆)自治[区州]",
        r"(?:香港|澳门)特别行政区",
        r"[黑吉辽蒙冀晋鲁豫苏皖浙闽赣鄂湘粤桂琼川贵云藏陕甘青宁新]省",
        r"(?:北京|上海|天津|重庆|广州|深圳|成都|杭州|南京|武汉|西安|厦门|福州|昆明|合肥|南昌|南宁|贵阳|海口|兰州|银川|乌鲁木齐|拉萨|呼和浩特)市",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return "全国"


def event_key(article: Article) -> str:
    """生成跨来源事件键。

    AI 已抽取奖项名时优先使用奖项名；否则使用标题作为保守回退，
    避免在事实不足时将不相关通知强行合并。
    """
    award = _award_identity(article.award_name) or _award_identity(article.title)
    return "|".join((award, _year(article), _phase(article), jurisdiction_for_article(article)))


def source_tier(source: str, config: dict | None = None) -> str:
    config = config or {}
    sources = config.get("sources", {})

    if source.startswith("微信公众号:"):
        account_name = source.split(":", 1)[1]
        for account in sources.get("wechat_accounts", []):
            if account.get("name") == account_name:
                return account.get("source_tier", "official_wechat")
        return "official_wechat"

    for site in sources.get("websites", []):
        if site.get("name") == source:
            return site.get("source_tier", "official_website")

    if "信息库" in source or "聚合" in source:
        return "aggregator"
    return "official_website"


def source_priority(source: str, config: dict | None = None) -> int:
    return SOURCE_TIER_PRIORITY.get(source_tier(source, config), 0)


def source_is_aggregator(source: str, config: dict | None = None) -> bool:
    return source_tier(source, config) == "aggregator"


def completeness_score(article: Article) -> int:
    """同等级来源的确定性排序：结构化字段优先，其次正文完整度。"""
    return (
        (100 if article.award_name else 0)
        + (50 if article.deadline_date else 0)
        + (25 if article.deadline_text else 0)
        + (25 if article.applicant_scope else 0)
        + min(len(article.raw_content or article.summary), 5000) // 100
    )


def aggregator_due(article: Article, now: datetime | None = None, delay_hours: int = 24) -> bool:
    """按首次入库时间判断聚合站兜底窗口是否到期。"""
    if not article.created_at:
        return True
    try:
        created_at = datetime.fromisoformat(article.created_at)
    except ValueError:
        return True
    reference = now or datetime.now()
    return reference >= created_at + timedelta(hours=max(1, delay_hours))
