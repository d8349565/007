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

    # 检查是否包含需要强制审核的 qualifier key（如 is_forecast、yoy/qoq）
    for qual_key in force_human_preds:
        if qual_key in qualifiers:
            return "HUMAN_REVIEW_REQUIRED"

    # predicate 白名单模糊校验：不匹配仅记录日志，不阻断 AUTO_PASS
    # (白名单作为"期望词表"辅助监控，不作为硬性门禁)
    predicate_whitelist = cfg.get("predicate_whitelist", {}).get(fact_type, [])
    if predicate_whitelist:
        pred_val = fact_record.get("predicate", "")
        if pred_val and not any(root in pred_val for root in predicate_whitelist):
            logger.debug(
                "predicate '%s' 未匹配白名单 (fact_type=%s)，继续评估",
                pred_val, fact_type,
            )

    # 关键限定词存在性守门：特定 fact_type 若缺少上下文限定词则不允许 AUTO_PASS
    # 配置格式: review.require_qualifier_any: {FACT_TYPE: [key1, key2, ...]}
    # 含义：qualifiers 中至少有一个 key 有非空值，否则降级到人工审核
    require_qualifier_any = review_cfg.get("require_qualifier_any", {})
    required_any = require_qualifier_any.get(fact_type, [])
    if required_any and not any(qualifiers.get(k) for k in required_any):
        logger.info(
            "fact_type=%s qualifiers 缺少上下文限定词 (需要其中之一: %s)，强制人工审核",
            fact_type, required_any,
        )
        return "HUMAN_REVIEW_REQUIRED"

    # MARKET_SHARE 定性守门：无量化数值时禁止 AUTO_PASS（保留人工确认）
    if fact_type == "MARKET_SHARE":
        if fact_record.get("value_num") is None and not fact_record.get("value_text"):
            logger.debug(
                "MARKET_SHARE 无量化数值 (predicate='%s')，保留人工审核",
                fact_record.get("predicate", ""),
            )
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


def review_facts_batch(
    facts_with_ids: list[tuple[str, dict]],
    evidence_text: str,
    document_id: str,
) -> list[dict]:
    """
    批量审核同一 evidence 下的多条 fact_atom。

    与 review_fact() 相比，将多条 facts 打包成一次 LLM 调用，
    共享 evidence_text，减少重复输入 token。

    参数:
        facts_with_ids: [(fact_atom_id, fact_record), ...]
        evidence_text: 共享的证据文本
        document_id: 文档 ID

    返回:
        [{"verdict": ..., "score": ..., "review_status": ..., "fact_atom_id": ...}, ...]
    """
    if not facts_with_ids:
        return []

    # 单条 fact 直接走原有逻辑（兼容性 + 避免数组开销）
    if len(facts_with_ids) == 1:
        fid, frec = facts_with_ids[0]
        result = review_fact(fid, frec, evidence_text, document_id)
        result["fact_atom_id"] = fid
        return [result]

    cfg = get_config()
    system_prompt = _load_prompt()

    # 构建批量输入：包含 evidence + 多条 fact_record
    fact_records_with_id = []
    for fid, frec in facts_with_ids:
        rec = dict(frec)
        rec["id"] = fid
        fact_records_with_id.append(rec)

    user_input = json.dumps(
        {
            "evidence_text": evidence_text,
            "fact_records": fact_records_with_id,
        },
        ensure_ascii=False,
    )

    client = get_llm_client()
    task_id = str(uuid.uuid4())
    _record_task_start(task_id, document_id, "reviewer")

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
        logger.error("Reviewer 批量调用失败: %s", e)
        # 全部标记 UNCERTAIN
        results = []
        for fid, frec in facts_with_ids:
            fallback = {
                "fact_atom_id": fid,
                "verdict": "UNCERTAIN",
                "score": 0.0,
                "issues": [{"field": "system", "issue": f"批量审核调用失败: {e}"}],
                "review_note": "审核调用异常，进入人工审核池",
                "review_status": "HUMAN_REVIEW_REQUIRED",
            }
            _persist_review(fid, "UNCERTAIN", 0.0, "HUMAN_REVIEW_REQUIRED", fallback["review_note"])
            results.append(fallback)
        return results

    # 解析批量结果
    if isinstance(raw_data, dict):
        raw_data = [raw_data]

    # 建立 fact_id → 审核结果的映射
    review_map = {}
    for item in raw_data:
        fid = item.get("fact_id", "")
        if fid:
            review_map[fid] = item

    results = []
    for fid, frec in facts_with_ids:
        item = review_map.get(fid, {})

        verdict = item.get("verdict", "UNCERTAIN").upper()
        score = item.get("score", 0.0)
        issues = item.get("issues", [])
        review_note = item.get("review_note", "")

        review_status = _map_verdict_to_status(verdict, score, frec, cfg)
        _persist_review(fid, verdict, score, review_status, review_note)

        logger.info(
            "[fact=%s] 审核结果: %s (score=%.2f) → %s",
            fid[:8], verdict, score, review_status,
        )

        results.append({
            "fact_atom_id": fid,
            "verdict": verdict,
            "score": score,
            "issues": issues,
            "review_note": review_note,
            "review_status": review_status,
        })

    return results


