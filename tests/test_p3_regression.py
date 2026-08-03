import csv
import unittest
from datetime import date
from pathlib import Path

from crawlers.base import Article
from filters.title_gate import TitleGate


class P3RegressionTests(unittest.TestCase):
    def test_all_prior_errors_are_frozen_and_title_gate_preserves_recall(self):
        fixture = Path(__file__).parent / "fixtures" / "p3_event_time_regressions.csv"
        with fixture.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 52)
        self.assertEqual(sum(row["case_type"] == "prior_false_positive" for row in rows), 45)
        false_negatives = [row for row in rows if row["case_type"] == "prior_false_negative"]
        self.assertEqual(len(false_negatives), 7)

        gate = TitleGate()
        decisions = {}
        for row in rows:
            article = Article(row["title"], "https://example.com", row["source"], publish_date=row["event_date"])
            decisions[row["id"]] = gate.check(article, date.fromisoformat(row["event_date"]))[0]
        self.assertTrue(all(decisions[row["id"]] for row in false_negatives))
        self.assertGreaterEqual(
            sum(not decisions[row["id"]] for row in rows if row["case_type"] == "prior_false_positive"),
            43,
        )

    def test_network_popularity_vote_is_not_an_application(self):
        allowed, _ = TitleGate().check(
            Article("微花园设计大赛网络人气评选活动正式启动", "https://example.com", "中国风景园林学会", publish_date="2025-08-01"),
            date(2025, 8, 1),
        )
        self.assertFalse(allowed)
