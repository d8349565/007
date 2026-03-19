"""验证回填结果"""
import sys
sys.path.insert(0, '.')
from app.models.db import get_connection
conn = get_connection()
passed = conn.execute("SELECT COUNT(*) FROM fact_atom WHERE review_status IN ('AUTO_PASS','HUMAN_PASS')").fetchone()[0]
linked = conn.execute("SELECT COUNT(*) FROM fact_atom WHERE review_status IN ('AUTO_PASS','HUMAN_PASS') AND subject_entity_id IS NOT NULL").fetchone()[0]
print(f"已通过: {passed}, 已链接: {linked}, 未链接: {passed-linked}")
rows = conn.execute(
    "SELECT subject_text, COUNT(*) as cnt, "
    "SUM(CASE WHEN subject_entity_id IS NOT NULL THEN 1 ELSE 0 END) as lnk "
    "FROM fact_atom WHERE subject_text LIKE '%佐敦%' "
    "AND review_status IN ('AUTO_PASS','HUMAN_PASS') "
    "GROUP BY subject_text ORDER BY cnt DESC"
).fetchall()
print("佐敦相关链接情况:")
for r in rows:
    print(f"  {r[1]}条 其中已链接{r[2]}条  '{r[0]}'")
conn.close()
