"""修正 entity_merge_task 中主副方向反转的记录（primary 应为较短/简称方）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.models.db import get_connection

conn = get_connection()
tasks = conn.execute("""
    SELECT t.id, t.primary_id, t.secondary_id,
           ep.canonical_name AS pname, es.canonical_name AS sname
    FROM entity_merge_task t
    JOIN entity ep ON t.primary_id = ep.id
    JOIN entity es ON t.secondary_id = es.id
    WHERE t.status='pending'
""").fetchall()

fixed = 0
for t in tasks:
    pname, sname = t["pname"], t["sname"]
    plen = len(pname.replace(" ", ""))
    slen = len(sname.replace(" ", ""))
    # 主实体名比副实体名长，且含括号 → 方向反了
    if plen > slen and ("（" in pname or "(" in pname):
        conn.execute(
            "UPDATE entity_merge_task SET primary_id=?, secondary_id=? WHERE id=?",
            (t["secondary_id"], t["primary_id"], t["id"]),
        )
        print(f"  修正: [{sname}] 主 ← [{pname}] 副")
        fixed += 1
    else:
        print(f"  正确: [{pname}] 主 ← [{sname}] 副")

conn.commit()
conn.close()
print(f"\n共修正 {fixed} 条任务方向")
