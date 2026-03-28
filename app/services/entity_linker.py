"""实体标准化 —— 精确匹配 + 别名匹配 + 括号规范化匹配 + 自动发现 + 未命中保留原文"""

import re
import uuid

from app.logger import get_logger
from app.models.db import get_connection
from app.services import entity_utils as eu

logger = get_logger(__name__)

# ──────────── 复用 entity_utils ────────────
_PAREN_RE = eu.PAREN_RE
_LEGAL_SUFFIXES = eu.LEGAL_SUFFIXES
normalize = eu.normalize
fingerprint = eu.fingerprint


def _normalize_name(text: str) -> str:
    """
    将实体名称规范化，用于模糊匹配：
    1. 去掉括号内容 + 法人后缀
    返回规范化后的字符串；若与原文相同则返回空字符串（无需再查一次）。
    """
    norm = eu.normalize(text)
    return norm if norm != text else ""


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

        # 4. 包含匹配回退：text 是某 entity 名称的子串，或反之
        search_texts = [text]
        if normalized and normalized not in search_texts:
            search_texts.append(normalized)

        for st in search_texts:
            if len(st) < 2:
                continue
            safe = st.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            rows = conn.execute(
                """SELECT id, canonical_name FROM entity
                   WHERE canonical_name LIKE ? ESCAPE '!'
                     AND canonical_name != ?""",
                (f"%{safe}%", st),
            ).fetchall()
            if rows:
                best = min(rows, key=lambda r: len(r["canonical_name"]))
                logger.info("包含匹配: '%s' → '%s'", st, best["canonical_name"])
                return {"entity_id": best["id"], "canonical_name": best["canonical_name"], "matched": True}

        # 反向包含：某 entity 的 canonical_name 是 text 的子串
        if len(text) >= 2:
            rows = conn.execute(
                """SELECT id, canonical_name FROM entity
                   WHERE ? LIKE '%' || canonical_name || '%'
                     AND canonical_name != ?
                     AND LENGTH(canonical_name) >= 2""",
                (text, text),
            ).fetchall()
            if rows:
                best = max(rows, key=lambda r: len(r["canonical_name"]))
                logger.info("反向包含匹配: '%s' → '%s'", text, best["canonical_name"])
                return {"entity_id": best["id"], "canonical_name": best["canonical_name"], "matched": True}

        # 5. 未命中 → 保留原文
        return {"entity_id": None, "canonical_name": text, "matched": False}

    finally:
        conn.close()


