"""Agent 2：Fact Extractor —— 事实抽取"""

import json
import uuid
from pathlib import Path

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection
from app.services.llm_client import get_llm_client

logger = get_logger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_common_prompt() -> str:
    return (_PROMPT_DIR / "fact_extractor_common.txt").read_text(encoding="utf-8")


def _load_fact_type_rules(fact_type: str) -> str:
    """加载 fact_type 局部规则"""
    rule_file = _PROMPT_DIR / "fact_type_rules" / f"{fact_type.lower()}.txt"
    if rule_file.exists():
        return rule_file.read_text(encoding="utf-8")
    return ""


def extract_facts(
    evidence_id: str,
    evidence_text: str,
    fact_type: str,
    document_id: str,
    doc_title: str = "",
    doc_source: str = "",
    doc_publish_time: str = "",
) -> list[dict]:
    """
    从单条 evidence 中抽取结构化事实。

    返回:
        [{"fact_atom_id": ..., "fact_type": ..., "subject": ..., ...}, ...]
    """
    cfg = get_config()

    # 组装 Prompt：通用 + fact_type 局部规则
    system_prompt = _load_common_prompt()
    type_rules = _load_fact_type_rules(fact_type)
    if type_rules:
        system_prompt += "\n\n" + type_rules

    user_input = json.dumps(
        {
            "document_title": doc_title,
            "document_source": doc_source,
            "document_publish_time": doc_publish_time,
            "fact_type": fact_type,
            "evidence_text": evidence_text,
        },
        ensure_ascii=False,
    )

    client = get_llm_client()
    task_id = str(uuid.uuid4())
    _record_task_start(task_id, document_id, "事实抽取")

    try:
        result = client.chat_json(system_prompt, user_input)
        raw_data = result["data"]
        _record_task_end(
            task_id, "成功",
            result["input_tokens"], result["output_tokens"],
            result["model"],
        )
    except Exception as e:
        _record_task_end(task_id, "失败", error=str(e))
        logger.error("Fact Extractor 调用失败 [evidence=%s]: %s", evidence_id[:8], e)
        return []

    # 确保返回列表
    records = raw_data if isinstance(raw_data, list) else [raw_data]

    # 校验 + 写入
    fact_atoms = []
    conn = get_connection()
    try:
        for rec in records:
            validated = _validate_record(rec, fact_type, cfg)
            if validated is None:
                continue

            fact_atom_id = str(uuid.uuid4())

            conn.execute(
                """INSERT INTO fact_atom
                (id, document_id, evidence_span_id, fact_type,
                 subject_text, predicate, object_text,
                 value_num, value_text, unit, currency,
                 time_expr, location_text, qualifier_json,
                 confidence_score, extraction_model, extraction_version,
                 review_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '待处理')""",
                (
                    fact_atom_id,
                    document_id,
                    evidence_id,
                    validated["fact_type"],
                    validated.get("subject"),
                    validated["predicate"],
                    validated.get("object"),
                    validated.get("value_num"),
                    validated.get("value_text"),
                    validated.get("unit"),
                    validated.get("currency"),
                    validated.get("time_expr"),
                    validated.get("location"),
                    json.dumps(validated.get("qualifiers", {}), ensure_ascii=False),
                    validated.get("confidence", 0.0),
                    result["model"],
                    "v1.0",
                ),
            )

            fact_atoms.append({
                "fact_atom_id": fact_atom_id,
                **validated,
            })

        conn.commit()
    finally:
        conn.close()

    logger.info(
        "[evidence=%s] 抽取 %d 条事实原子",
        evidence_id[:8], len(fact_atoms),
    )
    return fact_atoms


def _validate_record(
    rec: dict, expected_type: str, cfg: dict,
) -> dict | None:
    """校验抽取记录：fact_type、predicate 白名单、qualifiers 格式"""
    if not isinstance(rec, dict):
        logger.warning("无效记录格式: %s", type(rec))
        return None

    fact_type = rec.get("fact_type", expected_type)
    predicate = rec.get("predicate")

    # 必填校验
    if not predicate:
        logger.warning("缺少 predicate，跳过记录")
        return None

    subject = rec.get("subject")
    if not subject or not str(subject).strip():
        logger.warning(
            "subject 为空，跳过记录 (fact_type=%s, predicate=%s)",
            fact_type, predicate,
        )
        return None

    subject = str(subject).strip()

    # subject 质量校验：拒绝度量词/统计概念作为 subject
    _SUBJECT_REJECT_SUFFIXES = (
        "完工量", "订单量", "交付量", "产量", "销售量", "消费量",
        "出货量", "保有量", "增长量", "需求量",
    )
    _SUBJECT_REJECT_KEYWORDS = (
        "市场规模", "行业规模", "市场容量",
    )
    if subject.endswith(_SUBJECT_REJECT_SUFFIXES):
        logger.warning(
            "subject '%s' 是度量词而非实体，跳过记录 (fact_type=%s)",
            subject, fact_type,
        )
        return None
    if subject in _SUBJECT_REJECT_KEYWORDS:
        logger.warning(
            "subject '%s' 是统计概念而非实体，跳过记录 (fact_type=%s)",
            subject, fact_type,
        )
        return None

    # fact_type 白名单
    valid_types = cfg.get("fact_types", [])
    if valid_types and fact_type not in valid_types:
        logger.warning("未知 fact_type: %s", fact_type)
        return None

    # predicate 白名单模糊校验（词根匹配：白名单词根出现在 predicate 中即视为匹配）
    pred_whitelist = cfg.get("predicate_whitelist", {}).get(fact_type, [])
    if pred_whitelist:
        matched = any(root in predicate for root in pred_whitelist)
        if not matched:
            logger.info(
                "predicate '%s' 未匹配白名单任何词根（fact_type=%s），保留但标记",
                predicate, fact_type,
            )

    # qualifiers 格式校验
    qualifiers = rec.get("qualifiers", {})
    if not isinstance(qualifiers, dict):
        qualifiers = {}

    # qualifiers 白名单警告
    qual_whitelist = cfg.get("qualifier_whitelist", {}).get(fact_type, [])
    if qual_whitelist:
        extra_keys = set(qualifiers.keys()) - set(qual_whitelist)
        if extra_keys:
            logger.info(
                "额外 qualifiers 字段: %s (fact_type=%s)",
                extra_keys, fact_type,
            )

    rec["fact_type"] = fact_type
    rec["qualifiers"] = qualifiers
    return rec


def _record_task_start(task_id: str, document_id: str, task_type: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO extraction_task
            (id, document_id, task_type, status, started_at)
            VALUES (?, ?, ?, '运行中', CURRENT_TIMESTAMP)""",
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
