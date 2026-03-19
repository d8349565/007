"""数据质量检测脚本 - 验证报告中声称的问题"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import get_connection

def main():
    conn = get_connection()

    # 1. 重复事实检测
    dups = conn.execute('''
        SELECT fact_type, subject_text, predicate, object_text, COUNT(*) as cnt
        FROM fact_atom
        WHERE review_status != 'REJECTED'
        GROUP BY fact_type, subject_text, predicate, object_text
        HAVING COUNT(*) > 1
    ''').fetchall()
    print('=== 重复事实组 ===')
    for d in dups:
        print(f'  [{d["cnt"]}x] {d["fact_type"]} | {d["subject_text"]} | {d["predicate"]} | {d["object_text"]}')
    print(f'  总计: {len(dups)} 组')

    # 2. entity_id 链接率
    r = conn.execute('''
        SELECT
            SUM(CASE WHEN subject_entity_id IS NOT NULL THEN 1 ELSE 0 END) as sub_linked,
            SUM(CASE WHEN object_entity_id IS NOT NULL THEN 1 ELSE 0 END) as obj_linked,
            SUM(CASE WHEN location_entity_id IS NOT NULL THEN 1 ELSE 0 END) as loc_linked,
            COUNT(*) as total
        FROM fact_atom
    ''').fetchone()
    print(f'\n=== Entity 链接率 ===')
    print(f'  subject_entity_id: {r[0]}/{r[3]} = {r[0]/r[3]*100:.1f}%')
    print(f'  object_entity_id:  {r[1]}/{r[3]} = {r[1]/r[3]*100:.1f}%')
    print(f'  location_entity_id: {r[2]}/{r[3]} = {r[2]/r[3]*100:.1f}%')

    # 3. 空值率
    s = conn.execute('''
        SELECT
            SUM(CASE WHEN time_expr IS NULL OR time_expr='' THEN 1 ELSE 0 END) as null_time,
            SUM(CASE WHEN location_text IS NULL OR location_text='' THEN 1 ELSE 0 END) as null_loc,
            SUM(CASE WHEN currency IS NULL OR currency='' THEN 1 ELSE 0 END) as null_cur,
            COUNT(*) as total
        FROM fact_atom
    ''').fetchone()
    print(f'\n=== 空值率 ===')
    print(f'  time_expr:     {s[0]}/{s[3]} = {s[0]/s[3]*100:.1f}% null')
    print(f'  location_text: {s[1]}/{s[3]} = {s[1]/s[3]*100:.1f}% null')
    print(f'  currency:      {s[2]}/{s[3]} = {s[2]/s[3]*100:.1f}% null')

    # 4. unit 格式分布
    units = conn.execute(
        'SELECT unit, COUNT(*) as cnt FROM fact_atom WHERE unit IS NOT NULL AND unit != "" GROUP BY unit ORDER BY cnt DESC'
    ).fetchall()
    print(f'\n=== 单位格式 ({len(units)}种) ===')
    for u in units:
        print(f'  "{u["unit"]}" ({u["cnt"]})')

    # 5. qualifier_json 样本
    quals = conn.execute(
        'SELECT qualifier_json FROM fact_atom WHERE qualifier_json IS NOT NULL AND qualifier_json != "{}" AND qualifier_json != "null" LIMIT 15'
    ).fetchall()
    print(f'\n=== qualifier_json 样本 ({len(quals)}条非空) ===')
    for q in quals:
        print(f'  {q["qualifier_json"]}')

    # 6. object_text 样本
    objs = conn.execute(
        'SELECT DISTINCT object_text FROM fact_atom WHERE object_text IS NOT NULL AND object_text != "" LIMIT 20'
    ).fetchall()
    print(f'\n=== object_text 样本 ({len(objs)}条) ===')
    for o in objs:
        print(f'  {o["object_text"]}')

    # 7. location_text 样本
    locs = conn.execute(
        'SELECT DISTINCT location_text FROM fact_atom WHERE location_text IS NOT NULL AND location_text != "" LIMIT 20'
    ).fetchall()
    print(f'\n=== location_text 有值样本 ({len(locs)}条) ===')
    for l in locs:
        print(f'  {l["location_text"]}')

    # 8. time_expr 样本
    times = conn.execute(
        'SELECT DISTINCT time_expr FROM fact_atom WHERE time_expr IS NOT NULL AND time_expr != "" LIMIT 20'
    ).fetchall()
    print(f'\n=== time_expr 有值样本 ({len(times)}条) ===')
    for t in times:
        print(f'  {t["time_expr"]}')

    # 9. currency 样本
    curs = conn.execute(
        'SELECT DISTINCT currency FROM fact_atom WHERE currency IS NOT NULL AND currency != "" LIMIT 10'
    ).fetchall()
    print(f'\n=== currency 有值样本 ({len(curs)}条) ===')
    for c in curs:
        print(f'  {c["currency"]}')

    # 10. 总记录数和状态分布
    status = conn.execute(
        'SELECT review_status, COUNT(*) as cnt FROM fact_atom GROUP BY review_status ORDER BY cnt DESC'
    ).fetchall()
    print(f'\n=== review_status 分布 ===')
    for st in status:
        print(f'  {st["review_status"]}: {st["cnt"]}')

    conn.close()

if __name__ == '__main__':
    main()
