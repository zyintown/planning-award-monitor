"""读取 P2 人工标注 CSV，生成加权质量指标和误报/漏报清单。"""

import argparse
import csv
import random
from collections import defaultdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POSITIVE_LABELS = {"是", "1", "true", "yes", "y"}
NEGATIVE_LABELS = {"否", "0", "false", "no", "n"}
UNCERTAIN_LABELS = {"不确定", "uncertain", "unknown", "?"}
EVENT_TIME_LABEL = "事件时点评估结论"
EVENT_TIME_BASIS = "event_time_basis"
USABLE_EVENT_TIME_BASES = {"published_at", "first_seen_at"}


def parse_label(value: str) -> bool | None:
    normalized = (value or "").strip().lower()
    if normalized in POSITIVE_LABELS:
        return True
    if normalized in NEGATIVE_LABELS:
        return False
    if not normalized or normalized in UNCERTAIN_LABELS:
        return None
    raise ValueError(f"无法识别人工审核结论: {value}")


def parse_prediction(value: str) -> bool:
    parsed = parse_label(value)
    if parsed is None:
        raise ValueError(f"预测值不能为空: {value}")
    return parsed


def _confusion(rows: list[dict]) -> dict[str, float]:
    counts = {"tp": 0.0, "fp": 0.0, "tn": 0.0, "fn": 0.0}
    for row in rows:
        actual = row["_actual"]
        predicted = row["_predicted"]
        weight = row["_weight"]
        key = (
            "tp" if predicted and actual else
            "fp" if predicted and not actual else
            "fn" if not predicted and actual else
            "tn"
        )
        counts[key] += weight
    return counts


def _ratios(counts: dict[str, float]) -> dict[str, float | None]:
    tp, fp, tn, fn = (counts[key] for key in ("tp", "fp", "tn", "fn"))
    total = tp + fp + tn + fn
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "accuracy": (tp + tn) / total if total else None,
        "precision": precision,
        "recall": recall,
        "f1": (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else None
        ),
    }


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    fraction = index - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def _bootstrap_intervals(
    rows: list[dict], iterations: int = 1000, seed: int = 20260710
) -> dict[str, tuple[float | None, float | None]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["sampling_stratum"]].append(row)
    randomizer = random.Random(seed)
    samples = {name: [] for name in ("accuracy", "precision", "recall", "f1")}

    for _ in range(iterations):
        replicate = []
        for group in grouped.values():
            population = int(group[0]["stratum_population"])
            planned_sample = int(group[0]["stratum_sample_size"])
            fully_labeled_census = population == planned_sample == len(group)
            if fully_labeled_census:
                replicate.extend(group)
            else:
                replicate.extend(randomizer.choice(group) for _ in range(len(group)))
        ratios = _ratios(_confusion(replicate))
        for name, value in ratios.items():
            if value is not None:
                samples[name].append(value)

    return {
        name: (_percentile(values, 0.025), _percentile(values, 0.975))
        for name, values in samples.items()
    }


def _detect_label_column(source_rows: list[dict]) -> str:
    headers = set().union(*(row.keys() for row in source_rows)) if source_rows else set()
    return EVENT_TIME_LABEL if EVENT_TIME_LABEL in headers else "人工审核结论"


