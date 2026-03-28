"""对存量事实原子执行全局去重。

使用 deduplicator 服务的指纹算法，对已入库的所有活跃 fact_atom 执行：
1. 全局指纹去重（同文档 + 跨文档）
2. 跨类型去重（无金额 INVESTMENT ↔ EXPANSION）

去重策略：保留 confidence 最高的一条为正本，其余标记 DUPLICATE，
review_note 记录正本 ID，evidence_span 关联不变。

用法:
    python scripts/apply_dedup_existing.py          # dry-run 报告
    python scripts/apply_dedup_existing.py --apply   # 执行去重
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import get_connection
from app.logger import get_logger
from app.services.deduplicator import build_dedup_key, normalize_subject

logger = get_logger(__name__)

_ACTIVE_FILTER = "review_status NOT IN ('REJECTED', 'DUPLICATE', 'HUMAN_REJECTED')"


def deduplicate_global(dry_run: bool = True) -> int:
    """全局指纹去重：相同 dedup_key 的事实只保留最高分一条"""
    conn = get_connection()
    try:
        facts = conn.execute(
            f"""SELECT id, document_id, fact_type, subject_text, predicate,
                       object_text, value_num, value_text, time_expr,
                       location_text, qualifier_json, confidence_score
            FROM fact_atom
            WHERE {_ACTIVE_FILTER}
            ORDER BY confidence_score DESC, created_at ASC"""
        ).fetchall()

        seen = {}       # dedup_key → canonical fact id
        duplicates = [] # (dup_id, canonical_id, fact_type, subject)

        for f in facts:
            key = build_dedup_key(dict(f))
            if key in seen:
                duplicates.append((
                    f["id"], seen[key],
                    f["fact_type"], f["subject_text"] or "?",
                ))
            else:
                seen[key] = f["id"]

        print(f"活跃事实 {len(facts)} 条，发现 {len(duplicates)} 条指纹重复：\n")
        for dup_id, canon_id, ft, subj in duplicates:
            print(f"  DUPLICATE [{ft}] {subj} — {dup_id[:8]} → 正本 {canon_id[:8]}")

        if dry_run:
            print(f"\n[DRY-RUN] 将标记 {len(duplicates)} 条为 DUPLICATE")
            return len(duplicates)

        for dup_id, canon_id, ft, subj in duplicates:
            conn.execute(
                "UPDATE fact_atom SET review_status='DUPLICATE', review_note=? WHERE id=?",
                (f"重复: 与 {canon_id} 重复（全局去重）", dup_id),
            )
        conn.commit()
        print(f"\n已标记 {len(duplicates)} 条为 DUPLICATE")
        return len(duplicates)
    finally:
        conn.close()


def deduplicate_cross_type_global(dry_run: bool = True) -> int:
    """跨类型去重：无金额 INVESTMENT 且与同文档同主体 EXPANSION 重叠"""
    conn = get_connection()
    try:
        investments = conn.execute(
            f"""SELECT id, document_id, subject_text, location_text
            FROM fact_atom
            WHERE fact_type = 'INVESTMENT' AND value_num IS NULL
              AND {_ACTIVE_FILTER}"""
        ).fetchall()

        duplicates = []  # (inv_id, exp_id, subject)
        for inv in investments:
            norm_subj = normalize_subject(inv["subject_text"])
            expansions = conn.execute(
                f"""SELECT id, subject_text FROM fact_atom
                WHERE fact_type = 'EXPANSION' AND document_id = ?
                  AND {_ACTIVE_FILTER}""",
                (inv["document_id"],),
            ).fetchall()

            for exp in expansions:
                if normalize_subject(exp["subject_text"]) == norm_subj:
                    duplicates.append((
                        inv["id"], exp["id"],
                        inv["subject_text"] or "?",
                    ))
                    break

        print(f"\n发现 {len(duplicates)} 条 INVESTMENT↔EXPANSION 重叠：\n")
        for inv_id, exp_id, subj in duplicates:
            print(f"  INVESTMENT {inv_id[:8]} ({subj}) → EXPANSION {exp_id[:8]}")

        if dry_run:
            print(f"\n[DRY-RUN] 将标记 {len(duplicates)} 条为 DUPLICATE")
            return len(duplicates)

        for inv_id, exp_id, subj in duplicates:
            conn.execute(
                "UPDATE fact_atom SET review_status='DUPLICATE', review_note=? WHERE id=?",
                (f"重复: 无金额INVESTMENT，与EXPANSION {exp_id} 重叠", inv_id),
            )
        conn.commit()
        print(f"\n已标记 {len(duplicates)} 条为 DUPLICATE")
        return len(duplicates)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="存量事实去重工具")
    parser.add_argument("--apply", action="store_true", help="执行去重（默认 dry-run）")
    args = parser.parse_args()

    dry_run = not args.apply
    if dry_run:
        print("=" * 60)
        print("  DRY-RUN 模式：仅报告，不修改数据")
        print("  加 --apply 参数执行实际去重")
        print("=" * 60)

    total = 0
    print("\n--- 第1步：全局指纹去重 ---")
    total += deduplicate_global(dry_run)

    print("\n--- 第2步：跨类型去重 (INVESTMENT↔EXPANSION) ---")
    total += deduplicate_cross_type_global(dry_run)

    print(f"\n{'=' * 60}")
    print(f"  总计: {total} 条待去重")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
