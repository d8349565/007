"""佐敦相关数据分析脚本"""
import sys
sys.path.insert(0, '.')
from app.models.db import get_connection

conn = get_connection()

print("=== 1. 基础统计 ===")
total = conn.execute("SELECT COUNT(*) FROM fact_atom").fetchone()[0]
passed = conn.execute("SELECT COUNT(*) FROM fact_atom WHERE review_status IN ('AUTO_PASS','HUMAN_PASS')").fetchone()[0]
linked = conn.execute("SELECT COUNT(*) FROM fact_atom WHERE review_status IN ('AUTO_PASS','HUMAN_PASS') AND subject_entity_id IS NOT NULL").fetchone()[0]
entities = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
aliases = conn.execute("SELECT COUNT(*) FROM entity_alias").fetchone()[0]
print(f"总事实: {total}, 已通过: {passed}, 已链接: {linked}, 未链接: {passed-linked}")
print(f"实体数: {entities}, 别名数: {aliases}")

print("\n=== 2. 实体表内容 ===")
rows = conn.execute("SELECT id, canonical_name, entity_type FROM entity ORDER BY canonical_name").fetchall()
for r in rows:
    print(f"  [{r[2]}] {r[1]}  (id={r[0][:8]}...)")

print("\n=== 3. 别名表内容 ===")
rows = conn.execute("""
    SELECT ea.alias, e.canonical_name, e.entity_type 
    FROM entity_alias ea JOIN entity e ON ea.entity_id=e.id 
    ORDER BY e.canonical_name
""").fetchall()
for r in rows:
    print(f"  '{r[0]}' -> [{r[2]}] {r[1]}")

print("\n=== 4. 佐敦相关主体文本（所有状态）===")
rows = conn.execute("""
    SELECT subject_text, review_status, COUNT(*) as cnt,
           SUM(CASE WHEN subject_entity_id IS NOT NULL THEN 1 ELSE 0 END) as linked
    FROM fact_atom 
    WHERE subject_text LIKE '%佐敦%' OR subject_text LIKE '%Jotun%' OR subject_text LIKE '%jotun%'
    GROUP BY subject_text, review_status
    ORDER BY cnt DESC
""").fetchall()
for r in rows:
    print(f"  [{r[1]}] '{r[0]}' => {r[2]}条, 已链接{r[3]}条")

print("\n=== 5. 佐敦相关事实（已通过，最近20条）===")
rows = conn.execute("""
    SELECT f.fact_type, f.subject_text, f.predicate, f.object_text,
           f.value_num, f.unit, f.time_expr, f.subject_entity_id,
           sd.title as doc_title
    FROM fact_atom f
    LEFT JOIN source_document sd ON f.document_id = sd.id
    WHERE (f.subject_text LIKE '%佐敦%' OR f.object_text LIKE '%佐敦%')
      AND f.review_status IN ('AUTO_PASS','HUMAN_PASS')
    ORDER BY f.time_expr DESC
    LIMIT 20
""").fetchall()
for r in rows:
    entity_status = "✓链接" if r[7] else "✗未链接"
    print(f"  {entity_status} [{r[0]}] {r[1]} | {r[2]} | {r[3] or ''} | {r[4] or ''}{r[5] or ''} | {r[6] or ''}")

print("\n=== 6. 未链接事实的主体文本 TOP30（已通过）===")
rows = conn.execute("""
    SELECT subject_text, COUNT(*) as cnt
    FROM fact_atom
    WHERE review_status IN ('AUTO_PASS','HUMAN_PASS') AND subject_entity_id IS NULL
    GROUP BY subject_text
    ORDER BY cnt DESC
    LIMIT 30
""").fetchall()
for r in rows:
    print(f"  '{r[0]}' => {r[1]}条")

print("\n=== 7. 主体文本与实体名称相似度检查（已通过未链接）===")
# 列出所有实体名，检查主体文本是否应该能匹配
entity_names = [r[0] for r in conn.execute("SELECT canonical_name FROM entity").fetchall()]
alias_names = [r[0] for r in conn.execute("SELECT alias FROM entity_alias").fetchall()]
all_names = set(entity_names + alias_names)

rows = conn.execute("""
    SELECT DISTINCT subject_text
    FROM fact_atom
    WHERE review_status IN ('AUTO_PASS','HUMAN_PASS') AND subject_entity_id IS NULL
    ORDER BY subject_text
""").fetchall()
print(f"  (当前实体/别名库: {all_names})")
print(f"  未链接主体文本({len(rows)}个):")
for r in rows:
    subj = r[0] or ''
    match = any(name in subj or subj in name for name in all_names)
    flag = "≈可匹配" if match else "✗无匹配"
    print(f"    {flag} '{subj}'")

conn.close()
