#!/usr/bin/env python
"""检查 dry-run 数据库的 P3 管线结果 — 聚焦关键指标。"""
import sqlite3
import sys
from datetime import datetime

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "data/dry-runs/monitor-dry-run-20260710-165125.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print(f"Dry-run DB: {DB_PATH}")
    print(f"时间: {datetime.now().isoformat()}")
    print("=" * 80)

    # 1. 总数和状态
    c.execute("SELECT COUNT(*) FROM articles")
    print(f"\n[Articles] 总数 = {c.fetchone()[0]}")
    c.execute("SELECT status, COUNT(*) as cnt FROM articles GROUP BY status ORDER BY cnt DESC")
    print("[Articles by status]")
    for row in c.fetchall():
        print(f"  {row['status']}: {row['cnt']}")

    # 2. source_runs（全部）
    print("\n[source_runs] 全部渠道")
    c.execute("""SELECT source, source_type, status, article_count, new_count,
                        detail_success, detail_failed, duration_seconds, error
                 FROM source_runs ORDER BY source""")
    rows = c.fetchall()
    print(f"  共 {len(rows)} 条记录")
    for row in rows:
        print(f"  {row['source']} ({row['source_type']})")
        print(f"    status={row['status']} | articles={row['article_count']} new={row['new_count']} | detail_ok={row['detail_success']} detail_fail={row['detail_failed']} | dur={row['duration_seconds']}s")
        if row['error']:
            print(f"    error: {row['error'][:200]}")

    # 3. article_extractions
    c.execute("SELECT COUNT(*) FROM article_extractions")
    print(f"\n[article_extractions] 总数 = {c.fetchone()[0]}")

    # 4. run_logs
    print("\n[run_logs] 最近一条")
    c.execute("""SELECT id, run_time, finished_at, status, duration_seconds,
                        total_articles, new_articles, keyword_passed,
                        ai_confirmed, pushed, errors
                 FROM run_logs ORDER BY id DESC LIMIT 1""")
    row = c.fetchone()
    if row:
        print(f"  id={row['id']} | run_time={row['run_time']} | finished={row['finished_at']} | status={row['status']} | dur={row['duration_seconds']}s")
        print(f"  total={row['total_articles']} new={row['new_articles']} kw_passed={row['keyword_passed']} ai_confirmed={row['ai_confirmed']} pushed={row['pushed']}")
        if row['errors']:
            print(f"  errors: {row['errors'][:500]}")

    # 5. 新增文章（id > 1053）
    print("\n[新增文章] id > 1053")
    c.execute("""SELECT id, title, source, status, ai_reason, publish_date
                 FROM articles WHERE id > 1053 ORDER BY id""")
    rows = c.fetchall()
    print(f"  共 {len(rows)} 条")
    for row in rows:
        print(f"  id={row['id']} | status={row['status']} | pub={row['publish_date']} | src={row['source']}")
        print(f"    title: {row['title'][:120]}")
        if row['ai_reason']:
            print(f"    reason: {row['ai_reason'][:200]}")

    # 6. 标题门禁拒绝统计
    print("\n[P3标题门禁拒绝原因]")
    c.execute("""SELECT ai_reason, COUNT(*) as cnt
                 FROM articles WHERE ai_reason LIKE 'P3标题门禁%'
                 GROUP BY ai_reason ORDER BY cnt DESC""")
    for row in c.fetchall():
        print(f"  [{row['cnt']}] {row['ai_reason']}")

    # 7. 其他拒绝原因
    print("\n[其他拒绝原因]")
    c.execute("""SELECT ai_reason, COUNT(*) as cnt
                 FROM articles WHERE status = 'keyword_rejected' AND ai_reason NOT LIKE 'P3标题门禁%'
                 GROUP BY ai_reason ORDER BY cnt DESC""")
    for row in c.fetchall():
        print(f"  [{row['cnt']}] {row['ai_reason']}")

    conn.close()
    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()
