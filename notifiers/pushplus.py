"""PushPlus微信推送模块"""

import time
import requests
from crawlers.base import Article
from utils.logger import get_logger

logger = get_logger()

PUSHPLUS_URL = "https://www.pushplus.plus/send"


class PushPlusNotifier:
    def __init__(self, token: str, topic: str = "", retry_times: int = 3):
        self.token = token
        self.topic = topic
        self.retry_times = retry_times

    def notify(self, articles: list[Article]) -> bool:
        """推送文章列表到微信。

        Args:
            articles: 待推送的文章列表

        Returns:
            True=推送成功, False=推送失败
        """
        if not articles:
            logger.info("无新信息，跳过推送")
            return True

        content = self._build_content(articles)
        title = f"报奖信息监测 ({len(articles)}条新信息)"

        for attempt in range(1, self.retry_times + 1):
            try:
                payload = {
                    "token": self.token,
                    "title": title,
                    "content": content,
                    "template": "txt",
                }
                if self.topic:
                    payload["topic"] = self.topic

                resp = requests.post(PUSHPLUS_URL, json=payload, timeout=15)
                resp.raise_for_status()
                data = resp.json()

                if data.get("code") == 200:
                    logger.info(f"PushPlus推送成功 ({len(articles)}条)")
                    return True
                else:
                    logger.warning(
                        f"PushPlus推送失败: {data.get('msg', '未知错误')} (第{attempt}次)"
                    )
            except Exception as e:
                logger.warning(
                    f"PushPlus推送异常: {type(e).__name__}: {e} (第{attempt}次)"
                )

            if attempt < self.retry_times:
                time.sleep(2)

        logger.error("PushPlus推送全部失败")
        return False

    def notify_alert(self, message: str) -> bool:
        """推送告警消息"""
        for attempt in range(1, self.retry_times + 1):
            try:
                payload = {
                    "token": self.token,
                    "title": "报奖监测告警",
                    "content": message,
                    "template": "txt",
                }
                resp = requests.post(PUSHPLUS_URL, json=payload, timeout=15)
                data = resp.json()
                if data.get("code") == 200:
                    return True
            except Exception as e:
                logger.warning(f"告警推送异常 (第{attempt}次): {e}")
            if attempt < self.retry_times:
                time.sleep(2)
        return False

    def _build_content(self, articles: list[Article]) -> str:
        """构建推送消息内容"""
        parts = [f"📰 报奖信息监测 (本次发现 {len(articles)} 条)\n"]

        for article in articles:
            part = f"""━━━━━━━━━━━━━━━━
📌 {article.title}
来源：{article.source} | 日期：{article.publish_date or '未知'}
摘要：{article.summary[:200] if article.summary else '无摘要'}
🔗 {article.url}"""
            parts.append(part)

        parts.append("━━━━━━━━━━━━━━━━")
        return "\n\n".join(parts)
