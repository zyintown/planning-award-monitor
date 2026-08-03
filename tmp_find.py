#!/usr/bin/env python
"""从历史记录中查找报奖申报通知候选 — 只读。"""
import sqlite3

conn = sqlite3.connect("data/monitor.db")
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 找标题含"申报"+"奖"或"竞赛"+"通知"的记录，排除已推送的
print("=" * 80)
print("[报奖申报通知候选] 标题含(申报/推荐/提名/参评/报名) + (奖/竞赛/大赛/评优)")
print("=" * 80)

rows = c.execute("""
    SELECT id, title, source, status, publish_date, created_at
    FROM articles
    WHERE (title LIKE '%申报%' OR title LIKE '%推荐%' OR title LIKE '%提名%'
           OR title LIKE '%参评%' OR title LIKE '%报名%')
      AND (title LIKE '%奖%' OR title LIKE '%竞赛%' OR title LIKE '%大赛%' OR title LIKE '%评优%')
      AND status != 'pushed'
    ORDER BY id
""").fetchall()

print(f"\n共 {len(rows)} 条候选:")
for r in rows:
    print(f"  id={r['id']} | status={r['status']} | pub={r['publish_date']} | src={r['source']}")
    print(f"    {r['title'][:120]}")

print()
print("=" * 80)
print("[也看看 pushed 的，作为正面参考]")
print("=" * 80)
rows2 = c.execute("""
    SELECT id, title, source, status, publish_date
    FROM articles
    WHERE status = 'pushed'
    ORDER BY id DESC
""").fetchall()
for r in rows2:
    print(f"  id={r['id']} | pub={r['publish_date']} | src={r['source']}")
    print(f"    {r['title'][:120]}")

conn.close()