def disambiguate(name: str, context: dict | None = None) -> dict:
    """
    实体名称消歧：判断一个名称可能对应哪个（些）实体。

    参数:
        name: 待消歧的名称
        context: 可选上下文 {"qualifier": "...", "article_title": "...", "source": "..."}

    返回:
        {
            "name": str,
            "candidates": [
                {
                    "entity_id": str,
                    "canonical_name": str,
                    "entity_type": str,
                    "primary_type": str | None,    # 新增字段（可能为空）
                    "tags": list[str],             # 新增字段（可能为空）
                    "confidence": float,            # 0~1
                    "qualifiers": list[str],        # 从 qualifier_json 提取的修饰词
                    "source_articles": list[str],   # 关联文章来源
                    "geo_paren": str | None,       # 括号内地理词（如"香港"）
                    "ambiguity_note": str,
                }
            ],
            "fallback_action": "create_new" | "require_manual" | "match_best",
            "has_context": bool,
        }
    """
    import json

    if not name or not name.strip():
        return {
            "name": name or "",
            "candidates": [],
            "fallback_action": "require_manual",
            "has_context": bool(context),
        }

    text = name.strip()
    normalized = eu.normalize(text)
    has_context = bool(context)

    conn = get_connection()
    try:
        # 1. 查询所有与规范化名称相同或相似的实体
        candidates: list[dict] = []

        # 精确匹配 canonical_name
        rows = conn.execute(
            """SELECT id, canonical_name, entity_type FROM entity
               WHERE canonical_name = ? ORDER BY rowid ASC""",
            (text,),
        ).fetchall()

        # 如果规范化后与原文不同，也查规范化名
        if normalized and normalized != text:
            rows = list(rows)
            rows2 = conn.execute(
                """SELECT id, canonical_name, entity_type FROM entity
                   WHERE canonical_name = ? ORDER BY rowid ASC""",
                (normalized,),
            ).fetchall()
            seen = {r["id"] for r in rows}
            for r in rows2:
                if r["id"] not in seen:
                    rows.append(r)

        for row in rows:
            entity_id = row["id"]

            # 提取括号内地理词
            geo = eu.extract_geo_paren(row["canonical_name"])

            # 收集 qualifier 修饰词（从 fact_atom）
            qualifiers: set[str] = set()
            art_titles: set[str] = set()
            fa_rows = conn.execute(
                """SELECT fa.qualifier_json, sd.title
                   FROM fact_atom fa
                   JOIN source_document sd ON fa.document_id = sd.id
                   WHERE (fa.subject_entity_id = ? OR fa.object_entity_id = ?)
                     AND fa.qualifier_json IS NOT NULL
                   LIMIT 20""",
                (entity_id, entity_id),
            ).fetchall()
            for fa in fa_rows:
                if fa["qualifier_json"]:
                    try:
                        q = json.loads(fa["qualifier_json"])
                        for v in q.values():
                            if isinstance(v, str) and v.strip():
                                qualifiers.add(v.strip())
                    except Exception:
                        pass
                if fa["title"]:
                    art_titles.add(fa["title"][:50])

            # 计算基础置信度
            confidence = 0.5
            if eu.extract_geo_paren(text) == geo:
                # 有地理消歧信号，置信度高
                confidence = 0.85 if geo else 0.75
            elif context:
                # 有上下文，进一步提升
                ctx_str = str(context).lower()
                if any(kw in ctx_str for kw in qualifiers):
                    confidence = 0.88

            # 构建歧义提示
            ambiguity_note = ""
            if geo:
                ambiguity_note = f"括号含地理修饰词'{geo}'"
            if len(rows) > 1:
                ambiguity_note = (ambiguity_note + f"；另有{len(rows)-1}个同名实体，需结合上下文判断"
                                  if ambiguity_note else f"存在{len(rows)}个同名实体，需结合上下文判断")

            candidates.append({
                "entity_id": entity_id,
                "canonical_name": row["canonical_name"],
                "entity_type": row["entity_type"],
                "primary_type": None,
                "tags": [],
                "confidence": confidence,
                "qualifiers": list(qualifiers)[:10],
                "source_articles": list(art_titles)[:5],
                "geo_paren": geo,
                "ambiguity_note": ambiguity_note,
            })

        # 2. 决定 fallback_action
        if candidates:
            if has_context:
                # 有上下文：交给调用方判断
                fallback_action = "match_best"
            else:
                # 无上下文：必须人工确认
                fallback_action = "require_manual"
        else:
            # 没有同名实体 → 可创建新的
            fallback_action = "create_new"

        return {
            "name": text,
            "candidates": candidates,
            "fallback_action": fallback_action,
            "has_context": has_context,
        }

    finally:
        conn.close()


def add_entity(name: str, entity_type: str, entity_id: str | None = None) -> str:
    """手动添加实体（管理工具使用）。若同名实体已存在则直接返回其 id（幂等）。"""
    conn = get_connection()
    try:
        # 先查：同名实体已存在则直接返回（避免重复创建）
        if not entity_id:
            row = conn.execute(
                "SELECT id FROM entity WHERE canonical_name = ? ORDER BY rowid ASC LIMIT 1",
                (name,),
            ).fetchone()
            if row:
                return row["id"]
        eid = entity_id or str(uuid.uuid4())
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


# --- 实体类型推断（委托给 entity_utils）---
infer_entity_type = eu.infer_entity_type


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

        # 跳过通用名称（不应作为独立实体）
        if text in eu.SKIP_NAMES:
            continue

        # 包含匹配：避免创建与已有实体名称高度重叠的新实体
        if len(text) >= 2:
            safe = text.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            contains_row = conn.execute(
                """SELECT id, canonical_name FROM entity
                   WHERE canonical_name LIKE ? ESCAPE '!'
                     AND canonical_name != ?
                   ORDER BY LENGTH(canonical_name) LIMIT 1""",
                (f"%{safe}%", text),
            ).fetchone()
            if contains_row:
                conn.execute(
                    "INSERT OR IGNORE INTO entity_alias (id, entity_id, alias_name) VALUES (?, ?, ?)",
                    (str(uuid.uuid4()), contains_row["id"], text),
                )
                logger.debug("自动别名: '%s' → '%s'", text, contains_row["canonical_name"])
                continue

        entity_type = eu.infer_entity_type(text, fact_type)[0]
        eid = str(uuid.uuid4())
        conn.execute(
            """INSERT OR IGNORE INTO entity (id, canonical_name, normalized_name, entity_type)
               VALUES (?, ?, ?, ?)""",
            (eid, text, text, entity_type),
        )
        created += 1
        logger.debug("自动创建实体: '%s' [type=%s]", text, entity_type)

    if created:
        conn.commit()
        logger.info("自动发现并创建 %d 个新实体", created)

    return created


