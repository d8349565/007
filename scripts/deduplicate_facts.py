"""
检测并处理重复事实。

去重逻辑：按 (document_id, fact_type, subject_text, predicate, object_text) 分组，
同组内保留 confidence_score 最高的一条，其余标记为 REJECTED。

用法:
    python scripts/deduplicate_facts.py          # dry-run 报告
    python scripts/deduplicate_facts.py --apply   # 执行去重
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import get_connection
from app.logger import get_logger

logger = get_logger(__name__)


def find_duplicate_groups():
    """检测重复事实组（同文档内相同 fact_type+subject+predicate+object）"""
    conn = get_connection()
    try:
        groups = conn.execute('''
            SELECT document_id, fact_type, subject_text, predicate, object_text, COUNT(*) as cnt
            FROM fact_atom
            WHERE review_status != 'REJECTED'
            GROUP BY document_id, fact_type, subject_text, predicate, object_text
            HAVING COUNT(*) > 1
        ''').fetchall()
        return [dict(g) for g in groups]
    finally:
        conn.close()


def deduplicate(dry_run: bool = True) -> int:
    """
    去重：每组保留 confidence_score 最高的一条，其余标记为 REJECTED。

    返回去重记录数
    """
    groups = find_duplicate_groups()
    if not groups:
        print("未发现重复事实。")
        return 0

    print(f"发现 {len(groups)} 组重复事实：")
    for g in groups:
        print(f"  [{g['cnt']}x] {g['fact_type']} | {g['subject_text']} | {g['predicate']} | {g['object_text']}")

    if dry_run:
        total_dup = sum(g["cnt"] - 1 for g in groups)
        print(f"\n[DRY-RUN] 将标记 {total_dup} 条重复记录为 REJECTED（每组保留最高分1条）")
        return total_dup

    conn = get_connection()
    removed = 0
    try:
        for g in groups:
            facts = conn.execute('''
                SELECT id, confidence_score FROM fact_atom
                WHERE document_id=? AND fact_type=? AND subject_text IS ? AND predicate=? AND object_text IS ?
                  AND review_status != 'REJECTED'
                ORDER BY confidence_score DESC
            ''', (g['document_id'], g['fact_type'], g['subject_text'],
                  g['predicate'], g['object_text'])).fetchall()

            # 保留第一条（最高分），其余标记为 REJECTED
            for f in facts[1:]:
                conn.execute(
                    "UPDATE fact_atom SET review_status='REJECTED', review_note='重复抽取，已被去重' WHERE id=?",
                    (f['id'],)
                )
                removed += 1
                logger.info("去重: REJECTED fact %s (score=%.2f)", f['id'][:8], f['confidence_score'] or 0)

        conn.commit()
        print(f"\n已标记 {removed} 条重复记录为 REJECTED。")
    finally:
        conn.close()
    return removed


def main():
    parser = argparse.ArgumentParser(description="事实去重工具")
    parser.add_argument("--apply", action="store_true", help="执行去重（默认 dry-run）")
    args = parser.parse_args()

    deduplicate(dry_run=not args.apply)


if __name__ == '__main__':
    main()
