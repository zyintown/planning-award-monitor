import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from watchdog import find_missed_windows, run_watchdog


class FakeNotifier:
    calls = []

    def __init__(self, webhook_url, secret=""):
        self.webhook_url = webhook_url
        self.secret = secret

    def notify_alert(self, message):
        self.calls.append(message)
        return True


class WatchdogTests(unittest.TestCase):
    def _make_db(self, directory, run_time=None, status="completed"):
        path = Path(directory) / "monitor.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE run_logs (id INTEGER PRIMARY KEY, run_time TEXT, status TEXT)"
        )
        if run_time:
            conn.execute("INSERT INTO run_logs(run_time, status) VALUES (?, ?)", (run_time, status))
        conn.commit()
        conn.close()
        return path

    def test_missing_window_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._make_db(directory)
            missing = find_missed_windows(
                path,
                [{"name": "morning", "hour": 9, "minute": 0}],
                now=datetime(2026, 7, 31, 11, 0),
                grace_minutes=90,
            )

        self.assertEqual([item["name"] for item in missing], ["morning"])

    def test_completed_window_is_not_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._make_db(directory, "2026-07-31 09:05:00")
            missing = find_missed_windows(
                path,
                [{"name": "morning", "hour": 9, "minute": 0}],
                now=datetime(2026, 7, 31, 11, 0),
                grace_minutes=90,
            )

        self.assertEqual(missing, [])

    def test_alert_is_deduplicated_by_state(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = self._make_db(directory)
            state_path = Path(directory) / "watchdog-state.json"
            config = {
                "storage": {"db_path": str(db_path)},
                "health": {
                    "watchdog": {
                        "grace_minutes": 90,
                        "state_path": str(state_path),
                        "schedules": [{"name": "morning", "hour": 9, "minute": 0}],
                    }
                },
                "notification": {"feishu": {"webhook_url": "test"}},
            }
            FakeNotifier.calls = []
            with patch("watchdog.FeishuNotifier", FakeNotifier):
                first = run_watchdog(config, now=datetime(2026, 7, 31, 11, 0))
                second = run_watchdog(config, now=datetime(2026, 7, 31, 11, 5))

        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        self.assertEqual(len(FakeNotifier.calls), 1)


if __name__ == "__main__":
    unittest.main()
