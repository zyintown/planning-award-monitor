import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from crawlers.base import Article, CrawlResult
from filters.ai_filter import AIFilter, AIJudgeResult
from main import (
    ConfigError,
    process_ai_pending,
    process_pending_notifications,
    run_crawlers_with_retries,
    run_pipeline,
    source_matches,
    topic_account_aliases,
    validate_config,
)
from notifiers.feishu import NotificationBatchResult
from storage.database import Database
from utils.event_identity import event_key


class FakeNotifier:
    def __init__(self, success):
        self.success = success
        self.calls = 0

    def notify_batches(self, articles):
        self.calls += 1
        return [
            NotificationBatchResult(
                articles=list(articles),
                success=self.success,
                error="模拟失败" if not self.success else "",
            )
        ]


class PipelineTests(unittest.TestCase):
    def _ready_article(self, db, title, url, source, created_at=None):
        article_id, _ = db.add_discovered(
            Article(title, url, source, publish_date="2026-07-30")
        )
        db.upsert_article_extraction(
            article_id,
            is_award_application=True,
            award_name="自然资源科学技术奖",
            deadline_text="申报截止",
            deadline_date="2026-09-23",
            applicant_scope=("有关单位",),
            model="test",
            prompt_version="test",
            raw_response="{}",
        )
        if created_at:
            with db._get_conn() as conn:
                conn.execute(
                    "UPDATE articles SET created_at=? WHERE id=?",
                    (created_at, article_id),
                )
        db.transition_status(article_id, "ready_to_push")
        return article_id

    def test_config_rejects_unsafe_ai_worker_count(self):
        config = {
            "sources": {
                "websites": [
                    {"name": "测试源", "url": "https://example.com", "type": "org_general"}
                ],
                "wechat_accounts": [],
            },
            "filter": {
                "keywords": ["申报"],
                "exclude_keywords": [],
                "ai": {
                    "enabled": True,
                    "api_url": "http://localhost:11434/api/chat",
                    "model": "test",
                    "max_workers": 5,
                },
            },
        }
        with self.assertRaises(ConfigError):
            validate_config(config, dry_run=True)

    def test_source_filter_prefix_disambiguates_same_name(self):
        self.assertTrue(source_matches("website:同名机构", "同名机构", "website"))
        self.assertFalse(source_matches("website:同名机构", "同名机构", "wechat"))
        self.assertTrue(source_matches("同名机构", "同名机构", "wechat"))
        self.assertTrue(source_matches("gov_general", "某网站", "gov_general"))

    def test_disabled_direct_account_can_remain_in_topic_whitelist(self):
        aliases = topic_account_aliases(
            [
                {
                    "name": "资源中国",
                    "enabled": False,
                    "topic_match_enabled": True,
                },
                {"name": "完全停用", "enabled": False},
                {
                    "name": "启用账号",
                    "enabled": True,
                    "account_aliases": ["启用别名"],
                },
            ]
        )

        self.assertEqual(aliases, ["资源中国", "启用账号", "启用别名"])

    def test_failed_notification_is_retried_until_pushed(self):
        db = Database(":memory:", auto_backup=False)
        article_id, _ = db.add_discovered(
            Article("待推送通知", "https://example.com/pending", "测试源")
        )
        db.transition_status(article_id, "ready_to_push")

        failed = process_pending_notifications(db, FakeNotifier(False))
        after_failure = db.get_article_by_id(article_id)
        self.assertEqual(failed, 0)
        self.assertEqual(after_failure["status"], "push_failed")
        self.assertEqual(after_failure["attempt_count"], 1)

        pushed = process_pending_notifications(db, FakeNotifier(True))
        after_success = db.get_article_by_id(article_id)
        self.assertEqual(pushed, 1)
        self.assertEqual(after_success["status"], "pushed")
        self.assertIsNotNone(after_success["pushed_at"])

    def test_event_priority_pushes_official_and_supersedes_aggregator(self):
        db = Database(":memory:", auto_backup=False)
        official_id = self._ready_article(
            db,
            "自然资源科学技术奖申报通知",
            "https://official.example/notice",
            "中国自然资源学会",
        )
        aggregator_id = self._ready_article(
            db,
            "自然资源科学技术奖申报信息",
            "https://aggregator.example/notice",
            "奖项竞赛申报信息库",
        )
        config = {
            "sources": {
                "websites": [
                    {"name": "中国自然资源学会", "source_tier": "official_website"},
                    {"name": "奖项竞赛申报信息库", "source_tier": "aggregator"},
                ],
                "wechat_accounts": [],
            }
        }

        pushed = process_pending_notifications(db, FakeNotifier(True), config=config)

        self.assertEqual(pushed, 1)
        self.assertEqual(db.get_article_by_id(official_id)["status"], "pushed")
        self.assertEqual(db.get_article_by_id(aggregator_id)["status"], "source_superseded")

    def test_aggregator_waits_24_hours_then_falls_back(self):
        db = Database(":memory:", auto_backup=False)
        created_at = "2026-07-30 10:00:00"
        article_id = self._ready_article(
            db,
            "自然资源科学技术奖申报信息",
            "https://aggregator.example/fallback",
            "奖项竞赛申报信息库",
            created_at=created_at,
        )
        config = {
            "sources": {
                "websites": [
                    {"name": "奖项竞赛申报信息库", "source_tier": "aggregator"}
                ],
                "wechat_accounts": [],
            },
            "notification": {"aggregator_delay_hours": 24},
        }
        notifier = FakeNotifier(True)

        self.assertEqual(
            process_pending_notifications(
                db,
                notifier,
                config=config,
                now=datetime(2026, 7, 31, 9, 59),
            ),
            0,
        )
        self.assertEqual(db.get_article_by_id(article_id)["status"], "ready_to_push")
        self.assertEqual(
            process_pending_notifications(
                db,
                notifier,
                config=config,
                now=datetime(2026, 7, 31, 10, 1),
            ),
            1,
        )
        self.assertEqual(db.get_article_by_id(article_id)["status"], "pushed")

    def test_later_official_source_upgrades_pushed_provenance_without_repush(self):
        db = Database(":memory:", auto_backup=False)
        aggregator_id = self._ready_article(
            db,
            "自然资源科学技术奖申报通知",
            "https://aggregator.example/already-pushed",
            "奖项竞赛申报信息库",
        )
        db.transition_status(aggregator_id, "pushed", pushed=True)
        official_id = self._ready_article(
            db,
            "自然资源科学技术奖申报通知",
            "https://official.example/later",
            "中国自然资源学会",
        )
        config = {
            "sources": {
                "websites": [
                    {"name": "中国自然资源学会", "source_tier": "official_website"},
                    {"name": "奖项竞赛申报信息库", "source_tier": "aggregator"},
                ],
                "wechat_accounts": [],
            }
        }
        notifier = FakeNotifier(True)

        self.assertEqual(
            process_pending_notifications(db, notifier, config=config),
            0,
        )
        self.assertEqual(notifier.calls, 0)
        self.assertEqual(db.get_article_by_id(official_id)["status"], "source_superseded")
        upgraded = db.get_article_by_id(aggregator_id)
        self.assertEqual(upgraded["status"], "pushed")
        self.assertEqual(upgraded["source"], "中国自然资源学会")
        self.assertIn("来源升级", upgraded["ai_reason"])

    def test_event_key_keeps_different_local_jurisdictions_separate(self):
        sichuan = Article(
            "自然资源科学技术奖四川省申报通知",
            "https://example.com/sichuan",
            "四川省自然资源厅",
            publish_date="2026-07-30",
            award_name="自然资源科学技术奖",
        )
        guangdong = Article(
            "自然资源科学技术奖广东省申报通知",
            "https://example.com/guangdong",
            "广东省自然资源厅",
            publish_date="2026-07-30",
            award_name="自然资源科学技术奖",
        )
        self.assertNotEqual(event_key(sichuan), event_key(guangdong))

    def test_event_key_normalizes_award_year_and_ignores_summary_location(self):
        aggregator = Article(
            "2026年自然资源科学技术奖推荐工作启动",
            "https://aggregator.example/natural-resources",
            "奖项竞赛申报信息库",
            summary="上海市相关申报信息汇总",
            award_name="自然资源科学技术奖",
        )
        official = Article(
            "关于开展2026年自然资源科学技术奖推荐工作的通知",
            "https://official.example/natural-resources",
            "中国自然资源学会（新闻栏目）",
            summary="全国推荐工作通知",
            award_name="2026 年自然资源科学技术奖",
        )
        self.assertEqual(event_key(aggregator), event_key(official))

    def test_dry_run_never_calls_notifier_or_changes_status(self):
        db = Database(":memory:", auto_backup=False)
        article_id, _ = db.add_discovered(
            Article("待推送通知", "https://example.com/dry", "测试源")
        )
        db.transition_status(article_id, "ready_to_push")
        notifier = FakeNotifier(True)

        pushed = process_pending_notifications(db, notifier, dry_run=True)

        self.assertEqual(pushed, 0)
        self.assertEqual(notifier.calls, 0)
        self.assertEqual(db.get_article_by_id(article_id)["status"], "ready_to_push")

    def test_pipeline_records_website_failure_in_run_log(self):
        db = Database(":memory:", auto_backup=False)
        config = {
            "sources": {"websites": [], "wechat_accounts": []},
            "filter": {
                "keywords": ["申报"],
                "exclude_keywords": [],
                "ai": {"enabled": False},
            },
            "crawler": {"detail": {"enabled": False}},
            "notification": {"feishu": {"webhook_url": ""}},
            "health": {
                "failure_alert_threshold": 3,
                "all_failed_retry_delays_minutes": [],
            },
        }
        failed_result = CrawlResult(
            source="失败网站",
            source_type="website",
            status="failed",
            articles=[],
            duration_seconds=0.1,
            error="timeout",
        )

        with patch("main.run_crawlers", return_value=[failed_result]):
            result = run_pipeline(
                config,
                dry_run=True,
                db=db,
                notifier=FakeNotifier(True),
            )

        self.assertEqual(result["status"], "completed_with_errors")
        self.assertIn("失败网站: timeout", result["errors"])
        with db._get_conn() as conn:
            run_row = conn.execute(
                "SELECT status, errors FROM run_logs WHERE id=?", (result["run_id"],)
            ).fetchone()
            source_row = conn.execute(
                "SELECT status, error FROM source_runs WHERE run_id=?", (result["run_id"],)
            ).fetchone()
        self.assertEqual(run_row["status"], "completed_with_errors")
        self.assertIn("失败网站", run_row["errors"])
        self.assertEqual(source_row["status"], "failed")
        self.assertEqual(source_row["error"], "timeout")

    def test_all_source_failure_retries_until_success(self):
        failed = CrawlResult("源", "website", "failed", [], 0.1)
        success = CrawlResult("源", "website", "success", [], 0.1)
        sleeps = []
        with patch("main.run_crawlers", side_effect=[[failed], [success]]) as mocked:
            results, retry_count = run_crawlers_with_retries(
                {"health": {"all_failed_retry_delays_minutes": [30, 90]}},
                sleep_fn=sleeps.append,
            )

        self.assertEqual(results, [success])
        self.assertEqual(retry_count, 1)
        self.assertEqual(sleeps, [1800.0])
        self.assertEqual(mocked.call_count, 2)

    def test_all_source_failure_uses_both_retries(self):
        failed = CrawlResult("源", "website", "failed", [], 0.1)
        sleeps = []
        with patch("main.run_crawlers", side_effect=[[failed], [failed], [failed]]):
            results, retry_count = run_crawlers_with_retries(
                {"health": {"all_failed_retry_delays_minutes": [30, 90]}},
                sleep_fn=sleeps.append,
            )

        self.assertEqual(results, [failed])
        self.assertEqual(retry_count, 2)
        self.assertEqual(sleeps, [1800.0, 5400.0])

    def test_source_filter_does_not_retry_all_source_failure(self):
        failed = CrawlResult("源", "website", "failed", [], 0.1)
        sleeps = []
        with patch("main.run_crawlers", return_value=[failed]) as mocked:
            results, retry_count = run_crawlers_with_retries(
                {"health": {"all_failed_retry_delays_minutes": [30, 90]}},
                source_filter="website:源",
                sleep_fn=sleeps.append,
            )

        self.assertEqual(results, [failed])
        self.assertEqual(retry_count, 0)
        self.assertEqual(sleeps, [])
        self.assertEqual(mocked.call_count, 1)

    def test_wechat_semantic_zero_match_is_recorded_as_no_match(self):
        db = Database(":memory:", auto_backup=False)
        result = CrawlResult(
            source="微信公众号主题检索",
            source_type="wechat",
            status="success",
            articles=[
                Article(
                    "自然资源奖项申报通知",
                    "https://example.com/wechat-anomaly",
                    "微信公众号:未知",
                )
            ],
            duration_seconds=0.1,
            metrics={
                "raw_result_count": 2,
                "account_matched_count": 0,
                "window_kept_count": 1,
            },
        )
        config = {
            "sources": {"websites": [], "wechat_accounts": []},
            "filter": {
                "keywords": ["申报"],
                "exclude_keywords": [],
                "ai": {"enabled": False},
            },
            "crawler": {"detail": {"enabled": False}},
            "notification": {"feishu": {"webhook_url": ""}},
            "health": {"all_failed_retry_delays_minutes": []},
        }

        with patch("main.run_crawlers", return_value=[result]):
            outcome = run_pipeline(
                config,
                dry_run=True,
                db=db,
                notifier=FakeNotifier(True),
            )

        self.assertEqual(outcome["status"], "completed")
        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT status, error FROM source_runs WHERE run_id=?",
                (outcome["run_id"],),
            ).fetchone()
        self.assertEqual(row["status"], "no_match")
        self.assertIn("不计入渠道故障", row["error"])

    def test_ai_result_is_persisted_before_notification(self):
        db = Database(":memory:", auto_backup=False)
        article_id, _ = db.add_discovered(
            Article("奖项申报通知", "https://example.com/structured", "测试源")
        )
        db.transition_status(article_id, "ai_pending")
        ai_filter = AIFilter(model="test-model")
        result = AIJudgeResult(
            True,
            "仍可申报",
            raw_response="{}",
            award_name="规划设计奖",
            deadline_date="2026-08-01",
            applicant_scope=("设计单位",),
        )

        with patch.object(ai_filter, "batch_judge", return_value=[
            (db.get_articles_by_status(["ai_pending"])[0], result)
        ]):
            confirmed, rejected, pending = process_ai_pending(db, ai_filter)

        self.assertEqual((confirmed, rejected, pending), (1, 0, 0))
        self.assertEqual(db.get_article_by_id(article_id)["status"], "ready_to_push")
        self.assertEqual(db.get_article_extraction(article_id)["award_name"], "规划设计奖")

    def test_feishu_content_includes_structured_fields(self):
        article = Article(
            "奖项申报通知",
            "https://example.com/notify",
            "测试源",
            award_name="规划设计奖",
            deadline_date="2026-08-01",
            applicant_scope=["设计单位"],
        )
        from notifiers.feishu import FeishuNotifier

        text = str(FeishuNotifier("")._build_content([article]))
        self.assertIn("规划设计奖", text)
        self.assertIn("2026-08-01", text)
        self.assertIn("设计单位", text)


if __name__ == "__main__":
    unittest.main()
