"""
规范化 qualifier_json 字段名。

规则：
1. change_percentage_points → change_pct
2. change_amount_unit → 移除（冗余）
3. growth_description → 移除（信息已在 yoy 中）
4. product → product_type
5. proportion_of_business → 移除（不在白名单中）

用法:
    python scripts/normalize_qualifiers.py          # dry-run
    python scripts/normalize_qualifiers.py --apply  # 执行
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import get_connection
from app.logger import get_logger

logger = get_logger(__name__)

# 字段重命名映射
_RENAME_MAP = {
    "change_percentage_points": "change_pct",
    "product": "product_type",
}

# 直接移除的字段
_REMOVE_FIELDS = {"change_amount_unit", "growth_description", "proportion_of_business"}


def normalize_qualifiers(dry_run: bool = True) -> int:
    conn = get_connection()
    updated = 0
    try:
        rows = conn.execute(
            "SELECT id, qualifier_json FROM fact_atom WHERE qualifier_json IS NOT NULL AND qualifier_json != '{}' AND qualifier_json != 'null'"
        ).fetchall()

        for row in rows:
            try:
                q = json.loads(row["qualifier_json"])
            except (json.JSONDecodeError, TypeError):
                continue

            changed = False
            new_q = {}

            for k, v in q.items():
                if k in _REMOVE_FIELDS:
                    changed = True
                    logger.debug("移除字段 %s: %s (fact=%s)", k, v, row["id"][:8])
                    continue
                if k in _RENAME_MAP:
                    new_key = _RENAME_MAP[k]
                    # 如果目标字段已存在，保留原值不覆盖
                    if new_key not in q and new_key not in new_q:
                        new_q[new_key] = v
                        changed = True
                        logger.debug("重命名 %s → %s (fact=%s)", k, new_key, row["id"][:8])
                    else:
                        changed = True  # 跳过重复
                    continue
                new_q[k] = v

            if changed:
                updated += 1
                if not dry_run:
                    conn.execute(
                        "UPDATE fact_atom SET qualifier_json=? WHERE id=?",
                        (json.dumps(new_q, ensure_ascii=False), row["id"]),
                    )

        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return updated


def main():
    parser = argparse.ArgumentParser(description="规范化 qualifier_json 字段名")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    dry_run = not args.apply
    n = normalize_qualifiers(dry_run=dry_run)

    mode = "[DRY-RUN]" if dry_run else "[APPLIED]"
    print(f"{mode} 需/已修改 {n} 条 qualifier_json")


if __name__ == "__main__":
    main()
