"""
迁移脚本：
  1. 为已有数据库添加 entity_relation_suggestion 表（若不存在）
  2. 为 entity_relation_suggestion 表补充 search_evidence / auto_confirmed 字段（若不存在）
  3. 添加 entity_search_cache 表（若不存在）
用法：python scripts/migrate_relation_suggestion.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.db import get_connection
from app.logger import get_logger

logger = get_logger(__name__)

_ERS_DDL = """
CREATE TABLE IF NOT EXISTS entity_relation_suggestion (
    id                TEXT PRIMARY KEY,
    entity_id         TEXT NOT NULL REFERENCES entity(id),
    target_name       TEXT NOT NULL,
    target_entity_id  TEXT REFERENCES entity(id),
    suggestion_type   TEXT NOT NULL,
    relation_type     TEXT,
    evidence          TEXT,
    evidence_fact_id  TEXT REFERENCES fact_atom(id),
    confidence        REAL NOT NULL DEFAULT 0.5,
    llm_reason        TEXT,
    search_evidence   TEXT,
    auto_confirmed    INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    confirmed_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_entity_relation_suggestion_entity
    ON entity_relation_suggestion(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_relation_suggestion_status
    ON entity_relation_suggestion(status);
"""

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS entity_search_cache (
    id           TEXT PRIMARY KEY,
    entity_name  TEXT NOT NULL,
    query        TEXT NOT NULL UNIQUE,
    search_source TEXT NOT NULL DEFAULT 'llm',
    raw_results  TEXT,
    summary_text TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_entity_search_cache_entity
    ON entity_search_cache(entity_name);
"""

# entity_relation_suggestion 新增字段（ALTER TABLE 幂等补全）
_NEW_COLUMNS = [
    ("search_evidence", "TEXT"),
    ("auto_confirmed", "INTEGER NOT NULL DEFAULT 0"),
]


def _table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def main():
    conn = get_connection()
    try:
        # 1. 创建 entity_relation_suggestion（若不存在）
        for stmt in _ERS_DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)

        # 2. 补全新字段
        existing_cols = _table_columns(conn, "entity_relation_suggestion")
        for col_name, col_def in _NEW_COLUMNS:
            if col_name not in existing_cols:
                conn.execute(
                    f"ALTER TABLE entity_relation_suggestion ADD COLUMN {col_name} {col_def}"
                )
                logger.info("已添加字段 entity_relation_suggestion.%s", col_name)
            else:
                logger.info("字段已存在，跳过: entity_relation_suggestion.%s", col_name)

        # 3. 创建 entity_search_cache（若不存在）
        for stmt in _CACHE_DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)

        conn.commit()
        logger.info("迁移完成。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
