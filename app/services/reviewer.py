"""Agent 3：Reviewer / Validator —— 审核校验"""

import json
import uuid
from pathlib import Path

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection
from app.services.llm_client import get_llm_client

logger = get_logger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt() -> str:
    return (_PROMPT_DIR / "reviewer.txt").read_text(encoding="utf-8")


def review_fact(
    fact_atom_id: str,
    fact_record: dict,
    evidence_text: str,
    document_id: str,
) -> dict:
    """
    对一条 fact_atom 进行审核校验。

    返回:
        {"verdict": "PASS|REJECT|UNCERTAIN", "score": float, "issues": [...], "review_note": str}
    """
    cfg = get_config()
    system_prompt = _load_prompt()

    user_input = json.dumps(
        {
            "fact_type": fact_record.get("fact_type", ""),
            "evidence_text": evidence_text,
            "fact_record": fact_record,
        },
        ensure_ascii=False,
    )

    client = get_llm_client()
    task_id = str(uuid.uuid4())
    _record_task_start(task_id, document_id, "reviewer")

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
        logger.error("Reviewer 调用失败 [fact=%s]: %s", fact_atom_id[:8], e)
        return {
            "verdict": "UNCERTAIN",
            "score": 0.0,
            "issues": [{"field": "system", "issue": f"审核调用失败: {e}"}],
            "review_note": "审核调用异常，进入人工审核池",
        }

    verdict = data.get("verdict", "UNCERTAIN").upper()
    score = data.get("score", 0.0)
    issues = data.get("issues", [])
    review_note = data.get("review_note", "")

    # 映射 verdict → review_status
    review_status = _map_verdict_to_status(verdict, score, fact_record, cfg)

    # 更新 fact_atom 的 review_status
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE fact_atom SET
            review_status=?, review_note=?, confidence_score=?,
            updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (review_status, review_note, score, fact_atom_id),
        )

        # 写入 review_log
        conn.execute(
            """INSERT INTO review_log
            (id, target_type, target_id, old_status, new_status,
             reviewer, review_action, review_note)
            VALUES (?, 'fact_atom', ?, 'PENDING', ?, 'system_reviewer', ?, ?)""",
            (str(uuid.uuid4()), fact_atom_id, review_status,
             verdict.lower(), review_note),
        )

        conn.commit()
    finally:
        conn.close()

    logger.info(
        "[fact=%s] 审核结果: %s (score=%.2f) → %s",
        fact_atom_id[:8], verdict, score, review_status,
    )

    return {
        "verdict": verdict,
        "score": score,
        "issues": issues,
        "review_note": review_note,
        "review_status": review_status,
    }


def _map_verdict_to_status(
    verdict: str, score: float, fact_record: dict, cfg: dict,
) -> str:
    """将 Reviewer 判定映射为最终 review_status"""
    review_cfg = cfg.get("review", {})
    auto_pass_threshold = review_cfg.get("auto_pass_confidence", 0.90)
    force_human_types = review_cfg.get("force_human_review_types", [])
    force_human_preds = review_cfg.get("force_human_review_predicates", [])

    fact_type = fact_record.get("fact_type", "")
    qualifiers = fact_record.get("qualifiers", {})

    # REJECT → 直接 REJECTED
    if verdict == "REJECT":
        return "REJECTED"

    # UNCERTAIN → 进入人工审核池
    if verdict == "UNCERTAIN":
        return "HUMAN_REVIEW_REQUIRED"

    # PASS 但需要强制人工审核
    if fact_type in force_human_types:
        return "HUMAN_REVIEW_REQUIRED"

    # 检查是否包含需要强制审核的 qualifier（如 yoy/qoq）
    for pred in force_human_preds:
        if pred in qualifiers:
            return "HUMAN_REVIEW_REQUIRED"

    # PASS 且分数足够高
    if verdict == "PASS" and score >= auto_pass_threshold:
        return "AUTO_PASS"

    # PASS 但分数不够高
    return "HUMAN_REVIEW_REQUIRED"


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
