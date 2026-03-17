"""实体标准化 —— 精确匹配 + 别名匹配 + 未命中保留原文"""

from app.logger import get_logger
from app.models.db import get_connection

logger = get_logger(__name__)


def link_entity(raw_text: str, entity_type: str = "") -> dict:
    """
    尝试将原始文本链接到已有实体。

    匹配策略：
      1. 精确匹配 entity.name
      2. 别名匹配 entity_alias.alias
      3. 未命中 → 返回原文，不创建新实体

    返回:
        {"entity_id": str | None, "canonical_name": str, "matched": bool}
    """
    if not raw_text or not raw_text.strip():
        return {"entity_id": None, "canonical_name": raw_text or "", "matched": False}

    text = raw_text.strip()
    conn = get_connection()

    try:
        # 1. 精确匹配 entity.canonical_name
        row = conn.execute(
            "SELECT id, canonical_name FROM entity WHERE canonical_name = ?", (text,)
        ).fetchone()
        if row:
            return {"entity_id": row["id"], "canonical_name": row["canonical_name"], "matched": True}

        # 再加上 entity_type 精确匹配
        if entity_type:
            row = conn.execute(
                "SELECT id, canonical_name FROM entity WHERE canonical_name = ? AND entity_type = ?",
                (text, entity_type),
            ).fetchone()
            if row:
                return {"entity_id": row["id"], "canonical_name": row["canonical_name"], "matched": True}

        # 2. 别名匹配
        row = conn.execute(
            """SELECT e.id, e.canonical_name FROM entity_alias ea
               JOIN entity e ON ea.entity_id = e.id
               WHERE ea.alias_name = ?""",
            (text,),
        ).fetchone()
        if row:
            logger.info("别名匹配: '%s' → '%s'", text, row["canonical_name"])
            return {"entity_id": row["id"], "canonical_name": row["canonical_name"], "matched": True}

        # 3. 未命中 → 保留原文
        return {"entity_id": None, "canonical_name": text, "matched": False}

    finally:
        conn.close()


def add_entity(name: str, entity_type: str, entity_id: str | None = None) -> str:
    """手动添加实体（管理工具使用）"""
    import uuid
    eid = entity_id or str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO entity (id, canonical_name, normalized_name, entity_type)
               VALUES (?, ?, ?, ?)""",
            (eid, name, name, entity_type),
        )
        conn.commit()
    finally:
        conn.close()
    return eid


def add_alias(entity_id: str, alias: str) -> None:
    """为实体添加别名"""
    import uuid
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO entity_alias (id, entity_id, alias_name)
               VALUES (?, ?, ?)""",
            (str(uuid.uuid4()), entity_id, alias),
        )
        conn.commit()
    finally:
        conn.close()


def batch_link_fact_atoms(fact_atom_ids: list[str] | None = None) -> dict:
    """
    批量为 fact_atom 记录执行实体链接。
    仅处理 subject_text / object_text 非空的记录。

    返回:
        {"processed": int, "matched": int, "unmatched": int}
    """
    conn = get_connection()
    stats = {"processed": 0, "matched": 0, "unmatched": 0}

    try:
        if fact_atom_ids:
            placeholders = ",".join(["?"] * len(fact_atom_ids))
            rows = conn.execute(
                f"SELECT id, subject_text, object_text FROM fact_atom WHERE id IN ({placeholders})",
                fact_atom_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, subject_text, object_text FROM fact_atom WHERE subject_entity_id IS NULL OR object_entity_id IS NULL"
            ).fetchall()

        for row in rows:
            stats["processed"] += 1

            # subject 链接
            if row["subject_text"]:
                sub_result = link_entity(row["subject_text"])
                if sub_result["matched"]:
                    conn.execute(
                        "UPDATE fact_atom SET subject_entity_id=? WHERE id=?",
                        (sub_result["entity_id"], row["id"]),
                    )
                    stats["matched"] += 1
                else:
                    stats["unmatched"] += 1

            # object 链接
            if row["object_text"]:
                obj_result = link_entity(row["object_text"])
                if obj_result["matched"]:
                    conn.execute(
                        "UPDATE fact_atom SET object_entity_id=? WHERE id=?",
                        (obj_result["entity_id"], row["id"]),
                    )
                    stats["matched"] += 1
                else:
                    stats["unmatched"] += 1

        conn.commit()
    finally:
        conn.close()

    logger.info("实体链接完成: %s", stats)
    return stats