def calculate_metrics(
    source_rows: list[dict], iterations: int = 1000, label_column: str | None = None
) -> dict:
    label_column = label_column or _detect_label_column(source_rows)
    event_time_mode = label_column == EVENT_TIME_LABEL
    labeled = []
    uncertain = 0
    uncertain_weight = 0.0
    time_unassessable = 0
    time_unassessable_weight = 0.0
    for source_row in source_rows:
        weight = float(source_row.get("sampling_weight") or 1.0)
        if event_time_mode and source_row.get(EVENT_TIME_BASIS, "").strip() not in USABLE_EVENT_TIME_BASES:
            time_unassessable += 1
            time_unassessable_weight += weight
            continue
        actual = parse_label(source_row.get(label_column, ""))
        if actual is None:
            if (source_row.get(label_column) or "").strip():
                uncertain += 1
                uncertain_weight += weight
            continue
        row = dict(source_row)
        row["_actual"] = actual
        row["_predicted"] = parse_prediction(row["predicted_should_push"])
        row["_weight"] = weight
        labeled.append(row)

    counts = _confusion(labeled)
    ratios = _ratios(counts)
    intervals = (
        _bootstrap_intervals(labeled, iterations=iterations)
        if labeled and iterations > 0
        else {name: (None, None) for name in ratios}
    )
    false_positives = [
        row for row in labeled if row["_predicted"] and not row["_actual"]
    ]
    false_negatives = [
        row for row in labeled if not row["_predicted"] and row["_actual"]
    ]
    return {
        "total_rows": len(source_rows),
        "labeled_rows": len(labeled),
        "uncertain_rows": uncertain,
        "uncertain_weight": uncertain_weight,
        "time_unassessable_rows": time_unassessable,
        "time_unassessable_weight": time_unassessable_weight,
        "pending_rows": len(source_rows) - len(labeled) - uncertain - time_unassessable,
        "coverage": len(labeled) / len(source_rows) if source_rows else 0.0,
        "complete": len(labeled) + uncertain + time_unassessable == len(source_rows),
        "label_column": label_column,
        "event_time_mode": event_time_mode,
        "confusion": counts,
        "metrics": ratios,
        "intervals": intervals,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }


def _fmt_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _fmt_metric(value: float | None) -> str:
    return "无法计算" if value is None else f"{value:.2%}"


def render_report(result: dict, source_path: Path) -> str:
    status = "评估完成" if result["complete"] else "临时结果（标注未完成）"
    title = "P2 事件时点筛选质量评估" if result["event_time_mode"] else "P2 筛选质量评估"
    lines = [
        f"# {title} - {date.today():%Y-%m-%d}",
        "",
        f"- 输入：`{source_path}`",
        f"- 状态：{status}",
        f"- 标签列：`{result['label_column']}`",
        f"- 已明确标注：{result['labeled_rows']}/{result['total_rows']}（{result['coverage']:.1%}）",
        f"- 不确定：{result['uncertain_rows']}（加权 { _fmt_number(result['uncertain_weight']) }）",
        f"- 时点不可判定：{result['time_unassessable_rows']}（加权 { _fmt_number(result['time_unassessable_weight']) }）",
        f"- 待标注：{result['pending_rows']}",
        "",
        "## 加权混淆矩阵",
        "",
        "| TP | FP | TN | FN |",
        "|---:|---:|---:|---:|",
        "| " + " | ".join(
            _fmt_number(result["confusion"][key]) for key in ("tp", "fp", "tn", "fn")
        ) + " |",
        "",
        "## 指标",
        "",
        "| 指标 | 估计值 | 95% 区间 |",
        "|---|---:|---:|",
    ]
    labels = {"accuracy": "准确率", "precision": "精确率", "recall": "召回率", "f1": "F1"}
    for name in ("accuracy", "precision", "recall", "f1"):
        low, high = result["intervals"][name]
        interval = (
            "无法计算" if low is None or high is None else f"{low:.2%} ～ {high:.2%}"
        )
        lines.append(f"| {labels[name]} | {_fmt_metric(result['metrics'][name])} | {interval} |")

    for title, key in (("误报", "false_positives"), ("漏报", "false_negatives")):
        lines.extend(["", f"## {title}", ""])
        rows = result[key]
        if not rows:
            lines.append("无。")
            continue
        lines.extend(["| ID | 来源 | 标题 | 审核备注 |", "|---:|---|---|---|"])
        for row in rows:
            title_text = str(row.get("title", "")).replace("|", "\\|")
            note = str(row.get("证据说明") or row.get("审核备注", "")).replace("|", "\\|")
            lines.append(f"| {row.get('id', '')} | {row.get('source', '')} | {title_text} | {note} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="计算 P2 人工标注质量指标")
    parser.add_argument("input", help="已填写审核结论的优先标注 CSV")
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "reports" / f"P2筛选质量评估_{date.today():%Y%m%d}.md"),
    )
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    args = parser.parse_args()

    input_path = Path(args.input)
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result = calculate_metrics(rows, iterations=args.bootstrap_iterations)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report(result, input_path), encoding="utf-8")
    print(
        f"已标注 {result['labeled_rows']}/{result['total_rows']}，"
        f"报告: {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
