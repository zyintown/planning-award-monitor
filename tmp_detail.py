#!/usr/bin/env python
"""查看候选记录的详细内容 — 只读。"""
import sqlite3

conn = sqlite3.connect("data/monitor.db")
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 候选 id 列表
candidate_ids = [898, 912, 922, 923, 892, 888]

for aid in candidate_ids:
    row = c.execute("SELECT id, title, source, status, publish_date, summary, raw_content, ai_reason FROM articles WHERE id = ?", (aid,)).fetchone()
    if not row:
        print(f"id={aid} 不存在")
        continue
    print("=" * 80)
    print(f"id={row['id']} | status={row['status']} | pub={row['publish_date']} | src={row['source']}")
    print(f"  title: {row['title']}")
    print(f"  ai_reason: {row['ai_reason']}")
    summary_len = len(row['summary'] or '')
    content_len = len(row['raw_content'] or '')
    print(f"  summary len={summary_len}, raw_content len={content_len}")
    if row['summary']:
        print(f"  summary[:200]: {row['summary'][:200]}")
    if row['raw_content']:
        print(f"  raw_content[:300]: {row['raw_content'][:300]}")
    print()

conn.close()
