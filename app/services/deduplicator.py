"""事实原子自动去重服务。

在 pipeline 抽取完成后自动执行，三层去重：
1. 同文档内去重：相同事实在两阶段抽取中被重复生成
2. 跨文档去重：不同文章报道同一事实
3. 跨类型去重：无金额 INVESTMENT 与 EXPANSION 描述同一事件

去重策略：保留 confidence 最高的一条作为正本（canonical），
其余标记为 review_status='重复'，review_note 记录正本 ID。
evidence_span 关联保持不变，可追溯原文出处。
"""

import json
import re

from app.logger import get_logger
from app.models.db import get_connection

logger = get_logger(__name__)

# 实体名归一化：去掉常见法律后缀
_COMPANY_SUFFIXES = [
    '股份有限公司', '有限责任公司', '有限公司', '集团公司', '集团', '控股',
]


def _normalize_subject(text: str) -> str:
    """归一化实体名称用于去重比较"""
    if not text:
        return ""
    text = text.strip()
    # 去掉括号中的地名/限定语
    text = re.sub(r'[（(][^）)]*[）)]', '', text)
    for s in _COMPANY_SUFFIXES:
        if text.endswith(s) and len(text) > len(s):
            text = text[:-len(s)]
    return text.strip().lower()


# 公开别名，供 scripts/ 使用
normalize_subject = _normalize_subject


def _get_discriminator(fact_type: str, qualifiers: dict,
                       predicate: str = "", object_text: str = "") -> str:
    """根据事实类型提取判别维度字段，用于去重指纹构建。

    同时被内存去重（full_extractor）和 DB 去重（deduplicator）调用，
    保证两处逻辑一致。
    """
    discriminator = ""
    if fact_type == "FINANCIAL_METRIC":
        discriminator = qualifiers.get("metric_name", "") or ""
    elif fact_type == "INVESTMENT":
        discriminator = (object_text or "")[:30]
    elif fact_type == "EXPANSION":
        discriminator = qualifiers.get("project_name", "") or ""
    elif fact_type == "CAPACITY":
        discriminator = qualifiers.get("product_type", "") or ""
    elif fact_type == "SALES_VOLUME":
        discriminator = qualifiers.get("metric_name", "") or ""
    elif fact_type == "MARKET_SHARE":
        discriminator = qualifiers.get("market_scope", "") or ""
    elif fact_type == "COMPETITIVE_RANKING":
        discriminator = qualifiers.get("ranking_scope", "") or ""
    elif fact_type == "COOPERATION":
        discriminator = (object_text or "")[:30]
    elif fact_type == "PRICE_CHANGE":
        discriminator = qualifiers.get("price_type", "") or ""

    # 无判别维度时用 predicate 兜底
    if not discriminator:
        discriminator = (predicate or "")[:20]

    return discriminator.strip().lower()


def _build_dedup_key(fact: dict) -> str:
    """构建去重指纹。

    使用 (fact_type, 归一化subject, 类型特定判别维度, value_num, time_expr)
    作为复合键，在保证不误杀的前提下尽量合并同一事实。
    """
    fact_type = fact.get("fact_type", "")
    subject = _normalize_subject(fact.get("subject_text", ""))
    value_num = fact.get("value_num")
    time_expr = (fact.get("time_expr") or "").strip()

    # 解析 qualifier_json
    qualifiers = {}
    q_raw = fact.get("qualifier_json", "{}")
    if isinstance(q_raw, str):
        try:
            qualifiers = json.loads(q_raw)
        except Exception:
            qualifiers = {}
    elif isinstance(q_raw, dict):
        qualifiers = q_raw

    discriminator = _get_discriminator(
        fact_type, qualifiers,
        predicate=fact.get("predicate", ""),
        object_text=fact.get("object_text", ""),
    )

    value_str = str(value_num) if value_num is not None else ""

    return "|".join([
        fact_type, subject, discriminator,
        value_str, time_expr,
    ])


# 公开别名，供 scripts/ 使用
build_dedup_key = _build_dedup_key


def deduplicate_facts(document_id: str) -> dict:
    """
    对指定文档的事实原子执行自动去重。

    在 pipeline 中于 review 之后、entity_link 之前调用。

    返回: {"within_doc": int, "cross_doc": int, "cross_type": int}
    """
    stats = {"within_doc": 0, "cross_doc": 0, "cross_type": 0}

    stats["within_doc"] = _dedup_within_document(document_id)
    stats["cross_doc"] = _dedup_cross_document(document_id)
    stats["cross_type"] = _dedup_cross_type(document_id)

    total = sum(stats.values())
    if total > 0:
        logger.info(
            "[doc=%s] 去重完成: 同文档=%d, 跨文档=%d, 跨类型=%d",
            document_id[:8], stats["within_doc"],
            stats["cross_doc"], stats["cross_type"],
        )

    return stats


_ACTIVE_FILTER = "review_status NOT IN ('已拒绝', '重复', '人工拒绝')"

_FACT_COLUMNS = """id, fact_type, subject_text, predicate, object_text,
    value_num, value_text, time_expr, location_text,
    qualifier_json, confidence_score"""


