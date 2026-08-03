"""AI 二次确认：Agnes 主用、本地 Ollama fallback。"""

import json
import re
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

from crawlers.base import Article
from utils.logger import get_logger


logger = get_logger()

PROMPT_VERSION = "p3-v1"

JUDGE_PROMPT = """你是面向城市规划、国土空间规划、建筑、住建、风景园林和自然资源从业者的报奖信息筛选与结构化抽取助手。请判断以下信息是否为“仍可参与的行业奖项或竞赛申报通知”，并抽取可直接用于提醒的字段。

只有同时满足以下三项才返回 true：
1. 奖项或竞赛与城市规划、国土空间规划、建筑、住建、风景园林或自然资源行业直接相关；
2. 正在组织申报、提名、推荐、参评或报名，而不是结果发布；
3. 在判断日期仍可行动，且正文没有显示申报已经结束。

必须返回 false：
- 获奖结果、授奖公告、名单公示、评审结果、喜报、章程或系统入口；
- 网络人气评选、投票或已经进入评审阶段的活动；
- 非上述行业的基金、课题、项目指南、贷款、专家库、人才计划、标准、论文征集、会议培训、招聘、招标采购；
- 物理、化学、农业、档案、教育等无规划/建设行业关联的奖项，即使标题含“申报”或“推荐”；
- 截止日期早于判断日期，或标题中的奖项年度明显早于判断年份；
- 标题或正文不足以同时确认行业相关性、申报动作和时效。

应返回 true 的典型情形：风景园林奖项提名、城市规划设计竞赛报名、园林职业技能竞赛延期报名、行业协会转发的建设类科技奖推荐通知。

- award_name：奖项正式名称；无法确认时返回 null。
- deadline_text：正文中的截止日期原文；没有时返回 null。
- deadline_date：只有能确定完整年月日时才返回 YYYY-MM-DD，否则返回 null。
- applicant_scope：申报主体或对象数组；没有时返回空数组，数组中不得包含 null。

判断日期：{current_date}
发布日期：{publish_date}
标题：{title}
正文摘要：{summary}

只返回 JSON，不要输出 Markdown 或其他内容：
{{"is_award_application": true, "reason": "仍在申报期", "award_name": "某某奖", "deadline_text": "2026年8月1日前", "deadline_date": "2026-08-01", "applicant_scope": ["规划设计单位"]}} /no_think"""


@dataclass(frozen=True)
class AIJudgeResult:
    """decision=None 表示 AI 暂不可用或响应无效，应留在待重试队列。"""

    decision: bool | None
    reason: str
    raw_response: str = ""
    error_kind: str = ""
    award_name: str = ""
    deadline_text: str = ""
    deadline_date: str = ""
    applicant_scope: tuple[str, ...] = ()


