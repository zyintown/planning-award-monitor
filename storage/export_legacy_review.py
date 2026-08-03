"""导出历史待处理记录供人工审核；只读，不修改数据库状态。"""

import argparse
import csv
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def export_legacy_review(db_path: Path, output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, title, url, source, publish_date, status, ai_reason, created_at
            FROM articles
            WHERE pipeline_version = 1 AND status = 'ai_confirmed'
            ORDER BY created_at, id
            """
        ).fetchall()
    finally:
        connection.close()

    fieldnames = [
        "id",
        "title",
        "url",
        "source",
        "publish_date",
        "status",
        "ai_reason",
        "created_at",
        "人工审核结论",
        "是否补发",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            item = dict(row)
            item["人工审核结论"] = ""
            item["是否补发"] = ""
            writer.writerow(item)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="导出历史 ai_confirmed 人工审核清单")
    parser.add_argument("--db", default=str(ROOT / "data" / "monitor.db"))
    parser.add_argument(
        "--output",
        default=str(
            ROOT
            / "data"
            / "reports"
            / f"历史ai_confirmed审核清单_{datetime.now():%Y%m%d}.csv"
        ),
    )
    args = parser.parse_args()
    count = export_legacy_review(Path(args.db), Path(args.output))
    print(f"已导出 {count} 条: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
