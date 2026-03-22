"""全文级事实抽取：合并 Evidence Finder + Fact Extractor 为单次 LLM 调用。

输入：清洗后的完整文章文本
输出：结构化事实原子列表（同时创建 evidence_span + fact_atom 记录）

LLM 输出采用 list-of-lists 格式以减少 token 消耗：
每条事实是 13 元素的列表，按固定位置映射字段名。
"""

import json
import uuid
from pathlib import Path

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection
from app.services.llm_client import get_llm_client
from app.services.entity_linker import get_known_entities_context

logger = get_logger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_RULES_DIR = _PROMPT_DIR / "fact_type_rules"

# 列表位置 → 字段名映射
_FIELD_NAMES = [
    "fact_type",      # [0]
    "subject",        # [1]
    "predicate",      # [2]
    "object",         # [3]
    "value_num",      # [4]
    "value_text",     # [5]
    "unit",           # [6]
    "currency",       # [7]
    "time_expr",      # [8]
    "location",       # [9]
    "qualifiers",     # [10]
    "confidence",     # [11]
    "evidence_text",  # [12]
]




def _load_prompt() -> str:
    """加载全文级 prompt：基础规则 + 所有 fact_type 局部规则"""
    base = (_PROMPT_DIR / "fact_extractor_full.txt").read_text(encoding="utf-8")

    # 动态拼接所有 fact_type 规则
    rules_parts = []
    for rule_file in sorted(_RULES_DIR.glob("*.txt")):
        rules_parts.append(rule_file.read_text(encoding="utf-8"))

    if rules_parts:
        base += "\n\n## Fact-type specific rules\n\n"
        base += "\n\n".join(rules_parts)

    # 注入已知实体及关系上下文
    entity_context = get_known_entities_context()
    if entity_context:
        base += "\n\n" + entity_context

    return base


def _list_to_dict(record: list) -> dict | None:
    """将 13 元素列表转为命名字典，校验长度"""
    if not isinstance(record, list) or len(record) < 13:
        logger.warning("无效记录：非列表或长度不足 13，跳过")
        return None

    result = {}
    for i, field_name in enumerate(_FIELD_NAMES):
        result[field_name] = record[i]

    return result


def _validate_record(rec: dict, cfg: dict) -> dict | None:
    """校验记录：fact_type、subject、predicate 等"""
    fact_type = rec.get("fact_type", "")
    predicate = rec.get("predicate")

    if not predicate:
        logger.warning("缺少 predicate，跳过")
        return None

    subject = rec.get("subject")
    if not subject or not str(subject).strip():
        logger.warning("subject 为空，跳过 (predicate=%s)", predicate)
        return None

    subject = str(subject).strip()

    # fact_type 白名单
    valid_types = cfg.get("fact_types", [])
    if valid_types and fact_type not in valid_types:
        logger.warning("未知 fact_type: %s", fact_type)
        return None

    # predicate 白名单模糊校验
    pred_whitelist = cfg.get("predicate_whitelist", {}).get(fact_type, [])
    if pred_whitelist:
        matched = any(root in predicate for root in pred_whitelist)
        if not matched:
            logger.info(
                "predicate '%s' 未匹配白名单词根（fact_type=%s），保留但标记",
                predicate, fact_type,
            )

    # qualifiers 格式：确保是 dict
    qualifiers = rec.get("qualifiers", {})
    if not isinstance(qualifiers, dict):
        qualifiers = {}

    # confidence 确保是数值类型（float 或 int），防止 dict 等非法类型
    confidence = rec.get("confidence")
    if confidence is None:
        confidence = 0.0
    elif isinstance(confidence, bool):  # bool 是 int 的子类，需先排除
        confidence = 0.0
    elif not isinstance(confidence, (int, float)):
        # 如果是 dict 或其他类型，提取其中的数值（如果有）
        if isinstance(confidence, dict):
            logger.warning("confidence 是 dict，尝试提取其中的值")
            confidence = 0.0
        else:
            confidence = 0.0
    rec["confidence"] = float(confidence)

    # evidence_text 确保是字符串
    evidence_text = rec.get("evidence_text", "")
    if not isinstance(evidence_text, str):
        evidence_text = str(evidence_text) if evidence_text else ""
    rec["evidence_text"] = evidence_text

    # qualifiers 白名单警告
    qual_whitelist = cfg.get("qualifier_whitelist", {}).get(fact_type, [])
    if qual_whitelist:
        extra_keys = set(qualifiers.keys()) - set(qual_whitelist)
        if extra_keys:
            logger.info(
                "额外 qualifiers 字段: %s (fact_type=%s)", extra_keys, fact_type,
            )

    rec["fact_type"] = fact_type
    rec["subject"] = subject
    rec["qualifiers"] = qualifiers
    return rec


