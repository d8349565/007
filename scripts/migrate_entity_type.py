"""
Schema 迁移脚本：为实体系统添加多标签和消歧支持字段

迁移内容：
1. entity 表：新增 primary_type, tags 字段
2. entity_alias 表：新增 alias_type 字段
3. entity_relation_suggestion 表：新增 source_document_id, ambiguity_note 字段

使用方法：
    python -m scripts.migrate_entity_type
"""
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "mvp.db"


def get_conn():
    return sqlite3.connect(DB_PATH)


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def migrate() -> None:
    print("开始迁移实体 Schema...")

    conn = get_conn()
    migrated = []

    try:
        # 1. entity 表：新增 primary_type, tags
        if not column_exists(conn, "entity", "primary_type"):
            conn.execute(
                "ALTER TABLE entity ADD COLUMN primary_type TEXT DEFAULT 'UNKNOWN'"
            )
            migrated.append("entity.primary_type")
            print("  + entity.primary_type")
        else:
            print("  = entity.primary_type (已存在)")

        if not column_exists(conn, "entity", "tags"):
            conn.execute(
                "ALTER TABLE entity ADD COLUMN tags TEXT DEFAULT '[]'"
            )
            migrated.append("entity.tags")
            print("  + entity.tags")
        else:
            print("  = entity.tags (已存在)")

        # 2. entity_alias 表：新增 alias_type
        if not column_exists(conn, "entity_alias", "alias_type"):
            conn.execute(
                "ALTER TABLE entity_alias ADD COLUMN alias_type TEXT DEFAULT 'alias'"
            )
            migrated.append("entity_alias.alias_type")
            print("  + entity_alias.alias_type")
        else:
            print("  = entity_alias.alias_type (已存在)")

        # 3. entity_relation_suggestion 表：新增字段
        if not column_exists(conn, "entity_relation_suggestion", "source_document_id"):
            conn.execute(
                "ALTER TABLE entity_relation_suggestion ADD COLUMN source_document_id TEXT"
            )
            migrated.append("entity_relation_suggestion.source_document_id")
            print("  + entity_relation_suggestion.source_document_id")
        else:
            print("  = entity_relation_suggestion.source_document_id (已存在)")

        if not column_exists(conn, "entity_relation_suggestion", "ambiguity_note"):
            conn.execute(
                "ALTER TABLE entity_relation_suggestion ADD COLUMN ambiguity_note TEXT"
            )
            migrated.append("entity_relation_suggestion.ambiguity_note")
            print("  + entity_relation_suggestion.ambiguity_note")
        else:
            print("  = entity_relation_suggestion.ambiguity_note (已存在)")

        conn.commit()

        if migrated:
            print(f"\n迁移完成，共新增 {len(migrated)} 个字段：")
            for f in migrated:
                print(f"  - {f}")
        else:
            print("\n无需迁移，所有字段已存在。")

    except Exception as e:
        conn.rollback()
        print(f"迁移失败: {e}")
        raise


if __name__ == "__main__":
    migrate()
