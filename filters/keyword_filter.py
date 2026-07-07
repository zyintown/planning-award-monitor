"""关键词初筛 - 正向关键词命中 + 排除关键词不命中"""

import re
from crawlers.base import Article
from utils.logger import get_logger

logger = get_logger()


class KeywordFilter:
    def __init__(self, keywords: list[str], exclude_keywords: list[str]):
        self.keywords = keywords
        self.exclude_keywords = exclude_keywords
        # 编译正则关键词（支持 "开展.*奖" 这类模式）
        self._keyword_patterns = [re.compile(kw, re.IGNORECASE) for kw in keywords]
        self._exclude_patterns = [
            re.compile(ek, re.IGNORECASE) for ek in exclude_keywords
        ]

    def filter(self, article: Article) -> bool:
        """判断文章是否通过关键词初筛。

        规则：
        1. 标题或摘要命中任意正向关键词 → 通过初筛候选
        2. 标题或摘要命中任意排除关键词 → 直接拒绝
        3. 两者都命中 → 排除词优先，拒绝

        Args:
            article: 文章对象

        Returns:
            True=通过初筛, False=未通过
        """
        text = f"{article.title} {article.summary}"

        # 先检查排除词
        for pattern in self._exclude_patterns:
            if pattern.search(text):
                logger.debug(f"排除词命中 [{pattern.pattern}]: {article.title}")
                return False

        # 再检查正向关键词
        for pattern in self._keyword_patterns:
            if pattern.search(text):
                logger.debug(f"关键词命中 [{pattern.pattern}]: {article.title}")
                return True

        return False

    def batch_filter(self, articles: list[Article]) -> list[Article]:
        """批量筛选，返回通过的文章列表"""
        passed = [a for a in articles if self.filter(a)]
        logger.info(f"关键词初筛: {len(articles)}条 → {len(passed)}条通过")
        return passed