def _dedup_within_document(document_id: str) -> int:
    """同文档内去重：相同指纹保留最高分，其余标记 DUPLICATE"""
    conn = get_connection()
    marked = 0
    try:
        facts = conn.execute(
            f"""SELECT {_FACT_COLUMNS}
            FROM fact_atom
            WHERE document_id = ? AND {_ACTIVE_FILTER}
            ORDER BY confidence_score DESC""",
            (document_id,),
        ).fetchall()

        seen = {}       # dedup_key → canonical fact id
        duplicates = [] # (dup_id, canonical_id)

        for f in facts:
            key = _build_dedup_key(dict(f))
            if key in seen:
                duplicates.append((f["id"], seen[key]))
            else:
                seen[key] = f["id"]

        for dup_id, canonical_id in duplicates:
            conn.execute(
                f"""UPDATE fact_atom
                SET review_status='重复', review_note=?
                WHERE id=? AND {_ACTIVE_FILTER}""",
                (f"重复: 与 {canonical_id} 重复（同文档）", dup_id),
            )
            marked += 1

        if marked > 0:
            conn.commit()
            logger.info("[doc=%s] 同文档去重: 标记 %d 条", document_id[:8], marked)
    finally:
        conn.close()
    return marked


def _dedup_cross_document(document_id: str) -> int:
    """跨文档去重：新文档的事实与已有其他文档的事实比较"""
    conn = get_connection()
    marked = 0
    try:
        # 当前文档的活跃事实
        new_facts = conn.execute(
            f"""SELECT {_FACT_COLUMNS}
            FROM fact_atom
            WHERE document_id = ? AND {_ACTIVE_FILTER}""",
            (document_id,),
        ).fetchall()

        if not new_facts:
            return 0

        # 其他文档的活跃事实，构建索引
        existing_facts = conn.execute(
            f"""SELECT {_FACT_COLUMNS}
            FROM fact_atom
            WHERE document_id != ? AND {_ACTIVE_FILTER}""",
            (document_id,),
        ).fetchall()

        existing_index = {}  # key → (id, confidence)
        for ef in existing_facts:
            key = _build_dedup_key(dict(ef))
            conf = ef["confidence_score"] or 0
            if key not in existing_index or conf > existing_index[key][1]:
                existing_index[key] = (ef["id"], conf)

        already_marked = set()  # 已被标记的 fact id，避免重复 UPDATE

        for nf in new_facts:
            key = _build_dedup_key(dict(nf))
            if key not in existing_index:
                continue

            existing_id, existing_conf = existing_index[key]
            new_conf = nf["confidence_score"] or 0

            if new_conf <= existing_conf:
                # 新的分数不高于已有的，标记新的为 DUPLICATE
                conn.execute(
                    f"""UPDATE fact_atom
                    SET review_status='重复', review_note=?
                    WHERE id=? AND {_ACTIVE_FILTER}""",
                    (f"重复: 与 {existing_id} 重复（跨文档）", nf["id"]),
                )
                marked += 1
            elif existing_id not in already_marked:
                # 新的分数更高，标记旧的为 DUPLICATE，新的成为正本
                conn.execute(
                    f"""UPDATE fact_atom
                    SET review_status='重复', review_note=?
                    WHERE id=? AND {_ACTIVE_FILTER}""",
                    (f"重复: 与 {nf['id']} 重复（跨文档，分数更低）", existing_id),
                )
                already_marked.add(existing_id)
                marked += 1

        if marked > 0:
            conn.commit()
            logger.info("[doc=%s] 跨文档去重: 标记 %d 条", document_id[:8], marked)
    finally:
        conn.close()
    return marked


def _dedup_cross_type(document_id: str) -> int:
    """跨类型去重：无金额 INVESTMENT 且与同主体 EXPANSION 重叠 → 标记 DUPLICATE。

    保留逻辑：
    - INVESTMENT 有 value_num → 保留（它提供了 EXPANSION 没有的投资金额信息）
    - INVESTMENT 无 value_num 且同文档有同主体同地点 EXPANSION → 标记 DUPLICATE
    """
    conn = get_connection()
    marked = 0
    try:
        # 无金额的 INVESTMENT
        investments = conn.execute(
            f"""SELECT id, subject_text, location_text
            FROM fact_atom
            WHERE document_id = ? AND fact_type = 'INVESTMENT'
              AND value_num IS NULL AND {_ACTIVE_FILTER}""",
            (document_id,),
        ).fetchall()

        if not investments:
            return 0

        # 同文档的 EXPANSION
        expansions = conn.execute(
            f"""SELECT id, subject_text, location_text
            FROM fact_atom
            WHERE document_id = ? AND fact_type = 'EXPANSION'
              AND {_ACTIVE_FILTER}""",
            (document_id,),
        ).fetchall()

        if not expansions:
            return 0

        # EXPANSION 索引 (normalized_subject, normalized_location) → id
        exp_index = set()
        exp_id_map = {}
        for exp in expansions:
            subj = _normalize_subject(exp["subject_text"])
            loc = (exp["location_text"] or "").strip().lower()
            k = (subj, loc)
            exp_index.add(k)
            exp_id_map[k] = exp["id"]

        for inv in investments:
            subj = _normalize_subject(inv["subject_text"])
            loc = (inv["location_text"] or "").strip().lower()
            k = (subj, loc)

            if k in exp_index:
                exp_id = exp_id_map[k]
                conn.execute(
                    f"""UPDATE fact_atom
                    SET review_status='重复', review_note=?
                    WHERE id=? AND {_ACTIVE_FILTER}""",
                    (f"重复: 无金额INVESTMENT，与EXPANSION {exp_id} 重叠", inv["id"]),
                )
                marked += 1

        if marked > 0:
            conn.commit()
            logger.info("[doc=%s] 跨类型去重: 标记 %d 条", document_id[:8], marked)
    finally:
        conn.close()
    return marked
