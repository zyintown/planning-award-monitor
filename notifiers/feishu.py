"""飞书 Webhook 推送模块

通过飞书自定义机器人 Webhook 推送消息到群聊。
完全免费、无需实名认证、无次数限制。

接入方式：
1. 在飞书群聊中点击「设置」→「群机器人」→「添加机器人」→「自定义机器人」
2. 复制 Webhook 地址填入 config.yaml
3. （可选）开启「加签」安全设置，将密钥填入 secret 字段
"""

import time
import hashlib
import hmac
import base64
from dataclasses import dataclass
import requests
from crawlers.base import Article
from utils.logger import get_logger

logger = get_logger()


@dataclass
class NotificationBatchResult:
    """飞书单个批次结果，用于只重试失败批次中的文章。"""

    articles: list[Article]
    success: bool
    error: str = ""


class FeishuNotifier:
    # 飞书单条消息上限约30KB，每条文章约500字，安全上限设为10条/批
    BATCH_SIZE = 10

    def __init__(self, webhook_url: str, secret: str = "", retry_times: int = 3):
        self.webhook_url = webhook_url
        self.secret = secret
        self.retry_times = retry_times
        self.last_error = ""

    def notify(self, articles: list[Article]) -> bool:
        """推送文章列表到飞书群。文章过多时自动分批发送。

        Args:
            articles: 待推送的文章列表

        Returns:
            True=全部推送成功, False=至少一批失败
        """
        return all(result.success for result in self.notify_batches(articles))

    def notify_batches(self, articles: list[Article]) -> list[NotificationBatchResult]:
        """分批推送并返回每批涉及的文章，支持精确落库和失败重试。"""
        if not articles:
            logger.info("无新信息，跳过推送")
            return []
        if not self.webhook_url:
            error = "飞书 webhook_url 未配置"
            logger.error(error)
            return [NotificationBatchResult(list(articles), False, error)]

        results = []
        total = len(articles)
        for i in range(0, total, self.BATCH_SIZE):
            batch = articles[i:i + self.BATCH_SIZE]
            batch_num = i // self.BATCH_SIZE + 1
            total_batches = (total + self.BATCH_SIZE - 1) // self.BATCH_SIZE

            content = self._build_content(batch)
            suffix = f" ({batch_num}/{total_batches})" if total_batches > 1 else ""
            title = f"报奖信息监测 ({len(batch)}条新信息){suffix}"

            payload = {
                "msg_type": "post",
                "content": {
                    "post": {
                        "zh_cn": {
                            "title": title,
                            "content": content,
                        }
                    }
                },
            }

            success = self._send(payload, f"推送成功 ({len(batch)}条)")
            results.append(
                NotificationBatchResult(
                    articles=list(batch),
                    success=success,
                    error="" if success else self.last_error or "飞书推送失败",
                )
            )

            # 批次间间隔1秒，避免频率限制
            if i + self.BATCH_SIZE < total:
                time.sleep(1)

        return results

    def notify_alert(self, message: str) -> bool:
        """推送告警消息"""
        if not self.webhook_url:
            logger.error("飞书 webhook_url 未配置，跳过告警推送")
            return False
        payload = {
            "msg_type": "text",
            "content": {"text": f"⚠️ 报奖监测告警\n{message}"},
        }
        return self._send(payload, "告警推送成功")

    def _send(self, payload: dict, success_msg: str) -> bool:
        """发送消息到飞书 Webhook（带重试）"""
        self.last_error = ""
        # 加签
        if self.secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = self._gen_sign(timestamp)

        for attempt in range(1, self.retry_times + 1):
            try:
                resp = requests.post(self.webhook_url, json=payload, timeout=15)
                resp.raise_for_status()
                data = resp.json()

                if data.get("StatusCode") == 0 or data.get("code") == 0:
                    logger.info(f"飞书{success_msg}")
                    return True
                else:
                    msg = data.get("msg", data.get("StatusMessage", "未知错误"))
                    self.last_error = str(msg)
                    logger.warning(f"飞书推送失败: {msg} (第{attempt}次)")
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    f"飞书推送异常: {type(e).__name__}: {e} (第{attempt}次)"
                )

            if attempt < self.retry_times:
                time.sleep(2)

        logger.error("飞书推送全部失败")
        return False

    def _gen_sign(self, timestamp: str) -> str:
        """生成飞书加签"""
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    def _build_content(self, articles: list[Article]) -> list[list[dict]]:
        """构建飞书富文本消息内容。

        返回格式为 [[{element}, ...], ...]，每个内层列表代表一行。
        """
        lines: list[list[dict]] = [
            [{"tag": "text", "text": f"📰 报奖信息监测 (本次发现 {len(articles)} 条)\n"}],
        ]

        for article in articles:
            # 标题
            lines.append([{"tag": "text", "text": f"📌 {article.title}\n"}])
            # 来源与日期
            date_str = article.publish_date or "未知"
            lines.append(
                [{"tag": "text", "text": f"来源：{article.source} | 日期：{date_str}\n"}]
            )
            # 摘要
            summary = article.summary[:200] if article.summary else "无摘要"
            lines.append([{"tag": "text", "text": f"摘要：{summary}\n"}])
            if article.award_name:
                lines.append(
                    [{"tag": "text", "text": f"奖项：{article.award_name}\n"}]
                )
            deadline = article.deadline_date or article.deadline_text
            if deadline:
                lines.append(
                    [{"tag": "text", "text": f"截止：{deadline}\n"}]
                )
            if article.applicant_scope:
                lines.append(
                    [
                        {
                            "tag": "text",
                            "text": f"申报对象：{'、'.join(article.applicant_scope)}\n",
                        }
                    ]
                )
            # 原文链接
            lines.append(
                [
                    {"tag": "text", "text": "🔗 "},
                    {"tag": "a", "text": "查看原文", "href": article.url},
                    {"tag": "text", "text": "\n"},
                ]
            )
            # 分隔线
            lines.append([{"tag": "text", "text": "━━━━━━━━━━━━━━━━"}])

        return lines