def _persist_review(
    fact_atom_id: str, verdict: str, score: float,
    review_status: str, review_note: str,
) -> None:
    """将审核结果写入 fact_atom 和 review_log"""
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE fact_atom SET
            review_status=?, review_note=?, confidence_score=?,
            updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (review_status, review_note, score, fact_atom_id),
        )
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


def batch_re_evaluate_pending() -> dict:
    """用当前 config 重新评估所有 HUMAN_REVIEW_REQUIRED 的事实原子，无需重新调用 LLM。

    逻辑：
    - confidence_score >= 0.65 视为 reviewer 当初给出 PASS（只是被旧配置的白名单或阈值阻断）
    - confidence_score < 0.65 视为 reviewer 给出 UNCERTAIN，保留人工审核
    - 用 _map_verdict_to_status 结合新 config 重新计算，若结果为 AUTO_PASS 则晋升

    返回:
        {"evaluated": int, "promoted": int, "kept": int}
    """
    cfg = get_config()
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, fact_type, predicate, object_text, value_num, value_text,
                      qualifier_json, confidence_score
               FROM fact_atom WHERE review_status = 'HUMAN_REVIEW_REQUIRED'"""
        ).fetchall()

        promoted = 0
        kept = 0
        for row in rows:
            score = row["confidence_score"] or 0.0
            # 置信度低于 0.65 的保留人工审核（推测 reviewer 当初给了 UNCERTAIN）
            if score < 0.65:
                kept += 1
                continue

            # 重新构造 fact_record 供 _map_verdict_to_status 使用
            try:
                qualifiers = json.loads(row["qualifier_json"] or "{}")
            except Exception:
                qualifiers = {}
            fact_record = {
                "fact_type": row["fact_type"],
                "predicate": row["predicate"],
                "qualifiers": qualifiers,
                "value_num": row["value_num"],
                "value_text": row["value_text"],
            }

            # 用新 config 重新评估（推断当时 verdict=PASS，只是被旧规则拦截）
            new_status = _map_verdict_to_status("PASS", score, fact_record, cfg)
            if new_status == "AUTO_PASS":
                conn.execute(
                    """UPDATE fact_atom
                       SET review_status='AUTO_PASS',
                           review_note='[批量重评估] 符合当前自动通过标准',
                           updated_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (row["id"],),
                )
                conn.execute(
                    """INSERT INTO review_log
                       (id, target_type, target_id, old_status, new_status,
                        reviewer, review_action, review_note)
                       VALUES (?, 'fact_atom', ?, 'HUMAN_REVIEW_REQUIRED', 'AUTO_PASS',
                               'batch_re_evaluate', 'pass', '批量重评估晋升')""",
                    (str(uuid.uuid4()), row["id"]),
                )
                promoted += 1
            else:
                kept += 1

        conn.commit()
        logger.info(
            "批量重评估完成: 共 %d 条，晋升 %d 条，保留 %d 条",
            len(rows), promoted, kept,
        )
        return {"evaluated": len(rows), "promoted": promoted, "kept": kept}
    finally:
        conn.close()


