"""P3 标题门禁：在 AI 前剔除可由标题确定的非目标信息。"""

import re
from datetime import date

from crawlers.base import Article


TARGET_SOURCES = (
    "城市规划学会", "城市规划协会", "国土空间规划协会", "自然资源学会",
    "华夏建设科学技术奖励委员会", "风景园林学会",
)
INDUSTRY_TERMS = (
    "规划", "国土", "自然资源", "园林", "建设", "住建", "建筑", "城市设计",
    "测绘", "景观", "人居", "遗产",
)
OPPORTUNITY_TERMS = ("申报", "提名", "推荐", "参评", "报名", "征集", "启动", "延期")
AWARD_TERMS = ("奖", "竞赛", "大赛", "评优")
RESULT_TERMS = ("获奖", "授奖", "名单", "公示", "评审结果", "评选结果", "喜报", "荣获", "拟奖", "公布", "网络人气", "网络投票", "投票")
YEAR_PATTERN = re.compile(r"(?<!\d)(20\d{2})(?:年度|年)?")


class TitleGate:
    """仅拒绝标题已能确定为无关的记录；边界不明时仍交 AI 判断。"""

    def check(self, article: Article, evaluation_date: date | None = None) -> tuple[bool, str]:
        title = article.title or ""
        source = article.source or ""
        if any(term in title for term in RESULT_TERMS):
            return False, "P3标题门禁：获奖结果、名单或公示不推送"
        source_related = any(term in source for term in TARGET_SOURCES)
        has_competition = any(term in title for term in ("竞赛", "大赛"))
        if not any(term in title for term in OPPORTUNITY_TERMS) and not (source_related and has_competition):
            return False, "P3标题门禁：未体现申报、推荐、报名或征集动作"
        if not any(term in title for term in AWARD_TERMS):
            return False, "P3标题门禁：不是奖项或竞赛机会"

        event_date = evaluation_date or self._article_date(article)
        years = [int(value) for value in YEAR_PATTERN.findall(title)]
        if years and max(years) < event_date.year:
            return False, "P3标题门禁：奖项年度早于判断年度"

        title_related = any(term in title for term in INDUSTRY_TERMS)
        if not source_related and not title_related:
            return False, "P3标题门禁：非规划/建设行业奖项或竞赛"
        return True, "P3标题门禁：交由AI核验时效与申报条件"

    @staticmethod
    def _article_date(article: Article) -> date:
        value = (article.publish_date or "").replace("/", "-")
        try:
            return date.fromisoformat(value)
        except ValueError:
            return date.today()
