"""只读基准测试本地 Ollama 在不同并发数下的吞吐、错误和判断一致性。"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from statistics import median

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawlers.base import Article
from filters.ai_filter import AIFilter


def load_samples(db_path: Path, sample_size: int) -> list[Article]:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, title, url, source, publish_date, summary, raw_content
            FROM articles
            WHERE status IN ('ai_confirmed', 'ai_rejected', 'pushed')
            ORDER BY CASE status WHEN 'pushed' THEN 0 WHEN 'ai_rejected' THEN 1 ELSE 2 END,
                     id
            LIMIT ?
            """,
            (sample_size,),
        ).fetchall()
    finally:
        connection.close()
    return [
        Article(
            title=row["title"],
            url=row["url"],
            source=row["source"],
            publish_date=row["publish_date"] or "",
            summary=row["summary"] or "",
            raw_content=row["raw_content"] or "",
            db_id=int(row["id"]),
        )
        for row in rows
    ]


def choose_recommended_workers(results: list[dict], minimum_speedup: float = 1.3) -> int:
    if not results:
        return 1
    baseline = next((result for result in results if result["workers"] == 1), None)
    if (
        not baseline
        or baseline["errors"]
        or baseline["invalid"]
        or not baseline.get("stable", True)
    ):
        return 1
    baseline_decisions = baseline["decisions"]
    eligible = [baseline]
    for result in results:
        if result["workers"] == 1:
            continue
        if result["errors"] or result["invalid"] or not result.get("stable", True):
            continue
        if result["decisions"] != baseline_decisions:
            continue
        if result["speedup"] >= minimum_speedup:
            eligible.append(result)
    passing = [result for result in eligible if result["workers"] != 1]
    if not passing:
        return 1
    return min(result["workers"] for result in passing)


def run_benchmark(
    articles: list[Article],
    ai_config: dict,
    worker_options: list[int],
    repeats: int = 2,
) -> dict:
    if not articles:
        raise ValueError("数据库中没有可用于基准测试的 AI 阶段样本")

    common = {
        "api_url": ai_config.get("api_url", "http://localhost:11434/api/chat"),
        "model": ai_config.get("model", "qwen3.5:latest"),
        "max_summary_length": ai_config.get("max_summary_length", 500),
        "timeout": ai_config.get("timeout", 60),
        "enabled": True,
    }
    warmup = AIFilter(**common, max_workers=1).judge(articles[0])
    if warmup.decision is None:
        raise RuntimeError(f"AI预热失败: {warmup.reason}")

    worker_options = sorted(set(worker_options), key=lambda workers: (workers != 1, workers))
    results = []
    baseline_seconds = None
    for workers in worker_options:
        ai_filter = AIFilter(**common, max_workers=workers)
        trials = []
        for _ in range(repeats):
            started = time.monotonic()
            judged = ai_filter.batch_judge(articles)
            elapsed = time.monotonic() - started
            trials.append(
                {
                    "elapsed_seconds": elapsed,
                    "decisions": [result.decision for _, result in judged],
                    "errors": sum(
                        result.error_kind == "transport" for _, result in judged
                    ),
                    "invalid": sum(
                        result.error_kind == "invalid_response" for _, result in judged
                    ),
                }
            )
        elapsed = median(trial["elapsed_seconds"] for trial in trials)
        decisions = trials[0]["decisions"]
        errors = sum(trial["errors"] for trial in trials)
        invalid = sum(trial["invalid"] for trial in trials)
        stable = all(trial["decisions"] == decisions for trial in trials)
        if workers == 1:
            baseline_seconds = elapsed
        results.append(
            {
                "workers": workers,
                "elapsed_seconds": round(elapsed, 3),
                "throughput": round(len(articles) / elapsed, 3),
                "speedup": round((baseline_seconds or elapsed) / elapsed, 3),
                "errors": errors,
                "invalid": invalid,
                "stable": stable,
                "decisions": decisions,
                "trial_seconds": [
                    round(trial["elapsed_seconds"], 3) for trial in trials
                ],
            }
        )

    recommendation = choose_recommended_workers(results)
    return {
        "model": common["model"],
        "sample_size": len(articles),
        "repeats": repeats,
        "minimum_speedup": 1.3,
        "results": results,
        "recommended_max_workers": recommendation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="测试 Ollama 1/2/4 路并发性能")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--db", default=str(ROOT / "data" / "monitor.db"))
    parser.add_argument("--sample-size", type=int, default=6)
    parser.add_argument("--workers", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output")
    args = parser.parse_args()

    if any(worker < 1 or worker > 4 for worker in args.workers):
        parser.error("workers 必须在1到4之间")
    if 1 not in args.workers:
        parser.error("workers 必须包含1作为基线")
    if args.repeats < 1:
        parser.error("repeats 必须是正整数")

    with Path(args.config).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    ai_config = config.get("filter", {}).get("ai", {})
    articles = load_samples(Path(args.db), args.sample_size)
    result = run_benchmark(articles, ai_config, args.workers, repeats=args.repeats)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
