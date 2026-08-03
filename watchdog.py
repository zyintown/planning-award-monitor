"""计划任务缺跑看门狗。

该脚本只读检查 run_logs，不注册或修改 Windows 计划任务。
使用 --check 时只输出检查结果，不发送飞书告警。
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path

import yaml

from main import PROJECT_ROOT, load_config, resolve_config_paths
from notifiers.feishu import FeishuNotifier


DEFAULT_SCHEDULES = [
    {"name": "morning", "hour": 9, "minute": 0},
    {"name": "evening", "hour": 21, "minute": 0},
]


def get_schedules(config: dict) -> list[dict]:
    schedules = config.get("health", {}).get("watchdog", {}).get("schedules")
    if not schedules:
        schedules = DEFAULT_SCHEDULES
    return [
        {
            "name": str(item["name"]),
            "hour": int(item["hour"]),
            "minute": int(item.get("minute", 0)),
        }
        for item in schedules
    ]


def _parse_run_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def find_missed_windows(
    db_path: str | Path,
    schedules: list[dict],
    now: datetime | None = None,
    grace_minutes: int = 90,
) -> list[dict]:
    """只读找出已超过宽限时间、但没有完成运行记录的计划窗口。"""
    current = now or datetime.now()
    missed = []
    database_path = Path(db_path).resolve()
    if not database_path.exists():
        return [
            {
                "key": f"{current.date()}:database",
                "name": "database",
                "scheduled_at": "",
                "reason": f"数据库不存在: {database_path}",
            }
        ]

    uri = f"file:{database_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        for schedule in schedules:
            scheduled_at = datetime.combine(
                current.date(),
                datetime_time(schedule["hour"], schedule["minute"]),
            )
            if current < scheduled_at + timedelta(minutes=grace_minutes):
                continue

            window_start = scheduled_at - timedelta(minutes=15)
            window_end = scheduled_at + timedelta(minutes=grace_minutes)
            row = conn.execute(
                """
                SELECT id, run_time, status
                FROM run_logs
                WHERE run_time >= ? AND run_time <= ?
                  AND status IN ('completed', 'completed_with_errors', 'failed')
                ORDER BY id DESC LIMIT 1
                """,
                (
                    window_start.strftime("%Y-%m-%d %H:%M:%S"),
                    window_end.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            ).fetchone()
            if row is None:
                missed.append(
                    {
                        "key": f"{current.date()}:{schedule['name']}",
                        "name": schedule["name"],
                        "scheduled_at": scheduled_at.isoformat(timespec="minutes"),
                        "reason": "宽限窗口内没有完成的运行记录",
                    }
                )
    finally:
        conn.close()
    return missed


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {"alerted": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"alerted": []}
    except (OSError, json.JSONDecodeError):
        return {"alerted": []}


def _write_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def run_watchdog(config: dict, check_only: bool = False, now: datetime | None = None) -> int:
    resolved = resolve_config_paths(config, PROJECT_ROOT)
    health = resolved.get("health", {})
    watchdog_config = health.get("watchdog", {})
    grace_minutes = int(watchdog_config.get("grace_minutes", 90))
    missing = find_missed_windows(
        resolved["storage"]["db_path"],
        get_schedules(resolved),
        now=now,
        grace_minutes=grace_minutes,
    )

    if check_only:
        print(json.dumps({"missing": missing}, ensure_ascii=False, indent=2))
        return 1 if missing else 0

    state_path = Path(watchdog_config.get("state_path", "data/watchdog-state.json"))
    if not state_path.is_absolute():
        state_path = PROJECT_ROOT / state_path
    state = _read_state(state_path)
    alerted = set(state.get("alerted", []))
    current_keys = {item["key"] for item in missing}
    new_missing = [item for item in missing if item["key"] not in alerted]

    if new_missing:
        notification = resolved.get("notification", {}).get("feishu", {})
        notifier = FeishuNotifier(
            webhook_url=notification.get("webhook_url", ""),
            secret=notification.get("secret", ""),
        )
        message = "；".join(
            f"{item['name']}({item['scheduled_at']})：{item['reason']}"
            for item in new_missing
        )
        if notifier.notify_alert(f"计划任务缺跑：{message}"):
            alerted.update(item["key"] for item in new_missing)

    state["alerted"] = sorted(alerted & current_keys)
    state["checked_at"] = (now or datetime.now()).isoformat(timespec="seconds")
    _write_state(state_path, state)
    return 1 if missing else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="报奖信息监测计划任务缺跑看门狗")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.yaml"))
    parser.add_argument(
        "--check", action="store_true", help="只读检查并输出结果，不发送告警"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        return run_watchdog(config, check_only=args.check)
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"看门狗检查失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
