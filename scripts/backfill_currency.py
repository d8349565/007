"""
回填 currency 字段 + 修正单位格式。

逻辑：
1. unit 含"元"但 currency 空 → 从 unit 推断 currency
2. unit = "亿" → 改为 "亿元"（金额语境下的省略）
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import get_connection
from app.logger import get_logger

logger = get_logger(__name__)

# unit → currency 映射
_UNIT_CURRENCY_MAP = {
    "亿元": "CNY", "万元": "CNY", "元": "CNY", "元/吨": "CNY",
    "亿日元": "JPY",
    "亿美元": "USD",
    "亿港元": "HKD",
    "亿欧元": "EUR",
}


def backfill_currency(dry_run: bool = True) -> int:
    conn = get_connection()
    updated = 0
    try:
        rows = conn.execute(
            "SELECT id, unit FROM fact_atom WHERE (currency IS NULL OR currency = '') AND unit IS NOT NULL AND unit != ''"
        ).fetchall()

        for row in rows:
            currency = _UNIT_CURRENCY_MAP.get(row["unit"])
            if currency:
                if not dry_run:
                    conn.execute(
                        "UPDATE fact_atom SET currency=? WHERE id=?",
                        (currency, row["id"]),
                    )
                updated += 1
                logger.debug("回填 currency: %s → %s (unit=%s)", row["id"][:8], currency, row["unit"])

        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return updated


def fix_unit_format(dry_run: bool = True) -> int:
    """修正 unit = '亿' → '亿元'"""
    conn = get_connection()
    try:
        if dry_run:
            count = conn.execute("SELECT COUNT(*) FROM fact_atom WHERE unit = '亿'").fetchone()[0]
            return count
        else:
            count = conn.execute("UPDATE fact_atom SET unit = '亿元' WHERE unit = '亿'").rowcount
            conn.commit()
            return count
    finally:
        conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="回填 currency + 修正 unit")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    dry_run = not args.apply

    n_currency = backfill_currency(dry_run=dry_run)
    n_unit = fix_unit_format(dry_run=dry_run)

    mode = "[DRY-RUN]" if dry_run else "[APPLIED]"
    print(f"{mode} currency 回填: {n_currency} 条")
    print(f"{mode} unit '亿'→'亿元': {n_unit} 条")


if __name__ == "__main__":
    main()
