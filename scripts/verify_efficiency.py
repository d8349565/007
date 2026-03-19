"""验证效率分析报告中的数据"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.models.db import get_connection

conn = get_connection()

# 各阶段调用统计
stages = conn.execute('''
    SELECT task_type, COUNT(*) as cnt,
           SUM(input_tokens) as total_in, SUM(output_tokens) as total_out,
           CAST(AVG(input_tokens) AS INTEGER) as avg_in,
           CAST(AVG(output_tokens) AS INTEGER) as avg_out
    FROM extraction_task
    WHERE status = 'success'
    GROUP BY task_type
''').fetchall()
print('=== 各阶段调用统计 ===')
total_calls = 0; total_in = 0; total_out = 0
for s in stages:
    print(f"  {s['task_type']}: {s['cnt']}次, avg_in={s['avg_in']}, avg_out={s['avg_out']}, total_in={s['total_in']}, total_out={s['total_out']}")
    total_calls += s['cnt']
    total_in += s['total_in']
    total_out += s['total_out']
print(f"  合计: {total_calls}次, total_in={total_in}, total_out={total_out}, grand={total_in+total_out}")

# fact 产出
facts = conn.execute('SELECT COUNT(*) FROM fact_atom').fetchone()[0]
print(f"\nfact 总数: {facts}")
print(f"Token/Fact: {(total_in+total_out)/facts:.0f}")

# chunk 和 evidence 数
chunks = conn.execute('SELECT COUNT(*) FROM document_chunk').fetchone()[0]
evidences = conn.execute('SELECT COUNT(*) FROM evidence_span').fetchone()[0]
print(f"chunks: {chunks}, evidences: {evidences}")

# reviewer 费用分析（基于 config pricing）
# input_cached: 0.2, input_uncached: 2.0, output: 3.0 (元/百万tokens)
reviewer_data = conn.execute('''
    SELECT SUM(input_tokens) as tin, SUM(output_tokens) as tout, COUNT(*) as cnt
    FROM extraction_task WHERE task_type='reviewer' AND status='success'
''').fetchone()
if reviewer_data and reviewer_data['tin']:
    cost_in = reviewer_data['tin'] / 1_000_000 * 2.0
    cost_out = reviewer_data['tout'] / 1_000_000 * 3.0
    print(f"\nreviewer: {reviewer_data['cnt']}次, in={reviewer_data['tin']}, out={reviewer_data['tout']}")
    print(f"  费用: input ¥{cost_in:.4f} + output ¥{cost_out:.4f} = ¥{cost_in+cost_out:.4f}")
    print(f"  占比: {(cost_in+cost_out):.4f}")

# evidence_finder 各 chunk 的 token 明细
ef_details = conn.execute('''
    SELECT input_tokens, output_tokens FROM extraction_task
    WHERE task_type='evidence_finder' AND status='success'
    ORDER BY started_at
''').fetchall()
print("\n=== evidence_finder 各 chunk 明细 ===")
for i, r in enumerate(ef_details):
    print(f"  chunk_{i+1}: in={r['input_tokens']}, out={r['output_tokens']}")

conn.close()
