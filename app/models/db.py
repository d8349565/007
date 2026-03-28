"""SQLite 数据库连接与初始化"""

import os
import sqlite3
from pathlib import Path

from app.config import get_config
from app.logger import get_logger

logger = get_logger(__name__)


def get_db_path() -> str:
    # 允许通过环境变量覆盖（测试用）
    override = os.environ.get("DATABASE_PATH_OVERRIDE")
    if override:
        return override
    cfg = get_config()
    return cfg["database"]["path"]


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """获取 SQLite 连接（启用 WAL 模式和外键约束）"""
    if db_path is None:
        db_path = get_db_path()

    # 确保目录存在
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | None = None) -> None:
    """执行 schema.sql 初始化所有表"""
    schema_path = Path(__file__).parent / "schema.sql"

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = get_connection(db_path)
    try:
        conn.executescript(schema_sql)
        conn.commit()
        logger.info("数据库初始化完成: %s", db_path or get_db_path())
    finally:
        conn.close()


def execute_query(
    sql: str,
    params: tuple | dict | None = None,
    db_path: str | None = None,
) -> list[sqlite3.Row]:
    """执行查询并返回结果"""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(sql, params or ())
        results = cursor.fetchall()
        conn.commit()
        return results
    finally:
        conn.close()


def execute_many(
    sql: str,
    params_list: list[tuple | dict],
    db_path: str | None = None,
) -> int:
    """批量执行"""
    conn = get_connection(db_path)
    try:
        cursor = conn.executemany(sql, params_list)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
