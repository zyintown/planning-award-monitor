"""对已人工标注的事件时点队列做 P3 只读回放；不访问数据库或飞书。"""

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.base import Article
from filters.ai_filter import AIFilter
from filters.title_gate import TitleGate
from storage.evaluate_labels import calculate_metrics, render_report


def _article(row: dict) -> Article:
    return Article(row["title"], row["url"], row["source"], publish_date=row["event_date"])


def replay(rows: list[dict], model: str, workers: int) -> list[dict]:
    gate = TitleGate()
    replay_rows = []
    candidates = []
    for source_row in rows:
        row = dict(source_row)
        article = _article(row)
        allowed, reason = gate.check(article, date.fromisoformat(row["event_date"]))
        row["p3_title_gate"] = "通过" if allowed else "拒绝"
        row["p3_reason"] = reason
        row["p3_ai_state"] = "未调用"
        row["predicted_should_push"] = "否"
        replay_rows.append(row)
        if allowed:
            candidates.append((len(replay_rows) - 1, article, date.fromisoformat(row["event_date"])))

    ai_filter = AIFilter(model=model, max_workers=workers)
    def judge(candidate):
        index, article, judgment_date = candidate
        return index, ai_filter.judge(article, judgment_date=judgment_date)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, result in executor.map(judge, candidates):
            row = replay_rows[index]
            row["p3_ai_state"] = "通过" if result.decision is True else "拒绝" if result.decision is False else "待重试"
            row["p3_reason"] = result.reason
            row["predicted_should_push"] = "是" if result.decision is True else "否"
    return replay_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="P3 事件时点评估离线回放")
    parser.add_argument("input", help="已填写事件时点评估结论的 CSV")
    parser.add_argument("--model", default="qwen3.5:latest")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output", default=str(ROOT / "data" / "reports" / "P3事件时点离线回放_20260710.csv"))
    parser.add_argument("--report", default=str(ROOT / "data" / "reports" / "P3事件时点离线回放_20260710.md"))
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        raise ValueError("workers 必须在1到4之间")
    with Path(args.input).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    replay_rows = replay(rows, args.model, args.workers)
    added = ["p3_title_gate", "p3_ai_state", "p3_reason"]
    with Path(args.output).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames + added, extrasaction="raise")
        writer.writeheader()
        writer.writerows(replay_rows)
    result = calculate_metrics(replay_rows)
    report = render_report(result, Path(args.output))
    report = report.replace("# P2 事件时点筛选质量评估 - ", "## 指标与误报漏报 - ", 1)
    report = "# P3 事件时点离线回放\n\n- 模型：`%s`\n- 标题门禁通过：%d/%d\n\n%s" % (
        args.model, sum(row["p3_title_gate"] == "通过" for row in replay_rows), len(replay_rows), report
    )
    Path(args.report).write_text(report, encoding="utf-8")
    print(f"P3回放完成：AI候选 {sum(row['p3_title_gate'] == '通过' for row in replay_rows)} 条，报告: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