# --- 常用地点映射（自动创建用）---
_LOCATION_KEYWORDS = eu.LOCATION_KEYWORDS


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


# ====================== 实体关系管理 ======================

import json as _json

# 关系类型常量
RELATION_TYPES = ("SUBSIDIARY", "SHAREHOLDER", "JV", "BRAND", "PARTNER", "INVESTS_IN")


def add_entity_relation(
    from_entity_id: str,
    to_entity_id: str,
    relation_type: str,
    detail_json: str = "{}",
    source: str = "manual",
) -> str:
    """
    添加实体关系。

    参数:
        from_entity_id: 源实体（如母公司）
        to_entity_id: 目标实体（如子公司）
        relation_type: SUBSIDIARY | SHAREHOLDER | JV | BRAND | PARTNER
        detail_json: JSON 扩展信息，如 {"share_pct": 42.53}
        source: manual | auto_extracted

    返回: 关系记录 ID
    """
    if relation_type not in RELATION_TYPES:
        raise ValueError(f"无效关系类型: {relation_type}，可选: {RELATION_TYPES}")

    rel_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO entity_relation
               (id, from_entity_id, to_entity_id, relation_type, detail_json, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rel_id, from_entity_id, to_entity_id, relation_type, detail_json, source),
        )
        conn.commit()
    finally:
        conn.close()
    return rel_id


def remove_entity_relation(relation_id: str) -> bool:
    """删除一条实体关系记录。"""
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM entity_relation WHERE id = ?", (relation_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_entity_relations(entity_id: str) -> list[dict]:
    """
    获取与指定实体相关的所有关系（无论方向）。

    返回: [{"id", "from_entity_id", "from_name", "to_entity_id", "to_name",
            "relation_type", "detail_json", "source"}, ...]
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT r.id, r.from_entity_id, ef.canonical_name AS from_name,
                      r.to_entity_id, et.canonical_name AS to_name,
                      r.relation_type, r.detail_json, r.source
               FROM entity_relation r
               JOIN entity ef ON r.from_entity_id = ef.id
               JOIN entity et ON r.to_entity_id = et.id
               WHERE r.from_entity_id = ? OR r.to_entity_id = ?""",
            (entity_id, entity_id),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_known_entities_context() -> str:
    """
    构建已知实体及关系的上下文文本，用于注入 LLM Prompt。

    返回格式示例：
    ## Known entities & relationships
    - 佐敦涂料（中国）有限公司 [COMPANY] (别名: 佐敦, 佐敦中国)
      → SUBSIDIARY: Jotun AS
    """
    conn = get_connection()
    try:
        entities = conn.execute(
            "SELECT id, canonical_name, entity_type FROM entity ORDER BY canonical_name"
        ).fetchall()
        if not entities:
            return ""

        aliases = conn.execute(
            "SELECT entity_id, alias_name FROM entity_alias"
        ).fetchall()
        alias_map: dict[str, list[str]] = {}
        for a in aliases:
            alias_map.setdefault(a["entity_id"], []).append(a["alias_name"])

        relations = conn.execute(
            """SELECT r.from_entity_id, ef.canonical_name AS from_name,
                      r.to_entity_id, et.canonical_name AS to_name,
                      r.relation_type, r.detail_json
               FROM entity_relation r
               JOIN entity ef ON r.from_entity_id = ef.id
               JOIN entity et ON r.to_entity_id = et.id"""
        ).fetchall()

        rel_from: dict[str, list] = {}
        rel_to: dict[str, list] = {}
        for r in relations:
            rel_from.setdefault(r["from_entity_id"], []).append(r)
            rel_to.setdefault(r["to_entity_id"], []).append(r)

        lines = [
            "## Known entities & relationships",
            "When you recognize any of these entities or their aliases in the article, use the canonical name.\n",
        ]
        for e in entities:
            eid = e["id"]
            name = e["canonical_name"]
            etype = e["entity_type"]
            alias_list = alias_map.get(eid, [])

            line = f"- {name} [{etype}]"
            if alias_list:
                line += f" (别名: {', '.join(alias_list)})"
            lines.append(line)

            for r in rel_from.get(eid, []):
                detail = r["detail_json"] or "{}"
                try:
                    d = _json.loads(detail) if isinstance(detail, str) else detail
                except (_json.JSONDecodeError, TypeError):
                    d = {}
                pct = f" ({d['share_pct']}%)" if "share_pct" in d else ""
                lines.append(f"  → {r['relation_type']}: {r['to_name']}{pct}")

            for r in rel_to.get(eid, []):
                detail = r["detail_json"] or "{}"
                try:
                    d = _json.loads(detail) if isinstance(detail, str) else detail
                except (_json.JSONDecodeError, TypeError):
                    d = {}
                pct = f" ({d['share_pct']}%)" if "share_pct" in d else ""
                lines.append(f"  ← {r['relation_type']} of {r['from_name']}{pct}")

        return "\n".join(lines)
    finally:
        conn.close()


