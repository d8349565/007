"""迁移脚本：新增 entity_profile 表

逻辑：
1. 检查 entity_profile 表是否已存在
2. 不存在则创建
3. --apply 实际执行，默认 dry-run
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import get_connection
from app.logger import get_logger

logger = get_logger(__name__)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS entity_profile (
    id               TEXT PRIMARY KEY,
    entity_id        TEXT NOT NULL UNIQUE REFERENCES entity(id),
    aliases_json     TEXT NOT NULL DEFAULT '[]',
    relations_json   TEXT NOT NULL DEFAULT '[]',
    benchmarks_json  TEXT NOT NULL DEFAULT '[]',
    competitors_json TEXT NOT NULL DEFAULT '[]',
    summary_text     TEXT NOT NULL DEFAULT '',
    profile_source   TEXT NOT NULL DEFAULT '聚合',
    fact_count       INTEGER NOT NULL DEFAULT 0,
    last_built_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_enriched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_entity_profile_entity
    ON entity_profile(entity_id);
"""


def migrate_entity_profile(dry_run: bool = True) -> int:
    conn = get_connection()
    try:
        # 检查表是否已存在
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_profile'"
        ).fetchone()

        if exists:
            logger.info("entity_profile 表已存在，跳过")
            return 0

        if not dry_run:
            conn.executescript(_CREATE_SQL)
            logger.info("entity_profile 表已创建")
        return 1
    finally:
        conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="新增 entity_profile 表")
    parser.add_argument("--apply", action="store_true", help="实际执行（默认 dry-run）")
    args = parser.parse_args()

    dry_run = not args.apply
    count = migrate_entity_profile(dry_run=dry_run)

    mode = "[DRY-RUN]" if dry_run else "[APPLIED]"
    if count:
        print(f"{mode} 需要创建 entity_profile 表")
    else:
        print(f"{mode} entity_profile 表已存在，无需操作")


if __name__ == "__main__":
    main()
