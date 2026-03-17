"""Agent 1：Evidence Finder —— 证据发现"""

import json
import uuid
from pathlib import Path

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection
from app.services.llm_client import get_llm_client

logger = get_logger(__name__)

# 加载 Prompt 模板
_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt() -> str:
    path = _PROMPT_DIR / "evidence_finder.txt"
    return path.read_text(encoding="utf-8")


def find_evidence(
    chunk_id: str,
    chunk_text: str,
    document_id: str,
    doc_title: str = "",
    doc_source: str = "",
    doc_publish_time: str = "",
) -> list[dict]:
    """
    对一个 chunk 执行证据发现。

    返回:
        [{"evidence_id": ..., "fact_type": ..., "evidence_text": ..., "priority": ...}, ...]
    """
    cfg = get_config()
    system_prompt = _load_prompt()

    user_input = json.dumps(
        {
            "document_title": doc_title,
            "document_source": doc_source,
            "document_publish_time": doc_publish_time,
            "chunk_text": chunk_text,
        },
        ensure_ascii=False,
    )

    # 调用 LLM
    client = get_llm_client()
    task_id = str(uuid.uuid4())
    _record_task_start(task_id, document_id, chunk_id, "evidence_finder")

    try:
        result = client.chat_json(system_prompt, user_input)
        data = result["data"]
        _record_task_end(
            task_id, "success",
            result["input_tokens"], result["output_tokens"],
            result["model"],
        )
    except Exception as e:
        _record_task_end(task_id, "failed", error=str(e))
        logger.error("Evidence Finder 调用失败 [chunk=%s]: %s", chunk_id[:8], e)
        return []

    # 解析结果
    if not data.get("has_fact", False):
        logger.info("[%s] 未发现可抽取事实", chunk_id[:8])
        return []

    candidates = data.get("candidates", [])
    evidence_list = []

    conn = get_connection()
    try:
        for cand in candidates:
            evidence_id = str(uuid.uuid4())
            evidence_text = cand.get("evidence_text", "")
            fact_type = cand.get("fact_type", "")
            priority = cand.get("priority", "medium")

            if not evidence_text or not fact_type:
                continue

            # 验证 fact_type 在白名单内
            valid_types = cfg.get("fact_types", [])
            if valid_types and fact_type not in valid_types:
                logger.warning("未知 fact_type: %s，跳过", fact_type)
                continue

            # 写入 evidence_span 表
            conn.execute(
                """INSERT INTO evidence_span
                (id, document_id, chunk_id, evidence_text, fact_type, priority, extraction_task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (evidence_id, document_id, chunk_id, evidence_text,
                 fact_type, priority, task_id),
            )

            evidence_list.append({
                "evidence_id": evidence_id,
                "fact_type": fact_type,
                "evidence_text": evidence_text,
                "priority": priority,
            })

        conn.commit()
    finally:
        conn.close()

    logger.info(
        "[%s] 发现 %d 条证据", chunk_id[:8], len(evidence_list)
    )
    return evidence_list


def _record_task_start(
    task_id: str, document_id: str, chunk_id: str, task_type: str,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO extraction_task
            (id, document_id, chunk_id, task_type, status, started_at)
            VALUES (?, ?, ?, ?, 'running', CURRENT_TIMESTAMP)""",
            (task_id, document_id, chunk_id, task_type),
        )
        conn.commit()
    finally:
        conn.close()


def _record_task_end(
    task_id: str,
    status: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    model: str = "",
    error: str = "",
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
