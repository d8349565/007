"""
查看 object_entity_id 填充情况及有 object_text 的事实分布
"""
import sys
sys.path.insert(0, '.')
from app.models.db import get_connection
conn = get_connection()

# object_entity_id 状态
no_obj_eid = conn.execute(
    "SELECT COUNT(*) FROM fact_atom WHERE review_status IN ('AUTO_PASS','HUMAN_PASS') AND object_entity_id IS NULL AND object_text IS NOT NULL AND object_text != ''"
).fetchone()[0]
has_obj_eid = conn.execute(
    "SELECT COUNT(*) FROM fact_atom WHERE review_status IN ('AUTO_PASS','HUMAN_PASS') AND object_entity_id IS NOT NULL"
).fetchone()[0]
no_obj_text = conn.execute(
    "SELECT COUNT(*) FROM fact_atom WHERE review_status IN ('AUTO_PASS','HUMAN_PASS') AND (object_text IS NULL OR object_text = '')"
).fetchone()[0]
print(f"已通过事实 object 分布:")
print(f"  object_entity_id 已填:        {has_obj_eid}")
print(f"  有 object_text 但未链接 ID:   {no_obj_eid}")
print(f"  无 object_text (纯数值型):    {no_obj_text}")

# 有 object_text 但未链接的 top20
rows = conn.execute(
    "SELECT object_text, COUNT(*) as cnt FROM fact_atom "
    "WHERE review_status IN ('AUTO_PASS','HUMAN_PASS') AND object_entity_id IS NULL "
    "AND object_text IS NOT NULL AND object_text != '' "
    "GROUP BY object_text ORDER BY cnt DESC LIMIT 20"
).fetchall()
print(f"\n有 object_text 但 object_entity_id 未填 top20:")
for r in rows:
    print(f"  '{r[0]}' => {r[1]}条")

conn.close()
