"""从既有 P2 优先队列生成事件时点评估表；不改写原审核结果或数据库。"""

import argparse
import csv
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVENT_TIME_LABEL = "事件时点评估结论"
EVENT_TIME_BASIS = "event_time_basis"

FIELDNAMES = [
    "id",
    "title",
    "url",
    "source",
    "publish_date",
    "first_seen_at",
    "event_date",
    EVENT_TIME_BASIS,
    "status",
    "pipeline_version",
    "decision_stage",
    "predicted_should_push",
    "sampling_stratum",
    "stratum_population",
    "stratum_sample_size",
    "sampling_weight",
    "priority_review",
    "snapshot_date",
    "内容类型",
    "行业相关",
    "事件时点可行动",
    EVENT_TIME_LABEL,
    "证据链接",
    "证据说明",
    "复核人",
    "复核时间",
    "快照口径审核结论",
    "快照口径审核备注",
]


def _normalize_date(value: str) -> str:
    """仅接受完整日期，避免把不完整的年月误作确定的评估时点。"""
    text = (value or "").strip()
    match = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if not match:
        return ""
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return ""


def determine_event_time(publish_date: str, first_seen_at: str) -> tuple[str, str]:
    published = _normalize_date(publish_date)
    if published:
        return published, "published_at"
    first_seen = _normalize_date(first_seen_at)
    if first_seen:
        return first_seen, "first_seen_at"
    return "", "unknown"


def prepare_rows(review_rows: list[dict], first_seen_by_id: dict[str, str]) -> list[dict]:
    """保留抽样分层、权重和旧快照结论，初始化新的事件时点评估列。"""
    prepared = []
    for source_row in review_rows:
        row = dict(source_row)
        identifier = str(row.get("id", ""))
        first_seen_at = first_seen_by_id.get(identifier, "")
        event_date, basis = determine_event_time(
            row.get("publish_date", ""), first_seen_at
        )
        row.update(
            first_seen_at=first_seen_at,
            event_date=event_date,
            **{EVENT_TIME_BASIS: basis},
            内容类型="",
            行业相关="",
            事件时点可行动="",
            **{EVENT_TIME_LABEL: ""},
            证据链接=row.get("url", ""),
            证据说明="",
            复核人="",
            复核时间="",
            快照口径审核结论=row.get("人工审核结论", ""),
            快照口径审核备注=row.get("审核备注", ""),
        )
        prepared.append(row)
    return prepared


def _read_first_seen(db_path: Path) -> dict[str, str]:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        return {
            str(identifier): created_at or ""
            for identifier, created_at in connection.execute(
                "SELECT id, created_at FROM articles"
            )
        }
    finally:
        connection.close()


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    today = datetime.now().strftime("%Y%m%d")
    parser = argparse.ArgumentParser(description="生成 P2 事件时点评估优先队列")
    parser.add_argument(
        "input",
        help="已完成快照口径审核的 P2 优先队列 CSV；原文件不会被改写",
    )
    parser.add_argument("--db", default=str(ROOT / "data" / "monitor.db"))
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "reports" / f"P2事件时点评估优先队列_{today}.csv"),
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    rows = prepare_rows(_read_csv(input_path), _read_first_seen(Path(args.db)))
    output_path = Path(args.output)
    _write_csv(output_path, rows)
    basis_counts = {basis: sum(row[EVENT_TIME_BASIS] == basis for row in rows) for basis in ("published_at", "first_seen_at", "unknown")}
    print(
        f"事件时点队列: {len(rows)} 条 -> {output_path}；"
        f"发布日期 {basis_counts['published_at']}，首次发现代理 {basis_counts['first_seen_at']}，不可判定 {basis_counts['unknown']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
