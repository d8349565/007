"""完整处理链路编排 —— 导入 → 清洗 → 切分 → 证据发现 → 事实抽取 → 审核 → 实体链接"""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection
from app.services.cleaner import clean_text
from app.services.text_splitter import split_text
from app.services.evidence_finder import find_evidence
from app.services.fact_extractor import extract_facts
from app.services.reviewer import review_facts_batch
from app.services.entity_linker import batch_link_fact_atoms
from app.services.query import clear_document_results

logger = get_logger(__name__)

# chunk 并行度：受 API 速率限制，不宜太高
_CHUNK_WORKERS = 3


def _process_single_chunk(
    chunk_index: int,
    chunk_text: str,
    document_id: str,
    doc_title: str,
    doc_source: str,
    doc_publish_time: str,
) -> dict:
    """
    处理单个 chunk：证据发现 → 事实抽取 → 审核。
    线程安全：每次调用内部获取独立的 DB 连接。

    返回:
        {"evidences": int, "facts": int, "passed": int,
         "rejected": int, "uncertain": int, "fact_atom_ids": [...]}
    """
    chunk_stats = {
        "evidences": 0, "facts": 0,
        "passed": 0, "rejected": 0, "uncertain": 0,
        "fact_atom_ids": [],
    }

    chunk_id = str(uuid.uuid4())

    # 写入 document_chunk
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO document_chunk
            (id, document_id, chunk_index, chunk_text, char_count)
            VALUES (?, ?, ?, ?, ?)""",
            (chunk_id, document_id, chunk_index, chunk_text, len(chunk_text)),
        )
        conn.commit()
    finally:
        conn.close()

    # Agent 1: 证据发现
    evidences = find_evidence(
        chunk_id=chunk_id,
        chunk_text=chunk_text,
        document_id=document_id,
        doc_title=doc_title,
        doc_source=doc_source,
        doc_publish_time=doc_publish_time,
    )
    chunk_stats["evidences"] = len(evidences)

    # Agent 2 + 3: 事实抽取 + 批量审核（按 evidence）
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
        chunk_stats["facts"] += len(facts)

        if facts:
            facts_with_ids = [(f["fact_atom_id"], f) for f in facts]
            review_results = review_facts_batch(
                facts_with_ids=facts_with_ids,
                evidence_text=ev["evidence_text"],
                document_id=document_id,
            )
            for rr in review_results:
                verdict = rr.get("verdict", "UNCERTAIN")
                if verdict == "PASS":
                    chunk_stats["passed"] += 1
                elif verdict == "REJECT":
                    chunk_stats["rejected"] += 1
                else:
                    chunk_stats["uncertain"] += 1
                chunk_stats["fact_atom_ids"].append(rr["fact_atom_id"])

    return chunk_stats


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

    # 幂等性保护：清除此文档的旧处理结果，防止重跑产生重复
    clear_document_results(document_id)

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

    # 4. 并行处理各 chunk（证据发现 → 事实抽取 → 审核）
    all_fact_atom_ids = []

    if len(chunks) == 1:
        # 单 chunk 无需线程池开销
        chunk_stats = _process_single_chunk(
            0, chunks[0], document_id, doc_title, doc_source, doc_publish_time,
        )
        stats["evidences"] += chunk_stats["evidences"]
        stats["facts"] += chunk_stats["facts"]
        stats["passed"] += chunk_stats["passed"]
        stats["rejected"] += chunk_stats["rejected"]
        stats["uncertain"] += chunk_stats["uncertain"]
        all_fact_atom_ids.extend(chunk_stats["fact_atom_ids"])
    else:
        workers = min(_CHUNK_WORKERS, len(chunks))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _process_single_chunk,
                    i, chunk_text, document_id,
                    doc_title, doc_source, doc_publish_time,
                ): i
                for i, chunk_text in enumerate(chunks)
            }
            for future in as_completed(futures):
                chunk_idx = futures[future]
                try:
                    chunk_stats = future.result()
                    stats["evidences"] += chunk_stats["evidences"]
                    stats["facts"] += chunk_stats["facts"]
                    stats["passed"] += chunk_stats["passed"]
                    stats["rejected"] += chunk_stats["rejected"]
                    stats["uncertain"] += chunk_stats["uncertain"]
                    all_fact_atom_ids.extend(chunk_stats["fact_atom_ids"])
                except Exception as e:
                    logger.error("Chunk %d 处理失败: %s", chunk_idx, e)

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