class AIFilter:
    def __init__(
        self,
        api_url: str = "http://localhost:11434/api/chat",
        model: str = "qwen3.5:latest",
        max_summary_length: int = 500,
        timeout: int = 30,
        enabled: bool = True,
        max_workers: int = 1,
        provider: str = "ollama",
        fallback_provider: str = "",
        fallback_api_url: str = "",
        fallback_model: str = "",
        api_key_file: str = "",
    ):
        self.api_url = api_url
        self.model = model
        self.max_summary_length = max_summary_length
        self.timeout = timeout
        self.enabled = enabled
        self.max_workers = max(1, int(max_workers))
        self.provider = provider.lower().strip()
        self.fallback_provider = fallback_provider.lower().strip()
        needs_agnes_key = self.provider == "agnes" or self.fallback_provider == "agnes"
        self.api_keys = self._load_api_keys(api_key_file) if needs_agnes_key else []
        self.api_key = self.api_keys[0] if self.api_keys else ""
        self._api_key_index = 0
        self._api_key_lock = threading.Lock()
        self.fallback_api_url = fallback_api_url
        self.fallback_model = fallback_model
        self.prompt_version = PROMPT_VERSION

    def judge(self, article: Article, judgment_date: date | None = None) -> AIJudgeResult:
        if not self.enabled:
            return AIJudgeResult(True, "AI未启用，按配置直接通过")

        # 详情正文比列表页摘要更可靠；列表摘要可能只是导航或截断片段。
        summary = (article.raw_content or article.summary or "")[: self.max_summary_length]
        prompt = JUDGE_PROMPT.format(
            current_date=(judgment_date or date.today()).isoformat(),
            publish_date=article.publish_date or "未知",
            title=article.title,
            summary=summary,
        )

        try:
            response = self._call_model(prompt)
            if response is None:
                logger.warning(f"AI不可用，保留待重试: {article.title}")
                return AIJudgeResult(
                    None,
                    "AI返回空内容，等待下次重试",
                    error_kind="invalid_response",
                )

            result = self._parse_response(response)
            logger.debug(
                f"AI判断: {article.title} → {result.decision} ({result.reason})"
            )
            return result
        except Exception as exc:
            logger.warning(f"AI判断异常，保留待重试: {article.title} - {type(exc).__name__}: {exc}")
            return AIJudgeResult(
                None,
                f"AI异常，等待重试: {type(exc).__name__}: {exc}",
                error_kind="transport",
            )

    def batch_judge(self, articles: list[Article]) -> list[tuple[Article, AIJudgeResult]]:
        if self.max_workers == 1 or len(articles) <= 1:
            return self._batch_judge_sequential(articles)
        return self._batch_judge_concurrent(articles)

    def _batch_judge_sequential(
        self, articles: list[Article]
    ) -> list[tuple[Article, AIJudgeResult]]:
        results = []
        circuit_reason = ""
        for article in articles:
            if circuit_reason:
                result = AIJudgeResult(
                    None,
                    circuit_reason,
                    error_kind="transport",
                )
            else:
                result = self.judge(article)
                if result.error_kind == "transport":
                    circuit_reason = "AI服务本轮不可用，已停止后续调用，等待下次重试"
            results.append((article, result))
        self._log_batch_summary(results)
        return results

    def _batch_judge_concurrent(
        self, articles: list[Article]
    ) -> list[tuple[Article, AIJudgeResult]]:
        """有限在途并发；传输失败后不再提交新请求，并保持输入顺序。"""
        ordered: list[AIJudgeResult | None] = [None] * len(articles)
        next_index = 0
        circuit_reason = ""
        futures: dict[Future, int] = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while next_index < len(articles) and len(futures) < self.max_workers:
                futures[executor.submit(self.judge, articles[next_index])] = next_index
                next_index += 1

            while futures:
                completed, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in completed:
                    index = futures.pop(future)
                    result = future.result()
                    ordered[index] = result
                    if result.error_kind == "transport" and not circuit_reason:
                        circuit_reason = "AI服务本轮不可用，已停止后续调用，等待下次重试"

                if not circuit_reason:
                    while next_index < len(articles) and len(futures) < self.max_workers:
                        futures[executor.submit(self.judge, articles[next_index])] = next_index
                        next_index += 1

        if circuit_reason:
            for index in range(next_index, len(articles)):
                ordered[index] = AIJudgeResult(
                    None,
                    circuit_reason,
                    error_kind="transport",
                )

        results = [(article, ordered[index]) for index, article in enumerate(articles)]
        self._log_batch_summary(results)
        return results

    def _log_batch_summary(
        self, results: list[tuple[Article, AIJudgeResult]]
    ):
        confirmed = sum(1 for _, result in results if result.decision is True)
        pending = sum(1 for _, result in results if result.decision is None)
        logger.info(
            f"AI二次确认: {len(results)}条 → {confirmed}条通过，{pending}条待重试"
        )

    def _call_model(self, prompt: str) -> str | None:
        """调用主模型；主模型不可用时回退到备用模型。"""
        try:
            if self.provider == "agnes":
                api_key = self._next_api_key()
                if not api_key:
                    raise RuntimeError("根目录 local.env 未配置 Agnes Key")
                content = self._call_openai_compatible(
                    prompt, self.api_url, self.model, api_key
                )
            elif self.provider == "ollama":
                content = self._call_ollama(prompt)
            else:
                raise ValueError(f"未知 AI provider: {self.provider}")
            if content:
                return content
            raise RuntimeError(f"{self.provider} 返回空内容")
        except Exception as primary_error:
            if not self.fallback_provider:
                raise
            logger.warning(
                f"AI主模型不可用，回退到 {self.fallback_provider}: "
                f"{type(primary_error).__name__}: {primary_error}"
            )
            try:
                if self.fallback_provider == "ollama":
                    content = self._call_ollama_at(
                        self.fallback_api_url, self.fallback_model, prompt
                    )
                elif self.fallback_provider == "agnes":
                    api_key = self._next_api_key()
                    if not api_key:
                        raise RuntimeError("根目录 local.env 未配置 Agnes Key")
                    content = self._call_openai_compatible(
                        prompt,
                        self.fallback_api_url,
                        self.fallback_model,
                        api_key,
                    )
                else:
                    raise ValueError(f"未知 fallback provider: {self.fallback_provider}")
                if content:
                    return content
                raise RuntimeError(f"{self.fallback_provider} 返回空内容")
            except Exception as fallback_error:
                raise RuntimeError(
                    f"主模型和 fallback 均不可用: "
                    f"{type(fallback_error).__name__}: {fallback_error}"
                ) from fallback_error

    @property
    def api_key_count(self) -> int:
        return len(self.api_keys)

    def _load_api_keys(self, api_key_file: str) -> list[str]:
        """仅从项目根目录 local.env 指定文件加载 Key，去重且不记录值。"""
        keys: list[str] = []

        def add_key(value: str):
            value = value.strip().strip('"\'')
            if value and value not in keys:
                keys.append(value)

        if api_key_file:
            path = Path(api_key_file)
            try:
                if path.exists():
                    for line in path.read_text(encoding="utf-8").splitlines():
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#") or "=" not in stripped:
                            continue
                        _, value = stripped.split("=", 1)
                        add_key(value)
            except OSError as exc:
                logger.warning(f"Agnes key 文件无法读取，继续使用其他 Key: {type(exc).__name__}")
        return keys

    def _next_api_key(self) -> str:
        if not self.api_keys:
            return ""
        with self._api_key_lock:
            key = self.api_keys[self._api_key_index]
            self._api_key_index = (self._api_key_index + 1) % len(self.api_keys)
        return key

    def _call_ollama(self, prompt: str) -> str | None:
        return self._call_ollama_at(self.api_url, self.model, prompt)

    def _call_ollama_at(
        self, api_url: str, model: str, prompt: str
    ) -> str | None:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_predict": 512},
        }
        response = requests.post(
            api_url,
            json=payload,
            timeout=self.timeout + 30,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("message", {}).get("content", "")
        if not content:
            logger.warning(
                "Ollama返回空内容"
                + (
                    f" (done_reason={data.get('done_reason')}, eval_count={data.get('eval_count')})"
                    if data.get("done_reason")
                    else ""
                )
            )
            return None
        return content

    def _call_openai_compatible(
        self, prompt: str, api_url: str, model: str, api_key: str
    ) -> str | None:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 512,
            "stream": False,
        }
        response = requests.post(
            api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout + 30,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        content = (
            choices[0].get("message", {}).get("content", "")
            if choices and isinstance(choices[0], dict)
            else ""
        )
        if not content:
            logger.warning("OpenAI兼容 API 返回空内容")
            return None
        return content

    def _parse_response(self, content: str) -> AIJudgeResult:
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        start = cleaned.find("{")
        if start < 0:
            return AIJudgeResult(
                None,
                "AI返回不是JSON，等待重试",
                content,
                "invalid_response",
            )

        try:
            data, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(f"AI返回解析失败: {content[:100]}... - {exc}")
            return AIJudgeResult(
                None,
                "AI返回JSON无效，等待重试",
                content,
                "invalid_response",
            )

        decision = data.get("is_award_application")
        if type(decision) is not bool:
            return AIJudgeResult(
                None,
                "is_award_application 必须是JSON布尔值",
                content,
                "invalid_response",
            )

        reason = data.get("reason", "")
        if not isinstance(reason, str):
            reason = str(reason)

        optional_strings = {}
        for field_name in ("award_name", "deadline_text", "deadline_date"):
            value = data.get(field_name)
            if value is None:
                optional_strings[field_name] = ""
            elif isinstance(value, str):
                optional_strings[field_name] = value.strip()[:500]
            else:
                return AIJudgeResult(
                    None,
                    f"{field_name} 必须是字符串或null",
                    content,
                    "invalid_response",
                )

        deadline_text = optional_strings["deadline_text"]
        deadline_date = optional_strings["deadline_date"]
        if deadline_text and not deadline_date:
            deadline_date = self._infer_deadline_date(deadline_text)
        if deadline_date:
            try:
                date.fromisoformat(deadline_date)
            except ValueError:
                return AIJudgeResult(
                    None,
                    "deadline_date 必须是有效的YYYY-MM-DD日期",
                    content,
                    "invalid_response",
                )

        scope = data.get("applicant_scope", [])
        if scope is None:
            scope = []
        if not isinstance(scope, list) or any(not isinstance(item, str) for item in scope):
            return AIJudgeResult(
                None,
                "applicant_scope 必须是字符串数组",
                content,
                "invalid_response",
            )
        applicant_scope = tuple(
            item.strip()[:200] for item in scope if item.strip()
        )[:20]

        return AIJudgeResult(
            decision,
            reason[:500],
            content,
            award_name=optional_strings["award_name"],
            deadline_text=optional_strings["deadline_text"],
            deadline_date=deadline_date,
            applicant_scope=applicant_scope,
        )

    @staticmethod
    def _infer_deadline_date(text: str, reference_date: date | None = None) -> str:
        """从 AI 已抽取的截止原文推导日期范围末日。"""
        reference_year = (reference_date or date.today()).year
        full_dates = re.findall(
            r"((?:19|20)\d{2})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*日?",
            text or "",
        )
        month_days = re.findall(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日?", text or "")
        candidates = [
            (int(year), int(month), int(day))
            for year, month, day in full_dates
        ]
        inferred_year = candidates[-1][0] if candidates else reference_year
        candidates.extend(
            (inferred_year, int(month), int(day))
            for month, day in month_days
        )
        for year, month, day in reversed(candidates):
            try:
                return datetime(year, month, day).date().isoformat()
            except ValueError:
                continue
        return ""
