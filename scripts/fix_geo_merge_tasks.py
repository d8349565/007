"""验证地区括号规则，并自动拒绝跨地区法人 pending merge tasks。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.entity_merger import _extract_geo_paren, get_merge_suggestions
from app.models.db import get_connection

# 测试地区检测
tests = [
    ("中远佐敦船舶涂料（香港）有限公司", "中远佐敦船舶涂料（青岛）有限公司"),
    ("中远佐敦", "中远佐敦青岛工厂"),
    ("金刚化工（KCC）", "金刚化工"),
]
for a, b in tests:
    ga, gb = _extract_geo_paren(a), _extract_geo_paren(b)
    block = bool(ga and gb and ga != gb)
    print(f"屏蔽={block}  [{a}] geo={ga}  vs  [{b}] geo={gb}")

print()
sug = get_merge_suggestions(limit=20)
print("规则建议数:", len(sug))
for s in sug:
    print(" ", s["primary_name"], "<-", s["secondary_name"])

print()
conn = get_connection()
rows = conn.execute("""
    SELECT t.id, ep.canonical_name AS pname, es.canonical_name AS sname
    FROM entity_merge_task t
    JOIN entity ep ON t.primary_id=ep.id
    JOIN entity es ON t.secondary_id=es.id
    WHERE t.status='pending'
""").fetchall()

rejected = 0
for r in rows:
    ga = _extract_geo_paren(r["pname"])
    gb = _extract_geo_paren(r["sname"])
    if ga and gb and ga != gb:
        conn.execute("UPDATE entity_merge_task SET status=? WHERE id=?",
                     ("rejected", r["id"]))
        print(f"自动拒绝（不同地区法人）: [{r['pname']}] vs [{r['sname']}]")
        rejected += 1

conn.commit()
conn.close()
print(f"\n自动拒绝 {rejected} 条")