def extract_facts_full_text(
    cleaned_text: str,
    document_id: str,
    chunk_id: str,
    doc_title: str = "",
    doc_source: str = "",
    doc_publish_time: str = "",
) -> list[dict]:
    """
    从完整文章文本一次性提取所有结构化事实。

    合并 Evidence Finder + Fact Extractor 的功能。
    创建 evidence_span 和 fact_atom 记录。

    参数:
        cleaned_text: 清洗后的全文
        document_id: 文档 ID
        chunk_id: 全文对应的 chunk ID（DB 兼容）
        doc_title: 文档标题
        doc_source: 来源
        doc_publish_time: 发布时间

    返回:
        [{"fact_atom_id": ..., "evidence_id": ..., "evidence_text": ...,
          "fact_record": {...}}, ...]
    """
    cfg = get_config()
    system_prompt = _load_prompt()

    user_input = json.dumps(
        {
            "document_title": doc_title,
            "document_source": doc_source,
            "document_publish_time": doc_publish_time,
            "article_text": cleaned_text,
        },
        ensure_ascii=False,
    )

    client = get_llm_client()
    task_id = str(uuid.uuid4())
    _record_task_start(task_id, document_id, "full_extractor")

    try:
        result = client.chat_json(system_prompt, user_input)
        raw_data = result["data"]
        _record_task_end(
            task_id, "success",
            result["input_tokens"], result["output_tokens"],
            result["model"],
        )
    except Exception as e:
        _record_task_end(task_id, "failed", error=str(e))
        logger.error("全文抽取调用失败 [doc=%s]: %s", document_id[:8], e)
        # 记录错误到文档
        from app.models.db import get_connection
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE source_document SET error_message=? WHERE id=?",
                (str(e)[:500], document_id),
            )
            conn.commit()
        finally:
            conn.close()
        return []

    # 确保是列表
    if not isinstance(raw_data, list):
        raw_data = [raw_data]

    # 解析列表格式 → 字典
    parsed_records = []
    for item in raw_data:
        # 支持 list-of-lists（列表格式）和 list-of-dicts（兼容旧格式）
        if isinstance(item, list):
            rec = _list_to_dict(item)
        elif isinstance(item, dict):
            rec = item
        else:
            logger.warning("跳过无效记录类型: %s", type(item))
            continue

        if rec is None:
            continue

        validated = _validate_record(rec, cfg)
        if validated is not None:
            parsed_records.append(validated)

    logger.info("[doc=%s] 全文抽取得到 %d 条有效记录", document_id[:8], len(parsed_records))

    # 写入数据库：evidence_span + fact_atom
    fact_results = []
    evidence_cache = {}  # (fact_type, evidence_text) → evidence_id

    conn = get_connection()
    try:
        for rec in parsed_records:
            evidence_text = rec.get("evidence_text", "")
            fact_type = rec["fact_type"]

            if not evidence_text:
                logger.warning("记录缺少 evidence_text，跳过")
                continue

            # 创建或复用 evidence_span
            ev_key = (fact_type, evidence_text)
            if ev_key in evidence_cache:
                evidence_id = evidence_cache[ev_key]
            else:
                evidence_id = str(uuid.uuid4())
                # INSERT OR IGNORE 处理全局唯一约束
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO evidence_span
                    (id, document_id, chunk_id, evidence_text,
                     fact_type, priority, extraction_task_id)
                    VALUES (?, ?, ?, ?, ?, 'high', ?)""",
                    (evidence_id, document_id, chunk_id,
                     evidence_text, fact_type, task_id),
                )
                if cursor.rowcount == 0:
                    # 已存在，查找实际 ID
                    row = conn.execute(
                        """SELECT id FROM evidence_span
                        WHERE document_id=? AND fact_type=? AND evidence_text=?""",
                        (document_id, fact_type, evidence_text),
                    ).fetchone()
                    if row:
                        evidence_id = row["id"]
                evidence_cache[ev_key] = evidence_id

            # 创建 fact_atom
            fact_atom_id = str(uuid.uuid4())
            qualifiers_json = json.dumps(
                rec.get("qualifiers", {}), ensure_ascii=False,
            )

            conn.execute(
                """INSERT INTO fact_atom
                (id, document_id, evidence_span_id, fact_type,
                 subject_text, predicate, object_text,
                 value_num, value_text, unit, currency,
                 time_expr, location_text, qualifier_json,
                 confidence_score, extraction_model, extraction_version,
                 review_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')""",
                (
                    fact_atom_id,
                    document_id,
                    evidence_id,
                    fact_type,
                    rec.get("subject"),
                    rec["predicate"],
                    rec.get("object"),
                    rec.get("value_num"),
                    rec.get("value_text"),
                    rec.get("unit"),
                    rec.get("currency"),
                    rec.get("time_expr"),
                    rec.get("location"),
                    qualifiers_json,
                    rec.get("confidence", 0.0),
                    result["model"],
                    "v2.0",
                ),
            )

            fact_results.append({
                "fact_atom_id": fact_atom_id,
                "evidence_id": evidence_id,
                "evidence_text": evidence_text,
                "fact_record": rec,
            })

        conn.commit()
    finally:
        conn.close()

    logger.info(
        "[doc=%s] 写入 %d 条 fact_atom, %d 条 evidence_span",
        document_id[:8], len(fact_results), len(evidence_cache),
    )
    return fact_results


def _record_task_start(task_id: str, document_id: str, task_type: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO extraction_task
            (id, document_id, task_type, status, started_at)
            VALUES (?, ?, ?, 'running', CURRENT_TIMESTAMP)""",
            (task_id, document_id, task_type),
        )
        conn.commit()
    finally:
        conn.close()


def _record_task_end(
    task_id: str, status: str,
    input_tokens: int = 0, output_tokens: int = 0,
    model: str = "", error: str = "",
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE extraction_task SET
            status=?, input_tokens=?, output_tokens=?,
            model_name=?, finished_at=CURRENT_TIMESTAMP, error_message=?
            WHERE id=?""",
            (status, input_tokens, output_tokens, model, error, task_id),
        )
        conn.commit()
    finally:
        conn.close()
