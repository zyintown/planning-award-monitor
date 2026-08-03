#!/usr/bin/env python
"""P3 正式运行观察脚本 — 只读检查，不修改任何数据。"""
import sqlite3
from datetime import datetime

DB_PATH = "data/monitor.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print(f"P3 正式运行观察 — {datetime.now().isoformat()}")
    print("=" * 80)

    # 1. 基本状态
    c.execute("PRAGMA user_version")
    print(f"\n[Schema] user_version = {c.fetchone()[0]}")
    c.execute("SELECT COUNT(*) FROM articles")
    print(f"[Articles] 总数 = {c.fetchone()[0]}")
    c.execute("SELECT status, COUNT(*) as cnt FROM articles GROUP BY status ORDER BY cnt DESC")
    print("[Articles by status]")
    for row in c.fetchall():
        print(f"  {row['status']}: {row['cnt']}")

    # 2. run_logs 最近 5 条
    print("\n[run_logs] 最近 5 条")
    c.execute("""SELECT id, run_time, finished_at, status, duration_seconds,
                        total_articles, new_articles, keyword_passed,
                        ai_confirmed, pushed, errors
                 FROM run_logs ORDER BY id DESC LIMIT 5""")
    for row in c.fetchall():
        print(f"  id={row['id']} | run_time={row['run_time']} | finished={row['finished_at']} | status={row['status']} | dur={row['duration_seconds']}s")
        print(f"    total={row['total_articles']} new={row['new_articles']} kw_passed={row['keyword_passed']} ai_confirmed={row['ai_confirmed']} pushed={row['pushed']}")
        if row['errors']:
            print(f"    errors: {row['errors'][:500]}")

    # 3. source_runs — 最近一次运行
    print("\n[source_runs] 最近一次运行 (run_id = MAX)")
    c.execute("""SELECT sr.run_id, sr.source, sr.source_type, sr.status,
                        sr.article_count, sr.new_count, sr.detail_success, sr.detail_failed,
                        sr.duration_seconds, sr.error
                 FROM source_runs sr
                 WHERE sr.run_id = (SELECT MAX(id) FROM run_logs)
                 ORDER BY sr.source""")
    rows = c.fetchall()
    print(f"  共 {len(rows)} 条")
    for row in rows:
        print(f"  {row['source']} ({row['source_type']})")
        print(f"    status={row['status']} | articles={row['article_count']} new={row['new_count']} | detail_ok={row['detail_success']} detail_fail={row['detail_failed']} | dur={row['duration_seconds']}s")
        if row['error']:
            print(f"    error: {row['error'][:200]}")

    # 4. article_extractions
    c.execute("SELECT COUNT(*) FROM article_extractions")
    print(f"\n[article_extractions] 总数 = {c.fetchone()[0]}")

    # 5. 新增文章 (id > 1053)
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

    # 6. P3 标题门禁拒绝统计
    print("\n[P3标题门禁拒绝原因]")
    c.execute("""SELECT ai_reason, COUNT(*) as cnt
                 FROM articles WHERE ai_reason LIKE 'P3标题门禁%'
                 GROUP BY ai_reason ORDER BY cnt DESC""")
    rows = c.fetchall()
    if not rows:
        print("  (无)")
    else:
        for row in rows:
            print(f"  [{row['cnt']}] {row['ai_reason']}")

    # 7. 其他拒绝原因
    print("\n[其他拒绝原因]")
    c.execute("""SELECT ai_reason, COUNT(*) as cnt
                 FROM articles WHERE status = 'keyword_rejected' AND ai_reason NOT LIKE 'P3标题门禁%'
                 GROUP BY ai_reason ORDER BY cnt DESC""")
    rows = c.fetchall()
    if not rows:
        print("  (无)")
    else:
        for row in rows:
            print(f"  [{row['cnt']}] {row['ai_reason']}")

    # 8. pushed 记录
    print("\n[已推送记录]")
    c.execute("""SELECT id, title, source, created_at, pushed_at
                 FROM articles WHERE status = 'pushed' ORDER BY id DESC""")
    rows = c.fetchall()
    print(f"  共 {len(rows)} 条")
    for row in rows[:5]:
        print(f"  id={row['id']} | created={row['created_at']} | pushed={row['pushed_at']} | {row['source']}")
        print(f"    {row['title'][:100]}")

    conn.close()
    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()