def get_candidate_relations_from_facts() -> list[dict]:
    """
    从已通过的 COOPERATION/INVESTMENT/EXPANSION 事实中，提取候选实体关系对。
    只返回尚未在 entity_relation 中存在的候选。

    返回: [{"from_id", "from_name", "to_id", "to_name",
            "suggested_type", "evidence_count", "sample_predicates"}, ...]
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT f.subject_entity_id, es.canonical_name AS subject_name,
                      f.object_entity_id, eo.canonical_name AS object_name,
                      f.fact_type, f.predicate
               FROM fact_atom f
               JOIN entity es ON f.subject_entity_id = es.id
               JOIN entity eo ON f.object_entity_id = eo.id
               WHERE f.review_status IN ('AUTO_PASS', 'HUMAN_PASS')
                 AND f.fact_type IN ('COOPERATION', 'INVESTMENT', 'EXPANSION')
                 AND f.subject_entity_id IS NOT NULL
                 AND f.object_entity_id IS NOT NULL
                 AND f.subject_entity_id != f.object_entity_id"""
        ).fetchall()

        pair_data: dict[tuple, dict] = {}
        for r in rows:
            key = (r["subject_entity_id"], r["object_entity_id"])
            if key not in pair_data:
                pair_data[key] = {
                    "from_id": r["subject_entity_id"],
                    "from_name": r["subject_name"],
                    "to_id": r["object_entity_id"],
                    "to_name": r["object_name"],
                    "fact_types": set(),
                    "predicates": [],
                    "count": 0,
                }
            pair_data[key]["fact_types"].add(r["fact_type"])
            pair_data[key]["count"] += 1
            if r["predicate"] not in pair_data[key]["predicates"]:
                pair_data[key]["predicates"].append(r["predicate"])

        existing = conn.execute(
            "SELECT from_entity_id, to_entity_id FROM entity_relation"
        ).fetchall()
        existing_set = {(r["from_entity_id"], r["to_entity_id"]) for r in existing}
        existing_bidir = existing_set | {(b, a) for a, b in existing_set}

        candidates = []
        for key, data in pair_data.items():
            if key in existing_bidir:
                continue

            if "INVESTMENT" in data["fact_types"]:
                suggested = "SHAREHOLDER"
            elif "COOPERATION" in data["fact_types"]:
                suggested = "PARTNER"
            elif "EXPANSION" in data["fact_types"]:
                suggested = "SUBSIDIARY"
            else:
                suggested = "PARTNER"

            candidates.append({
                "from_id": data["from_id"],
                "from_name": data["from_name"],
                "to_id": data["to_id"],
                "to_name": data["to_name"],
                "suggested_type": suggested,
                "evidence_count": data["count"],
                "sample_predicates": data["predicates"][:3],
            })

        candidates.sort(key=lambda x: x["evidence_count"], reverse=True)
        return candidates
    finally:
        conn.close()


