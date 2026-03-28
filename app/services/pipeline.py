"""完整处理链路编排 —— 导入 → 清洗 → 全文抽取 → 审核 → 实体链接"""

import json
import uuid

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection
from app.services.cleaner import clean_text
from app.services.full_extractor import extract_facts_full_text
from app.services.reviewer import review_document_facts
from app.services.entity_linker import batch_link_fact_atoms
from app.services.deduplicator import deduplicate_facts
from app.services.query import clear_document_results

logger = get_logger(__name__)


def process_document(document_id: str) -> dict:
    """
    处理单篇文档：清洗 → 全文抽取 → 审核 → 实体链接。

    全文模式：将清洗后的完整文章一次性交给 LLM 分析，
    不再切分 chunk，保留完整上下文。

    参数:
        document_id: source_document 表中的 id

    返回:
        {"document_id": ..., "evidences": int,
         "facts": int, "passed": int, "rejected": int, "uncertain": int}
    """
    stats = {
        "document_id": document_id,
        "evidences": 0,
        "facts": 0,
        "passed": 0,
        "rejected": 0,
        "uncertain": 0,
        "duplicates": 0,
    }

    # 1. 获取文档信息
    conn = get_connection()
    try:
        doc = conn.execute(
            "SELECT * FROM source_document WHERE id=?", (document_id,)
        ).fetchone()
    finally:
        conn.close()

    if not doc:
        logger.error("文档不存在: %s", document_id)
        return stats

    doc_title = doc["title"] or ""
    doc_source = doc["source_name"] or ""
    doc_publish_time = doc["publish_time"] or ""
    raw_text = doc["raw_text"] or ""

    if not raw_text.strip():
        logger.warning("文档内容为空: %s", document_id)
        _mark_document_status(document_id, "empty")
        return stats

    logger.info("开始处理文档: %s [%s]", doc_title, document_id[:8])

    # 标记为处理中（在开始处理时立即设置，便于 Web 端识别）
    _mark_document_status(document_id, "processing")

    # 幂等性保护：清除此文档的旧处理结果，防止重跑产生重复
    clear_document_results(document_id)

    # 2. 文本清洗
    _mark_document_status(document_id, "cleaning")
    cleaned = clean_text(raw_text)
    if not cleaned.strip():
        logger.warning("清洗后内容为空: %s", document_id)
        _mark_document_status(document_id, "empty_after_clean")
        return stats

    # 3. 存储全文为单个 chunk（DB 兼容）
    chunk_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO document_chunk
            (id, document_id, chunk_index, chunk_text, char_count)
            VALUES (?, ?, 0, ?, ?)""",
            (chunk_id, document_id, cleaned, len(cleaned)),
        )
        conn.commit()
    finally:
        conn.close()

    # 4. 全文抽取（Agent 1+2 合并：一次 LLM 调用完成证据发现 + 事实抽取）
    _mark_document_status(document_id, "extracting")
    fact_results = extract_facts_full_text(
        cleaned_text=cleaned,
        document_id=document_id,
        chunk_id=chunk_id,
        doc_title=doc_title,
        doc_source=doc_source,
        doc_publish_time=doc_publish_time,
    )
    stats["facts"] = len(fact_results)

    # 统计 evidence 数量（去重）
    evidence_ids = {fr["evidence_id"] for fr in fact_results}
    stats["evidences"] = len(evidence_ids)

    # 5. 结构性审核（一次 LLM 调用审核所有 fact）
    all_fact_atom_ids = []

    if fact_results:
        _mark_document_status(document_id, "reviewing")
        facts_with_ids = [
            (fr["fact_atom_id"], fr["fact_record"]) for fr in fact_results
        ]
        review_results = review_document_facts(
            facts_with_ids=facts_with_ids,
            document_id=document_id,
        )
        for rr in review_results:
            verdict = rr.get("verdict", "UNCERTAIN")
            if verdict == "PASS":
                stats["passed"] += 1
            elif verdict == "REJECT":
                stats["rejected"] += 1
            else:
                stats["uncertain"] += 1
            all_fact_atom_ids.append(rr["fact_atom_id"])

    # 6. 自动去重（同文档 + 跨文档 + 跨类型）
    if fact_results:
        _mark_document_status(document_id, "deduplicating")
        dedup_stats = deduplicate_facts(document_id)
        stats["duplicates"] = sum(dedup_stats.values())

    # 7. 实体链接（排除已标记 DUPLICATE / REJECTED 的事实）
    if all_fact_atom_ids:
        _mark_document_status(document_id, "linking")
        conn = get_connection()
        try:
            placeholders = ",".join("?" * len(all_fact_atom_ids))
            active_ids = [
                row["id"] for row in
                conn.execute(
                    f"""SELECT id FROM fact_atom
                    WHERE id IN ({placeholders})
                      AND review_status NOT IN ('DUPLICATE', 'REJECTED')""",
                    all_fact_atom_ids,
                ).fetchall()
            ]
        finally:
            conn.close()
        if active_ids:
            batch_link_fact_atoms(active_ids)

    # 8. 更新文档状态
    _mark_document_status(document_id, "processed")

    logger.info(
        "文档处理完成: %s — evidences=%d, facts=%d, pass=%d, reject=%d, uncertain=%d, dup=%d",
        document_id[:8],
        stats["evidences"], stats["facts"],
        stats["passed"], stats["rejected"], stats["uncertain"],
        stats["duplicates"],
    )

    return stats


def process_batch(document_ids: list[str], show_progress: bool = True) -> list[dict]:
    """批量处理多篇文档"""
    results = []

    if show_progress:
        try:
            from tqdm import tqdm
            iterator = tqdm(document_ids, desc="处理文档", unit="篇")
        except ImportError:
            iterator = document_ids
    else:
        iterator = document_ids

    for doc_id in iterator:
        try:
            result = process_document(doc_id)
            results.append(result)
        except Exception as e:
            logger.error("处理文档失败 [%s]: %s", doc_id[:8], e)
            _mark_document_status(doc_id, "failed", error_message=str(e)[:500])
            results.append({
                "document_id": doc_id,
                "error": str(e),
            })

    return results


def _mark_document_status(document_id: str, status: str, error_message: str = None) -> None:
    """更新文档的处理状态"""
    conn = get_connection()
    try:
        if error_message:
            conn.execute(
                "UPDATE source_document SET status=?, error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, error_message, document_id),
            )
        else:
            conn.execute(
                "UPDATE source_document SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, document_id),
            )
        conn.commit()
    finally:
        conn.close()
