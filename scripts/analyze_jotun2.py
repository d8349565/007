"""佐敦相关数据分析 - part2"""
import sys
sys.path.insert(0, '.')
from app.models.db import get_connection

conn = get_connection()

print("=== 3. 别名表结构 ===")
rows = conn.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchall()
for r in rows:
    if r[0] and ('alias' in r[0].lower() or 'entity' in r[0].lower()):
        print(r[0][:300])
        print()

print("=== 3b. 别名表内容 ===")
rows = conn.execute("SELECT * FROM entity_alias LIMIT 20").fetchall()
if rows:
    print("columns:", rows[0].keys())
    for r in rows:
        print(dict(r))
else:
    print("(空表)")

print("\n=== 4. 佐敦相关主体文本(所有状态) ===")
rows = conn.execute(
    "SELECT subject_text, review_status, COUNT(*) as cnt, "
    "SUM(CASE WHEN subject_entity_id IS NOT NULL THEN 1 ELSE 0 END) as lnk "
    "FROM fact_atom "
    "WHERE subject_text LIKE '%佐敦%' "
    "GROUP BY subject_text, review_status ORDER BY cnt DESC"
).fetchall()
for r in rows:
    print(f"  [{r[1]}] '{r[0]}' => {r[2]}条 已链接{r[3]}")

print("\n=== 5. 佐敦相关事实-已通过 ===")
rows = conn.execute(
    "SELECT f.fact_type, f.subject_text, f.predicate, f.object_text, "
    "f.value_num, f.unit, f.time_expr, "
    "CASE WHEN f.subject_entity_id IS NOT NULL THEN '链接' ELSE '未链接' END as lnk "
    "FROM fact_atom f "
    "WHERE (f.subject_text LIKE '%佐敦%' OR f.object_text LIKE '%佐敦%') "
    "AND f.review_status IN ('AUTO_PASS','HUMAN_PASS') "
    "ORDER BY f.time_expr DESC LIMIT 25"
).fetchall()
for r in rows:
    print(f"  [{r[7]}][{r[0]}] {r[1]} | {r[2]} | obj={r[3] or ''} | {r[4] or ''}{r[5] or ''} | {r[6] or ''}")

print("\n=== 6. 未链接事实主体 TOP20（已通过）===")
rows = conn.execute(
    "SELECT subject_text, COUNT(*) as cnt "
    "FROM fact_atom "
    "WHERE review_status IN ('AUTO_PASS','HUMAN_PASS') AND subject_entity_id IS NULL "
    "GROUP BY subject_text ORDER BY cnt DESC LIMIT 20"
).fetchall()
for r in rows:
    print(f"  '{r[0]}' => {r[1]}条")

print("\n=== 7. 实体表中的佐敦相关记录 ===")
rows = conn.execute(
    "SELECT id, canonical_name, entity_type FROM entity "
    "WHERE canonical_name LIKE '%佐敦%' OR canonical_name LIKE '%Jotun%' "
    "ORDER BY canonical_name"
).fetchall()
for r in rows:
    print(f"  [{r[2]}] id={r[0][:12]}  name='{r[1]}'")

print("\n=== 8. 各实体的已通过事实数 ===")
rows = conn.execute(
    "SELECT e.canonical_name, e.entity_type, COUNT(f.id) as cnt "
    "FROM entity e "
    "LEFT JOIN fact_atom f ON (f.subject_entity_id=e.id OR f.object_entity_id=e.id) "
    "AND f.review_status IN ('AUTO_PASS','HUMAN_PASS') "
    "GROUP BY e.id ORDER BY cnt DESC LIMIT 30"
).fetchall()
for r in rows:
    print(f"  [{r[1]}] {r[0]} => {r[2]}条")

conn.close()
