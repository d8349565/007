"""深入分析佐敦相关数据：所有事实原子 + 源文档"""
import sys
import json
sys.path.insert(0, '.')
from app.models.db import get_connection

conn = get_connection()

# 1. 佐敦相关源文档
print("=" * 80)
print("1. 佐敦相关源文档")
print("=" * 80)
rows = conn.execute("""
    SELECT DISTINCT sd.id, sd.title, sd.publish_time, sd.status, 
           LENGTH(sd.raw_text) as text_len
    FROM source_document sd
    WHERE sd.raw_text LIKE '%佐敦%' OR sd.title LIKE '%佐敦%'
    ORDER BY sd.publish_time DESC
""").fetchall()
for r in rows:
    print(f"  [{r[3]}] {r[1][:60]} | 发布:{r[2]} | 长度:{r[4]} | id={r[0][:12]}")
print(f"  共 {len(rows)} 篇文档")

# 2. 所有佐敦相关事实原子（含详细字段）
print()
print("=" * 80)
print("2. 佐敦相关事实原子（前50条，所有状态）")
print("=" * 80)
rows = conn.execute("""
    SELECT f.id, f.fact_type, f.subject_text, f.predicate, f.object_text,
           f.value_num, f.value_text, f.unit, f.currency,
           f.time_expr, f.location_text, f.qualifier_json,
           f.confidence_score, f.review_status, f.review_note,
           f.subject_entity_id, f.object_entity_id,
           sd.title as doc_title
    FROM fact_atom f
    LEFT JOIN source_document sd ON f.document_id = sd.id
    WHERE f.subject_text LIKE '%佐敦%' OR f.object_text LIKE '%佐敦%'
       OR f.subject_text LIKE '%Jotun%' OR f.object_text LIKE '%Jotun%'
    ORDER BY f.review_status, f.fact_type, f.time_expr DESC
    LIMIT 50
""").fetchall()

for i, r in enumerate(rows):
    print(f"\n--- 事实 #{i+1} [{r[13]}] score={r[12]} ---")
    print(f"  类型: {r[1]}")
    print(f"  主体: {r[2]}")
    print(f"  谓词: {r[3]}")
    print(f"  客体: {r[4]}")
    print(f"  数值: {r[5]} | 原文: {r[6]} | 单位: {r[7]} | 币种: {r[8]}")
    print(f"  时间: {r[9]} | 地点: {r[10]}")
    quals = r[11] if r[11] else '{}'
    print(f"  限定: {quals}")
    print(f"  审核备注: {r[14]}")
    print(f"  来源文档: {r[17][:50] if r[17] else 'N/A'}")
    print(f"  subject_entity_id: {r[15][:12] if r[15] else 'NULL'}")
    print(f"  id: {r[0][:12]}")

print(f"\n共显示 {len(rows)} 条")

# 3. 按审核状态统计
print()
print("=" * 80)
print("3. 佐敦事实审核状态分布")
print("=" * 80)
rows = conn.execute("""
    SELECT review_status, COUNT(*) as cnt, 
           AVG(confidence_score) as avg_score
    FROM fact_atom
    WHERE subject_text LIKE '%佐敦%' OR object_text LIKE '%佐敦%'
    GROUP BY review_status
    ORDER BY cnt DESC
""").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}条, 平均置信度={r[2]:.3f}")

# 4. 按事实类型统计
print()
print("=" * 80)
print("4. 佐敦事实类型分布（自动通过的）")
print("=" * 80)
rows = conn.execute("""
    SELECT fact_type, COUNT(*) as cnt
    FROM fact_atom
    WHERE (subject_text LIKE '%佐敦%' OR object_text LIKE '%佐敦%')
      AND review_status = '自动通过'
    GROUP BY fact_type
    ORDER BY cnt DESC
""").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}条")

# 5. 检查被拒绝的原因
print()
print("=" * 80)
print("5. 被拒绝事实的审核备注")
print("=" * 80)
rows = conn.execute("""
    SELECT f.subject_text, f.predicate, f.fact_type, f.review_note, 
           f.confidence_score, f.value_num, f.value_text, f.unit
    FROM fact_atom f
    WHERE (f.subject_text LIKE '%佐敦%' OR f.object_text LIKE '%佐敦%')
      AND f.review_status IN ('已拒绝', '人工拒绝')
    ORDER BY f.fact_type
""").fetchall()
for r in rows:
    print(f"  [{r[2]}] {r[0]} | {r[1]} | 值:{r[5]}{r[7] or ''} | score={r[4]}")
    print(f"    拒绝原因: {r[3]}")

# 6. 检查重复标记的事实
print()
print("=" * 80)
print("6. 重复标记的事实")
print("=" * 80)
rows = conn.execute("""
    SELECT f.subject_text, f.predicate, f.fact_type, f.value_num, f.unit, 
           f.time_expr, f.review_note
    FROM fact_atom f
    WHERE (f.subject_text LIKE '%佐敦%' OR f.object_text LIKE '%佐敦%')
      AND f.review_status = '重复'
""").fetchall()
for r in rows:
    print(f"  [{r[2]}] {r[0]} | {r[1]} | {r[3]}{r[4] or ''} | {r[5]}")
    print(f"    备注: {r[6]}")

# 7. 查看原始文档片段（找一篇佐敦文章看看内容）
print()
print("=" * 80)
print("7. 第一篇佐敦文档的原始内容（前2000字）")
print("=" * 80)
row = conn.execute("""
    SELECT sd.raw_text, sd.title
    FROM source_document sd
    WHERE sd.raw_text LIKE '%佐敦%' OR sd.title LIKE '%佐敦%'
    ORDER BY sd.publish_time DESC
    LIMIT 1
""").fetchone()
if row:
    print(f"标题: {row[1]}")
    print(f"内容: {row[0][:2000]}")

conn.close()
