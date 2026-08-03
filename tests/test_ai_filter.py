import unittest
import time
import tempfile
from unittest.mock import Mock, patch
from pathlib import Path

import requests

from crawlers.base import Article
from filters.ai_filter import AIFilter, AIJudgeResult
from storage.benchmark_ai import choose_recommended_workers


class AIFilterTests(unittest.TestCase):
    def setUp(self):
        self.filter = AIFilter()

    def test_valid_false_is_rejected(self):
        result = self.filter._parse_response(
            '{"is_award_application": false, "reason": "这是获奖公示"}'
        )
        self.assertIs(result.decision, False)

    def test_string_false_is_invalid_instead_of_truthy(self):
        result = self.filter._parse_response(
            '{"is_award_application": "false", "reason": "类型错误"}'
        )
        self.assertIsNone(result.decision)
        self.assertIn("布尔", result.reason)

    def test_unparseable_response_stays_pending(self):
        result = self.filter._parse_response("不是JSON")
        self.assertIsNone(result.decision)

    def test_structured_fields_are_strictly_parsed(self):
        result = self.filter._parse_response(
            '{"is_award_application": true, "reason": "仍可申报", '
            '"award_name": "城市规划奖", "deadline_text": "8月1日前", '
            '"deadline_date": "2026-08-01", "applicant_scope": ["设计单位"]}'
        )
        self.assertIs(result.decision, True)
        self.assertEqual(result.award_name, "城市规划奖")
        self.assertEqual(result.deadline_date, "2026-08-01")
        self.assertEqual(result.applicant_scope, ("设计单位",))

    def test_judge_prefers_detail_content_over_list_summary(self):
        article = Article(
            "奖项申报通知",
            "https://example.com/detail",
            "测试源",
            summary="列表页导航摘要",
            raw_content="详情正文包含申报对象和截止日期",
        )
        with patch.object(
            self.filter,
            "_call_ollama",
            return_value='{"is_award_application": false, "reason": "测试"}',
        ) as call:
            self.filter.judge(article)

        prompt = call.call_args.args[0]
        self.assertIn("详情正文包含申报对象和截止日期", prompt)
        self.assertNotIn("列表页导航摘要", prompt)

    def test_agnes_uses_openai_compatible_request_and_response(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "local.env"
            key_file.write_text("key1=test-key\n", encoding="utf-8")
            ai_filter = AIFilter(
                provider="agnes",
                api_url="https://api.agnes-ai.cn/v1/chat/completions",
                model="agnes-2.5-flash",
                api_key_file=str(key_file),
            )
            response = Mock()
            response.json.return_value = {
                "choices": [
                    {"message": {"role": "assistant", "content": '{"is_award_application": false}'}}
                ]
            }
            with patch("filters.ai_filter.requests.post", return_value=response) as post:
                content = ai_filter._call_model("测试提示")

        self.assertIn("is_award_application", content)
        self.assertEqual(post.call_args.args[0], "https://api.agnes-ai.cn/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "agnes-2.5-flash")
        self.assertNotIn("options", post.call_args.kwargs["json"])

    def test_missing_agnes_key_falls_back_to_ollama(self):
        with tempfile.TemporaryDirectory() as directory:
            ai_filter = AIFilter(
                provider="agnes",
                api_url="https://api.agnes-ai.cn/v1/chat/completions",
                model="agnes-2.5-flash",
                api_key_file=str(Path(directory) / "missing-local.env"),
                fallback_provider="ollama",
                fallback_api_url="http://localhost:11434/api/chat",
                fallback_model="qwen3.5:latest",
            )
            with patch.object(
                ai_filter,
                "_call_ollama_at",
                return_value='{"is_award_application": false}',
            ) as fallback:
                content = ai_filter._call_model("测试提示")

        self.assertIn("is_award_application", content)
        fallback.assert_called_once_with(
            "http://localhost:11434/api/chat", "qwen3.5:latest", "测试提示"
        )

    def test_invalid_structured_date_stays_pending(self):
        result = self.filter._parse_response(
            '{"is_award_application": true, "reason": "仍可申报", '
            '"deadline_date": "2026-02-30", "applicant_scope": []}'
        )
        self.assertIsNone(result.decision)
        self.assertEqual(result.error_kind, "invalid_response")

    def test_missing_deadline_date_is_inferred_from_deadline_text_range(self):
        result = self.filter._parse_response(
            '{"is_award_application": true, "reason": "仍在申报期", '
            '"deadline_text": "2026年8月24日9:00开通，9月23日17:00关闭", '
            '"deadline_date": null, "applicant_scope": []}'
        )
        self.assertIs(result.decision, True)
        self.assertEqual(result.deadline_date, "2026-09-23")

    def test_transport_failure_opens_batch_circuit_breaker(self):
        articles = [
            Article(f"申报通知{i}", f"https://example.com/{i}", "测试源")
            for i in range(3)
        ]
        with patch.object(
            self.filter,
            "_call_ollama",
            side_effect=requests.ConnectionError("offline"),
        ) as call:
            results = self.filter.batch_judge(articles)

        self.assertEqual(call.call_count, 1)
        self.assertTrue(all(result.decision is None for _, result in results))
        self.assertTrue(all(result.error_kind == "transport" for _, result in results))

    def test_concurrent_batch_preserves_input_order(self):
        ai_filter = AIFilter(max_workers=3)
        articles = [
            Article(f"申报通知{i}", f"https://example.com/{i}", "测试源")
            for i in range(5)
        ]

        def judge(article):
            index = int(article.url.rsplit("/", 1)[-1])
            time.sleep((5 - index) * 0.002)
            return AIJudgeResult(True, str(index))

        with patch.object(ai_filter, "judge", side_effect=judge):
            results = ai_filter.batch_judge(articles)

        self.assertEqual([article.title for article, _ in results], [a.title for a in articles])
        self.assertEqual([result.reason for _, result in results], [str(i) for i in range(5)])

    def test_concurrent_transport_failure_stops_new_submissions(self):
        ai_filter = AIFilter(max_workers=2)
        articles = [
            Article(f"申报通知{i}", f"https://example.com/{i}", "测试源")
            for i in range(5)
        ]

        def judge(article):
            index = int(article.url.rsplit("/", 1)[-1])
            if index == 0:
                return AIJudgeResult(None, "offline", error_kind="transport")
            time.sleep(0.02)
            return AIJudgeResult(True, str(index))

        with patch.object(ai_filter, "judge", side_effect=judge) as call:
            results = ai_filter.batch_judge(articles)

        self.assertEqual(call.call_count, 2)
        self.assertEqual([article.title for article, _ in results], [a.title for a in articles])
        self.assertTrue(all(result is not None for _, result in results))
        self.assertTrue(all(result.error_kind == "transport" for _, result in results[2:]))

    def test_benchmark_recommends_only_consistent_error_free_speedup(self):
        results = [
            {"workers": 1, "throughput": 1.0, "speedup": 1.0, "errors": 0, "invalid": 0, "decisions": [True, False]},
            {"workers": 2, "throughput": 1.5, "speedup": 1.5, "errors": 0, "invalid": 0, "decisions": [True, False]},
            {"workers": 4, "throughput": 2.0, "speedup": 2.0, "errors": 0, "invalid": 0, "decisions": [False, False]},
        ]
        self.assertEqual(choose_recommended_workers(results), 2)


if __name__ == "__main__":
    unittest.main()
