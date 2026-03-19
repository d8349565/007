"""实体标准化 —— 精确匹配 + 别名匹配 + 括号规范化匹配 + 自动发现 + 未命中保留原文"""

import re
import uuid

from app.logger import get_logger
from app.models.db import get_connection

logger = get_logger(__name__)

# 括号规范化：去掉中英文括号内容及常见法人后缀
_PAREN_RE = re.compile(r'[（(][^）)]*[）)]')
_LEGAL_SUFFIXES = ('有限公司', '有限责任公司', '股份有限公司', '股份公司')


def _normalize_name(text: str) -> str:
    """
    将实体名称规范化，用于模糊匹配：
    1. 去掉括号内容："中远佐敦船舶涂料（青岛）有限公司" → "中远佐敦船舶涂料有限公司"
    2. 再去掉常见法人后缀：→ "中远佐敦船舶涂料"
    返回规范化后的字符串；若与原文相同则返回空字符串（无需再查一次）。
    """
    s = _PAREN_RE.sub('', text).strip()
    for suffix in _LEGAL_SUFFIXES:
        if s.endswith(suffix):
            s = s[:-len(suffix)].strip()
            break
    return s if s != text else ''


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

        # 3. 括号规范化匹配（去括号+法人后缀后重新查）
        normalized = _normalize_name(text)
        if normalized:
            row = conn.execute(
                "SELECT id, canonical_name FROM entity WHERE canonical_name = ?", (normalized,)
            ).fetchone()
            if not row:
                row = conn.execute(
                    """SELECT e.id, e.canonical_name FROM entity_alias ea
                       JOIN entity e ON ea.entity_id = e.id
                       WHERE ea.alias_name = ?""",
                    (normalized,),
                ).fetchone()
            if row:
                logger.info("括号规范化匹配: '%s' → '%s'", text, row["canonical_name"])
                return {"entity_id": row["id"], "canonical_name": row["canonical_name"], "matched": True}

        # 4. 未命中 → 保留原文
        return {"entity_id": None, "canonical_name": text, "matched": False}

    finally:
        conn.close()


def add_entity(name: str, entity_type: str, entity_id: str | None = None) -> str:
    """手动添加实体（管理工具使用）"""
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


# --- 实体类型推断后缀规则 ---
# 项目/工程类结尾词（优先级最高：避免"XX涂料新工厂项目"被误判为 COMPANY）
_PROJECT_ENDINGS = ("项目", "工程", "专项", "计划")
# 纯工厂/基地名（独立设施，不是公司法人）
_FACILITY_KEYWORDS = ("新工厂", "生产基地", "研发基地", "产业基地", "产业园区")

_COMPANY_SUFFIXES = (
    "公司", "集团", "股份", "有限", "企业", "涂料", "化工", "科技",
    "工业", "实业", "控股", "国际", "材料", "制造",
)


def _infer_entity_type(text: str, fact_type: str = "") -> str:
    """
    根据文本特征和 fact_type 上下文推断实体类型。
    返回: PROJECT / COMPANY / GROUP / PRODUCT / UNKNOWN

    优先级（从高到低）：
      1. 以"项目/工程/专项/计划"结尾 → PROJECT
      2. 包含独立设施关键词且无公司法人后缀 → PROJECT
      3. GROUP 集合主体
      4. 含公司法人后缀 → COMPANY
      5. COOPERATION fact_type → PROJECT
      6. 其他 → UNKNOWN
    """
    if not text:
        return "UNKNOWN"

    # 1. 以项目/工程类词结尾 → 直接 PROJECT（最高优先级）
    for ending in _PROJECT_ENDINGS:
        if text.endswith(ending):
            return "PROJECT"

    # 2. 包含独立设施词且不含公司法人后缀 → PROJECT
    if any(kw in text for kw in _FACILITY_KEYWORDS):
        if not any(s in text for s in ("公司", "集团", "股份", "有限")):
            return "PROJECT"

    # 3. 集合主体检测（优先于公司后缀，因为"企业"同时出现在两者中）
    if any(kw in text for kw in ("前十强", "前五强", "前三强", "上榜")):
        return "GROUP"
    if "品牌" in text and any(kw in text for kw in ("外资", "国产", "本土")):
        return "GROUP"

    # 4. 主体包含公司类后缀 → COMPANY
    for suffix in _COMPANY_SUFFIXES:
        if suffix in text:
            return "COMPANY"

    # 5. COOPERATION 类型的 object 通常是项目/产品
    if fact_type == "COOPERATION":
        return "PROJECT"

    return "UNKNOWN"


