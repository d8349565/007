"""分析每个 evidence 下的 fact 数量分布"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.models.db import get_connection

conn = get_connection()
dist = conn.execute('''
    SELECT fact_count, COUNT(*) as evidence_count
    FROM (
        SELECT evidence_span_id, COUNT(*) as fact_count
        FROM fact_atom
        GROUP BY evidence_span_id
    )
    GROUP BY fact_count
    ORDER BY fact_count
''').fetchall()
print("=== facts per evidence 分布 ===")
total_ev = 0; total_facts = 0
for d in dist:
    print(f"  {d['fact_count']} facts: {d['evidence_count']} evidences")
    total_ev += d['evidence_count']
    total_facts += d['fact_count'] * d['evidence_count']

# 如果批量审核（每 evidence 一次调用），调用次数
print(f"\n当前 reviewer 调用: {total_facts}次（逐条）")
print(f"批量后 reviewer 调用: {total_ev}次（每 evidence 一次）")
print(f"减少: {total_facts - total_ev}次 ({(total_facts - total_ev) / total_facts * 100:.0f}%)")
conn.close()
