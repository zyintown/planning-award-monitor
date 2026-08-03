import sqlite3
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from crawlers.base import Article
from storage.database import Database


ROOT = Path(__file__).resolve().parents[1]
TEST_RUN_DIR = ROOT / "data" / "test-runs"


class DatabaseTests(unittest.TestCase):
    def test_v1_migration_preserves_legacy_and_excludes_it_from_retry(self):
        TEST_RUN_DIR.mkdir(parents=True, exist_ok=True)
        db_path = TEST_RUN_DIR / f"migration-{uuid.uuid4().hex}.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    url TEXT UNIQUE NOT NULL,
                    source TEXT NOT NULL,
                    publish_date TEXT,
                    summary TEXT,
                    raw_content TEXT,
                    status TEXT DEFAULT 'new',
                    ai_reason TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    pushed_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE run_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_time TEXT DEFAULT (datetime('now','localtime')),
                    total_articles INTEGER,
                    new_articles INTEGER,
                    keyword_passed INTEGER,
                    ai_confirmed INTEGER,
                    pushed INTEGER,
                    errors TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO articles (title, url, source, status) VALUES (?, ?, ?, ?)",
                ("历史待推送", "https://example.com/legacy", "测试源", "ai_confirmed"),
            )

        db = Database(str(db_path), auto_backup=False)
        legacy = db.get_article_by_id(1)

        self.assertEqual(legacy["status"], "ai_confirmed")
        self.assertEqual(legacy["pipeline_version"], 1)
        self.assertEqual(legacy["canonical_url"], "https://example.com/legacy")
        self.assertTrue(legacy["dedup_key"])
        self.assertEqual(db.get_articles_by_status(["ai_confirmed"]), [])
        self.assertEqual(db.schema_version, 3)
        with db._get_conn() as conn:
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='article_extractions'"
                ).fetchone()
            )

    def test_dedup_window_matches_database_constraints(self):
        db = Database(":memory:", dedup_days=90, auto_backup=False)
        article = Article("同一通知", "https://example.com/a?utm_source=x", "测试源", "2026-07-10")

        first_id, first_is_new = db.add_discovered(article)
        duplicate_id, duplicate_is_new = db.add_discovered(
            Article(" 同一通知 ", "https://example.com/a?utm_source=y#top", "测试源", "2026-07-10")
        )

        self.assertTrue(first_is_new)
        self.assertFalse(duplicate_is_new)
        self.assertEqual(first_id, duplicate_id)

        old_time = (datetime.now() - timedelta(days=91)).strftime("%Y-%m-%d %H:%M:%S")
        with db._get_conn() as conn:
            conn.execute("UPDATE articles SET created_at = ? WHERE id = ?", (old_time, first_id))

        second_id, second_is_new = db.add_discovered(article)
        self.assertTrue(second_is_new)
        self.assertNotEqual(first_id, second_id)

    def test_source_health_history_and_consecutive_failures(self):
        db = Database(":memory:", auto_backup=False)
        run1 = db.start_run()
        db.record_source_run(run1, "测试源", "website", "success", 10, 2, 0.5)
        db.finish_run(run1, status="completed")
        run2 = db.start_run()
        db.record_source_run(run2, "测试源", "website", "failed", 0, 0, 1.0, error="timeout")
        db.finish_run(run2, status="completed_with_errors")

        self.assertEqual(db.get_source_baseline("测试源"), 10)
        self.assertEqual(db.get_consecutive_source_failures("测试源"), 1)

        run3 = db.start_run()
        db.record_source_run(run3, "测试源", "wechat", "no_match", 0, 0, 0.2)
        db.finish_run(run3, status="completed")
        self.assertEqual(db.get_consecutive_source_failures("测试源"), 0)

        run4 = db.start_run()
        db.record_source_run(run4, "测试源", "website", "partial", 2, 0, 0.3)
        db.finish_run(run4, status="completed_with_errors")
        self.assertEqual(db.get_consecutive_source_failures("测试源"), 1)

        run5 = db.start_run()
        db.record_source_run(run5, "测试源", "website", "anomaly", 0, 0, 0.2)
        db.finish_run(run5, status="completed_with_errors")
        self.assertEqual(db.get_consecutive_source_failures("测试源"), 0)

    def test_structured_extraction_round_trip_and_article_join(self):
        db = Database(":memory:", auto_backup=False)
        article_id, _ = db.add_discovered(
            Article("奖项申报通知", "https://example.com/award", "测试源")
        )
        db.upsert_article_extraction(
            article_id,
            is_award_application=True,
            award_name="城市规划奖",
            deadline_text="8月1日前",
            deadline_date="2026-08-01",
            applicant_scope=["规划设计单位", "高校"],
            model="test-model",
            prompt_version="p2-v1",
            raw_response="{}",
        )
        db.transition_status(article_id, "ready_to_push")

        extraction = db.get_article_extraction(article_id)
        article = db.get_articles_by_status(["ready_to_push"])[0]

        self.assertEqual(extraction["applicant_scope"], ["规划设计单位", "高校"])
        self.assertEqual(article.award_name, "城市规划奖")
        self.assertEqual(article.deadline_date, "2026-08-01")
        self.assertEqual(article.applicant_scope, ["规划设计单位", "高校"])


if __name__ == "__main__":
    unittest.main()