def _auto_discover_entities(rows: list, conn) -> int:
    """
    从 fact_atom 行中自动发现尚未存在的 subject_text / object_text，
    去重后创建新的 entity 记录。

    返回新创建的实体数。
    """
    # 收集所有需要检查的文本 → (text, fact_type, role)
    candidates: dict[str, str] = {}  # text → fact_type
    for row in rows:
        if row["subject_text"] and row["subject_text"].strip():
            text = row["subject_text"].strip()
            if text not in candidates:
                candidates[text] = row["fact_type"] or ""
        if row["object_text"] and row["object_text"].strip():
            text = row["object_text"].strip()
            if text not in candidates:
                candidates[text] = row["fact_type"] or ""

    if not candidates:
        return 0

    created = 0
    for text, fact_type in candidates.items():
        # 检查是否已存在（精确匹配 canonical_name）
        existing = conn.execute(
            "SELECT id FROM entity WHERE canonical_name = ?", (text,)
        ).fetchone()
        if existing:
            continue

        # 也检查别名表
        alias_match = conn.execute(
            "SELECT entity_id FROM entity_alias WHERE alias_name = ?", (text,)
        ).fetchone()
        if alias_match:
            continue

        entity_type = _infer_entity_type(text, fact_type)
        eid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO entity (id, canonical_name, normalized_name, entity_type)
               VALUES (?, ?, ?, ?)""",
            (eid, text, text, entity_type),
        )
        created += 1
        logger.debug("自动创建实体: '%s' [type=%s]", text, entity_type)

    if created:
        conn.commit()
        logger.info("自动发现并创建 %d 个新实体", created)

    return created


# --- 常用地点映射（自动创建用） ---
_LOCATION_KEYWORDS = {
    "全国": "REGION",
    "中国": "COUNTRY",
    "全球": "REGION",
    "国内": "REGION",
    "在华": "REGION",
    "华东": "REGION",
    "华南": "REGION",
    "华北": "REGION",
    "华中": "REGION",
    "西南": "REGION",
    "西北": "REGION",
    "东北": "REGION",
}


def _ensure_location_entities(conn) -> int:
    """确保常用地点实体存在，返回新创建数量。"""
    created = 0
    for name, loc_type in _LOCATION_KEYWORDS.items():
        existing = conn.execute(
            "SELECT id FROM entity WHERE canonical_name = ?", (name,)
        ).fetchone()
        if not existing:
            eid = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO entity (id, canonical_name, normalized_name, entity_type)
                   VALUES (?, ?, ?, ?)""",
                (eid, name, name, loc_type),
            )
            created += 1
    if created:
        conn.commit()
        logger.info("创建 %d 个地点实体", created)
    return created


def _link_location_text(location_text: str, conn) -> str | None:
    """
    尝试将 location_text 匹配到已有实体。
    支持精确匹配和包含匹配（如 "全国船舶涂料市场" 匹配 "全国"）。
    返回 entity_id 或 None。
    """
    if not location_text or not location_text.strip():
        return None
    text = location_text.strip()

    # 1. 精确匹配
    row = conn.execute(
        "SELECT id FROM entity WHERE canonical_name = ?", (text,)
    ).fetchone()
    if row:
        return row["id"]

    # 2. 包含匹配：location_text 中包含已知地点关键词
    for kw in _LOCATION_KEYWORDS:
        if kw in text:
            row = conn.execute(
                "SELECT id FROM entity WHERE canonical_name = ?", (kw,)
            ).fetchone()
            if row:
                return row["id"]

    return None


def batch_link_fact_atoms(fact_atom_ids: list[str] | None = None) -> dict:
    """
    批量为 fact_atom 记录执行实体链接。
    仅处理 subject_text / object_text / location_text 非空的记录。
    会先自动发现并创建不存在的实体，再执行链接。

    返回:
        {"processed": int, "matched": int, "unmatched": int, "created": int}
    """
    conn = get_connection()
    stats = {"processed": 0, "matched": 0, "unmatched": 0, "created": 0}

    try:
        # 确保地点实体存在
        _ensure_location_entities(conn)

        if fact_atom_ids:
            placeholders = ",".join(["?"] * len(fact_atom_ids))
            rows = conn.execute(
                f"SELECT id, subject_text, object_text, location_text, fact_type FROM fact_atom WHERE id IN ({placeholders})",
                fact_atom_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, subject_text, object_text, location_text, fact_type FROM fact_atom WHERE subject_entity_id IS NULL OR object_entity_id IS NULL OR location_entity_id IS NULL"
            ).fetchall()

        # 第一步：自动发现并创建不存在的实体
        created = _auto_discover_entities(rows, conn)
        stats["created"] = created

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

            # location 链接
            if row["location_text"]:
                loc_id = _link_location_text(row["location_text"], conn)
                if loc_id:
                    conn.execute(
                        "UPDATE fact_atom SET location_entity_id=? WHERE id=?",
                        (loc_id, row["id"]),
                    )
                    stats["matched"] += 1
                else:
                    stats["unmatched"] += 1

        conn.commit()
    finally:
        conn.close()

    logger.info("实体链接完成: %s", stats)
    return stats
