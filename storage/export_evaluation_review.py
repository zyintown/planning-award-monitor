"""只读导出 P2 人工标注全集与按来源分层的优先标注队列。"""

import argparse
import csv
import hashlib
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT_DATE = "2026-07-07"
DEFAULT_KEYWORD_SAMPLE_SIZE = 120
DEFAULT_SEED = 20260710

FIELDNAMES = [
    "id",
    "title",
    "url",
    "source",
    "publish_date",
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
    "人工审核结论",
    "审核备注",
]


def classify_status(status: str) -> tuple[str, str]:
    if status in {"ai_confirmed", "ready_to_push", "push_failed", "pushed"}:
        return "ai_positive", "是"
    if status == "ai_rejected":
        return "ai_negative", "否"
    if status in {"new", "keyword_rejected"}:
        return "keyword_negative", "否"
    return "unresolved", ""


def _stable_order(row: dict, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{row['id']}".encode("utf-8")).hexdigest()


def _allocate_by_source(groups: dict[str, list[dict]], target: int) -> dict[str, int]:
    """优先覆盖每个来源，再按剩余容量比例分配，结果完全确定。"""
    names = sorted(groups)
    total = sum(len(groups[name]) for name in names)
    target = max(0, min(target, total))
    allocation = {name: 0 for name in names}
    if target == 0:
        return allocation
    if target >= total:
        return {name: len(groups[name]) for name in names}

    if target >= len(names):
        for name in names:
            allocation[name] = 1
    else:
        for name in sorted(names, key=lambda item: (-len(groups[item]), item))[:target]:
            allocation[name] = 1
        return allocation

    remaining = target - sum(allocation.values())
    while remaining:
        candidates = [
            name for name in names if allocation[name] < len(groups[name])
        ]
        if not candidates:
            break
        capacity_total = sum(len(groups[name]) - allocation[name] for name in candidates)
        quotas = {
            name: remaining * (len(groups[name]) - allocation[name]) / capacity_total
            for name in candidates
        }
        granted = 0
        for name in candidates:
            addition = min(
                int(quotas[name]), len(groups[name]) - allocation[name]
            )
            allocation[name] += addition
            granted += addition
        remaining -= granted
        if not remaining:
            break
        for name in sorted(
            candidates,
            key=lambda item: (-(quotas[item] - int(quotas[item])), item),
        ):
            if remaining == 0:
                break
            if allocation[name] < len(groups[name]):
                allocation[name] += 1
                remaining -= 1
    return allocation


def build_review_rows(
    rows: list[dict],
    keyword_sample_size: int = DEFAULT_KEYWORD_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
    snapshot_date: str = DEFAULT_SNAPSHOT_DATE,
) -> tuple[list[dict], list[dict]]:
    prepared = []
    keyword_groups: dict[str, list[dict]] = defaultdict(list)
    for source_row in rows:
        item = dict(source_row)
        stage, predicted = classify_status(str(item.get("status", "")))
        item["decision_stage"] = stage
        item["predicted_should_push"] = predicted
        item["priority_review"] = "否"
        item["sampling_stratum"] = ""
        item["stratum_population"] = ""
        item["stratum_sample_size"] = ""
        item["sampling_weight"] = ""
        item["snapshot_date"] = snapshot_date
        item["人工审核结论"] = ""
        item["审核备注"] = ""
        prepared.append(item)
        if stage == "keyword_negative":
            keyword_groups[str(item["source"])].append(item)

    allocation = _allocate_by_source(keyword_groups, keyword_sample_size)
    selected_keyword_ids: set[int] = set()
    keyword_metadata: dict[str, tuple[int, int, float]] = {}
    for source, group in keyword_groups.items():
        sample_size = allocation[source]
        population = len(group)
        keyword_metadata[source] = (
            population,
            sample_size,
            population / sample_size if sample_size else 0.0,
        )
        selected = sorted(group, key=lambda row: _stable_order(row, seed))[:sample_size]
        selected_keyword_ids.update(int(row["id"]) for row in selected)

    stage_counts = defaultdict(int)
    for item in prepared:
        stage_counts[item["decision_stage"]] += 1

    full_rows = []
    priority = []
    for base_item in prepared:
        stage = base_item["decision_stage"]
        if stage in {"ai_positive", "ai_negative"}:
            stratum = stage
            population = stage_counts[stage]
            sample_size = population
            weight = 1.0
            selected = True
        elif stage == "keyword_negative" and int(base_item["id"]) in selected_keyword_ids:
            stratum = f"keyword_negative|{base_item['source']}"
            population, sample_size, weight = keyword_metadata[str(base_item["source"])]
            selected = True
        else:
            selected = False

        full_item = dict(base_item)
        full_item["priority_review"] = "是" if selected else "否"
        if stage in {"ai_positive", "ai_negative"}:
            full_population = stage_counts[stage]
            full_item.update(
                sampling_stratum=f"full|{stage}",
                stratum_population=full_population,
                stratum_sample_size=full_population,
                sampling_weight="1.00000000",
            )
        elif stage == "keyword_negative":
            full_population = len(keyword_groups[str(base_item["source"])])
            full_item.update(
                sampling_stratum=f"full|keyword_negative|{base_item['source']}",
                stratum_population=full_population,
                stratum_sample_size=full_population,
                sampling_weight="1.00000000",
            )
        full_rows.append(full_item)

        if selected:
            priority_item = dict(base_item)
            priority_item["priority_review"] = "是"
            priority_item["sampling_stratum"] = stratum
            priority_item["stratum_population"] = population
            priority_item["stratum_sample_size"] = sample_size
            priority_item["sampling_weight"] = f"{weight:.8f}"
            priority.append(priority_item)

    return full_rows, priority


def _read_articles(db_path: Path) -> list[dict]:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, title, url, source, publish_date, status, pipeline_version
                FROM articles
                ORDER BY id
                """
            )
        ]
    finally:
        connection.close()


def _write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def export_review_files(
    db_path: Path,
    full_output: Path,
    priority_output: Path,
    keyword_sample_size: int = DEFAULT_KEYWORD_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
    snapshot_date: str = DEFAULT_SNAPSHOT_DATE,
) -> tuple[int, int]:
    rows = _read_articles(db_path)
    full_rows, priority_rows = build_review_rows(
        rows,
        keyword_sample_size=keyword_sample_size,
        seed=seed,
        snapshot_date=snapshot_date,
    )
    _write_csv(full_output, full_rows)
    _write_csv(priority_output, priority_rows)
    return len(full_rows), len(priority_rows)


def main() -> int:
    today = date.today().strftime("%Y%m%d")
    parser = argparse.ArgumentParser(description="导出 P2 人工标注全集和优先队列")
    parser.add_argument("--db", default=str(ROOT / "data" / "monitor.db"))
    parser.add_argument(
        "--full-output",
        default=str(ROOT / "data" / "reports" / f"P2人工标注全集_{today}.csv"),
    )
    parser.add_argument(
        "--priority-output",
        default=str(ROOT / "data" / "reports" / f"P2人工标注优先队列_{today}.csv"),
    )
    parser.add_argument("--keyword-sample-size", type=int, default=DEFAULT_KEYWORD_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--snapshot-date", default=DEFAULT_SNAPSHOT_DATE)
    args = parser.parse_args()

    full_count, priority_count = export_review_files(
        Path(args.db),
        Path(args.full_output),
        Path(args.priority_output),
        keyword_sample_size=args.keyword_sample_size,
        seed=args.seed,
        snapshot_date=args.snapshot_date,
    )
    print(f"完整清单: {full_count} 条 -> {args.full_output}")
    print(f"优先队列: {priority_count} 条 -> {args.priority_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