def ai_suggest_relations(hint: str = "") -> list[dict]:
    """
    [废弃] 请使用 entity_analyzer 模块的 analyze_entity() 方法。
    此函数将在后续版本中移除。
    """
    """
    调用 LLM 分析数据库中所有实体之间的可能关系。

    参数:
        hint: 用户输入的关键词/背景提示（可选）

    返回: [{"from_id", "from_name", "to_id", "to_name",
             "relation_type", "reason", "confidence"}, ...]
    """
    from app.services.llm_client import LLMClient

    conn = get_connection()
    try:
        entities = conn.execute(
            """SELECT e.id, e.canonical_name, e.entity_type,
                      GROUP_CONCAT(a.alias_name, '、') AS aliases
               FROM entity e
               LEFT JOIN entity_alias a ON a.entity_id = e.id
               GROUP BY e.id
               ORDER BY e.canonical_name"""
        ).fetchall()
        existing = conn.execute(
            "SELECT from_entity_id, to_entity_id FROM entity_relation"
        ).fetchall()
    finally:
        conn.close()

    if not entities:
        return []

    entity_lines = []
    for e in entities:
        line = f"- [{e['id']}] {e['canonical_name']} [{e['entity_type'] or 'UNKNOWN'}]"
        if e["aliases"]:
            line += f"  别名: {e['aliases']}"
        entity_lines.append(line)
    entity_block = "\n".join(entity_lines)

    existing_set = {(r["from_entity_id"], r["to_entity_id"]) for r in existing}
    existing_bidir = existing_set | {(b, a) for a, b in existing_set}
    if existing_bidir:
        existing_lines = "\n".join(
            f"  {e['from_entity_id']} ↔ {e['to_entity_id']}"
            for e in existing
        )
        existing_note = f"\n\n以下实体对的关系已存在，请不要重复提议：\n{existing_lines}"
    else:
        existing_note = ""

    hint_note = f"\n\n用户补充背景提示：{hint}" if hint.strip() else ""

    system_prompt = (
        "你是企业关系分析专家，擅长从公司名称和别名推断母子公司、合资、品牌、股东等关系。"
        "请只输出 JSON 数组，不要输出其他文字。"
    )
    user_prompt = f"""请分析以下实体列表，识别它们之间可能存在的公司关系。
{hint_note}
实体列表（格式：[ID] 名称 [类型]  别名）：
{entity_block}
{existing_note}

关系类型说明：
- SUBSIDIARY：from 是 to 的子公司
- SHAREHOLDER：from 是 to 的股东（持有股份）
- JV：from 是由多个实体合资成立的企业
- BRAND：from 是 to 旗下的品牌
- PARTNER：from 和 to 存在商业合作关系

请以 JSON 数组输出，每条格式如下（confidence 为 0~1：
[
  {{"from_id": "实体ID", "from_name": "实体名称", "to_id": "实体ID", "to_name": "实体名称", "relation_type": "SUBSIDIARY", "reason": "判断依据", "confidence": 0.85}}
]
只输出有较高确定性的关系（confidence >= 0.6）。如果没有可识别的关系，输出空数组 []。"""

    try:
        client = LLMClient()
        res = client.chat_json(system_prompt, user_prompt)
        suggestions = res.get("data", [])
        if not isinstance(suggestions, list):
            return []

        valid_types = {"SUBSIDIARY", "SHAREHOLDER", "JV", "BRAND", "PARTNER", "INVESTS_IN"}
        valid_ids = {e["id"] for e in entities}
        result = []
        for s in suggestions:
            if (
                s.get("relation_type") in valid_types
                and s.get("from_id") in valid_ids
                and s.get("to_id") in valid_ids
                and s.get("from_id") != s.get("to_id")
                and (s["from_id"], s["to_id"]) not in existing_bidir
            ):
                result.append({
                    "from_id": s["from_id"],
                    "from_name": s.get("from_name", s["from_id"]),
                    "to_id": s["to_id"],
                    "to_name": s.get("to_name", s["to_id"]),
                    "relation_type": s["relation_type"],
                    "reason": s.get("reason", ""),
                    "confidence": float(s.get("confidence", 0.7)),
                })
        result.sort(key=lambda x: x["confidence"], reverse=True)
        return result
    except Exception as exc:
        logger.error("AI 关系建议失败: %s", exc)
        return []
