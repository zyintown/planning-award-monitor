"""AI二次确认 - 调用本地Ollama判断是否为报奖申报通知"""

import json
import re
import requests
from crawlers.base import Article
from utils.logger import get_logger

logger = get_logger()

# 判断Prompt
JUDGE_PROMPT = """你是一个报奖信息筛选助手。请判断以下信息是否为"报奖申报通知"。

判断标准：
- 是：信息内容是通知组织/单位开展某项奖项的申报、推荐或提名工作，且处于申报期内
- 否：信息是获奖公示、评审结果、会议通知、培训通知、招标采购等

信息标题：{title}
信息摘要：{summary}

请只回答JSON：{{"is_award_application": true/false, "reason": "简要理由"}}"""


class AIFilter:
    def __init__(
        self,
        api_url: str = "http://localhost:11434/api/chat",
        model: str = "qwen3.5:latest",
        max_summary_length: int = 500,
        timeout: int = 30,
        enabled: bool = True,
    ):
        self.api_url = api_url
        self.model = model
        self.max_summary_length = max_summary_length
        self.timeout = timeout
        self.enabled = enabled

    def judge(self, article: Article) -> tuple[bool, str]:
        """判断单篇文章是否为报奖申报通知。

        Args:
            article: 文章对象

        Returns:
            (is_award_application, reason) 元组
            AI不可用时返回 (True, "AI不可用，保守推送")
        """
        if not self.enabled:
            return True, "AI未启用，直接通过"

        summary = (article.summary or article.raw_content or "")[: self.max_summary_length]

        prompt = JUDGE_PROMPT.format(title=article.title, summary=summary)

        try:
            response = self._call_ollama(prompt)
            if response is None:
                # AI不可用，保守策略
                logger.warning(f"AI不可用，保守推送: {article.title}")
                return True, "AI不可用，保守推送"

            is_award, reason = self._parse_response(response)
            logger.debug(f"AI判断: {article.title} → {is_award} ({reason})")
            return is_award, reason

        except Exception as e:
            logger.warning(f"AI判断异常: {article.title} - {type(e).__name__}: {e}")
            return True, f"AI异常，保守推送: {e}"

    def batch_judge(self, articles: list[Article]) -> list[tuple[Article, bool, str]]:
        """批量判断，返回 [(article, is_award, reason), ...]"""
        results = []
        for article in articles:
            is_award, reason = self.judge(article)
            results.append((article, is_award, reason))

        confirmed = sum(1 for _, is_award, _ in results if is_award)
        logger.info(f"AI二次确认: {len(articles)}条 → {confirmed}条通过")
        return results

    def _call_ollama(self, prompt: str) -> str | None:
        """调用Ollama API"""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "temperature": 0.1,  # 低温度，提高判断一致性
                "num_predict": 4096,  # qwen3.5思考模式需要足够token（thinking+content）
            },
        }

        resp = requests.post(
            self.api_url,
            json=payload,
            timeout=self.timeout + 60,  # qwen思考模式需要更长时间
        )
        resp.raise_for_status()

        data = resp.json()
        # Ollama /api/chat 返回格式: {"message": {"content": "...", "thinking": "..."}}
        # qwen3.5思考模式：thinking字段是推理过程，content字段是最终回答
        msg = data.get("message", {})
        content = msg.get("content", "")

        # 如果content为空但done_reason是length，说明token不够
        if not content:
            done_reason = data.get("done_reason", "")
            if done_reason == "length":
                logger.warning(f"Ollama token不足(done_reason=length)，eval_count={data.get('eval_count')}")
            else:
                logger.warning("Ollama返回空内容")
            return None

        return content

    def _parse_response(self, content: str) -> tuple[bool, str]:
        """解析AI返回的JSON"""
        try:
            # qwen3.5可能输出 <think>...</think> 标签，需要去除
            content_clean = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

            # 尝试提取JSON部分
            json_match = re.search(r'\{[^}]+\}', content_clean)
            if json_match:
                data = json.loads(json_match.group())
                return bool(data.get("is_award_application", False)), data.get("reason", "")
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"AI返回解析失败: {content[:100]}... - {e}")

        # 解析失败，保守推送
        return True, "AI返回解析失败，保守推送"