def review_document_facts(
    facts_with_ids: list[tuple[str, dict]],
    document_id: str,
) -> list[dict]:
    """
    对整篇文档的所有 fact_atom 做结构性审核（分批 LLM 调用）。

    审核内容：格式正确性、字段类型、主体合理性、字段归位、原子可还原性。
    不审核事实内容准确性。

    参数:
        facts_with_ids: [(fact_atom_id, fact_record), ...]
        document_id: 文档 ID

    返回:
        [{"fact_atom_id": ..., "verdict": ..., "score": ..., "review_status": ...}, ...]
    """
    if not facts_with_ids:
        return []

    cfg = get_config()
    system_prompt = _load_prompt()

    # 分批处理，每批最多 20 条，避免单次请求过大导致超时
    BATCH_SIZE = 20
    all_results = []

    for i in range(0, len(facts_with_ids), BATCH_SIZE):
        batch = facts_with_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(facts_with_ids) + BATCH_SIZE - 1) // BATCH_SIZE
        logger.info(
            "Reviewer 批次 %d/%d，处理 %d 条 fact",
            batch_num, total_batches, len(batch),
        )

        batch_result = _review_batch(batch, document_id, system_prompt, cfg)
        all_results.extend(batch_result)

    return all_results


def _review_batch(
    facts_with_ids: list[tuple[str, dict]],
    document_id: str,
    system_prompt: str,
    cfg: dict,
) -> list[dict]:
    """单批次的事实审核（一次 LLM 调用）"""
    fact_records_with_id = []
    for fid, frec in facts_with_ids:
        rec = dict(frec)
        rec["id"] = fid
        fact_records_with_id.append(rec)

    user_input = json.dumps(
        {"fact_records": fact_records_with_id},
        ensure_ascii=False,
    )

    client = get_llm_client()
    task_id = str(uuid.uuid4())
    _record_task_start(task_id, document_id, "reviewer")

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
        logger.error("Reviewer 批次调用失败 [%s]: %s", document_id[:8], e)
        results = []
        for fid, frec in facts_with_ids:
            _persist_review(
                fid, "UNCERTAIN", 0.0,
                "HUMAN_REVIEW_REQUIRED",
                f"审核调用异常: {e}，进入人工审核池",
            )
            results.append({
                "fact_atom_id": fid,
                "verdict": "UNCERTAIN",
                "score": 0.0,
                "issues": [],
                "review_note": f"审核调用异常: {e}",
                "review_status": "HUMAN_REVIEW_REQUIRED",
            })
        return results

    # 解析结果
    if isinstance(raw_data, dict):
        raw_data = [raw_data]

    review_map = {}
    for item in raw_data:
        fid = item.get("fact_id", "") or item.get("id", "")
        if fid:
            review_map[fid] = item

    results = []
    for fid, frec in facts_with_ids:
        item = review_map.get(fid, {})

        verdict = item.get("verdict", "UNCERTAIN").upper()
        score = item.get("score", 0.0)
        issues = item.get("issues", [])
        review_note = item.get("review_note", "")

        review_status = _map_verdict_to_status(verdict, score, frec, cfg)
        _persist_review(fid, verdict, score, review_status, review_note)

        logger.info(
            "[fact=%s] 结构审核: %s (score=%.2f) → %s",
            fid[:8], verdict, score, review_status,
        )

        results.append({
            "fact_atom_id": fid,
            "verdict": verdict,
            "score": score,
            "issues": issues,
            "review_note": review_note,
            "review_status": review_status,
        })

    return results
