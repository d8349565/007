"""完整处理链路编排 —— 导入 → 清洗 → 切分 → 证据发现 → 事实抽取 → 审核 → 实体链接"""

import json
from datetime import datetime

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection
from app.services.cleaner import clean_text
from app.services.text_splitter import split_text
from app.services.evidence_finder import find_evidence
from app.services.fact_extractor import extract_facts
from app.services.reviewer import review_fact
from app.services.entity_linker import batch_link_fact_atoms

logger = get_logger(__name__)


def process_document(document_id: str) -> dict:
    """
    处理单篇文档：全链路从清洗到审核。

    参数:
        document_id: source_document 表中的 id

    返回:
        {"document_id": ..., "chunks": int, "evidences": int,
         "facts": int, "passed": int, "rejected": int, "uncertain": int}
    """
    stats = {
        "document_id": document_id,
        "chunks": 0,
        "evidences": 0,
        "facts": 0,
        "passed": 0,
        "rejected": 0,
        "uncertain": 0,
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

    # 2. 文本清洗
    cleaned = clean_text(raw_text)
    if not cleaned.strip():
        logger.warning("清洗后内容为空: %s", document_id)
        _mark_document_status(document_id, "empty_after_clean")
        return stats

    # 3. 文本切分
    cfg = get_config()
    chunk_dicts = split_text(cleaned, doc_id=document_id)
    chunks = [c["chunk_text"] for c in chunk_dicts]
    stats["chunks"] = len(chunks)
    logger.info("切分为 %d 个 chunk", len(chunks))

    # 4. 存储 chunks + 链路处理
    import uuid

    all_fact_atom_ids = []

    for i, chunk_text in enumerate(chunks):
        chunk_id = str(uuid.uuid4())

        # 写入 document_chunk
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO document_chunk
                (id, document_id, chunk_index, chunk_text, char_count)
                VALUES (?, ?, ?, ?, ?)""",
                (chunk_id, document_id, i, chunk_text, len(chunk_text)),
            )
            conn.commit()
        finally:
            conn.close()

        # 4a. Agent 1: 证据发现
        evidences = find_evidence(
            chunk_id=chunk_id,
            chunk_text=chunk_text,
            document_id=document_id,
            doc_title=doc_title,
            doc_source=doc_source,
            doc_publish_time=doc_publish_time,
        )
        stats["evidences"] += len(evidences)

        # 4b. Agent 2: 事实抽取（按 evidence 逐条）
        for ev in evidences:
            facts = extract_facts(
                evidence_id=ev["evidence_id"],
                evidence_text=ev["evidence_text"],
                fact_type=ev["fact_type"],
                document_id=document_id,
                doc_title=doc_title,
                doc_source=doc_source,
                doc_publish_time=doc_publish_time,
            )
            stats["facts"] += len(facts)

            # 4c. Agent 3: 审核校验（按 fact 逐条）
            for fact in facts:
                review_result = review_fact(
                    fact_atom_id=fact["fact_atom_id"],
                    fact_record=fact,
                    evidence_text=ev["evidence_text"],
                    document_id=document_id,
                )

                verdict = review_result.get("verdict", "UNCERTAIN")
                if verdict == "PASS":
                    stats["passed"] += 1
                elif verdict == "REJECT":
                    stats["rejected"] += 1
                else:
                    stats["uncertain"] += 1

                all_fact_atom_ids.append(fact["fact_atom_id"])

    # 5. 实体链接
    if all_fact_atom_ids:
        batch_link_fact_atoms(all_fact_atom_ids)

    # 6. 更新文档状态
    _mark_document_status(document_id, "processed")

    logger.info(
        "文档处理完成: %s — chunks=%d, evidences=%d, facts=%d, pass=%d, reject=%d, uncertain=%d",
        document_id[:8],
        stats["chunks"], stats["evidences"], stats["facts"],
        stats["passed"], stats["rejected"], stats["uncertain"],
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
            results.append({
                "document_id": doc_id,
                "error": str(e),
            })

    return results


def _mark_document_status(document_id: str, status: str) -> None:
    """更新文档的处理状态"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE source_document SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, document_id),
        )
        conn.commit()
    finally:
        conn.close()
