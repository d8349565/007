"""查询与导出 + 统计"""

import csv
import io
from datetime import datetime

from app.logger import get_logger
from app.models.db import get_connection

logger = get_logger(__name__)


# ──────────────────────────── 查询 ────────────────────────────

def query_facts(
    subject: str = "",
    fact_type: str = "",
    time_from: str = "",
    time_to: str = "",
    document_id: str = "",
    review_status: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """多条件组合查询 fact_atom"""
    conditions = []
    params = []

    if subject:
        conditions.append("f.subject_text LIKE ?")
        params.append(f"%{subject}%")
    if fact_type:
        conditions.append("f.fact_type = ?")
        params.append(fact_type)
    if time_from:
        conditions.append("f.time_expr >= ?")
        params.append(time_from)
    if time_to:
        conditions.append("f.time_expr <= ?")
        params.append(time_to)
    if document_id:
        conditions.append("f.document_id = ?")
        params.append(document_id)
    if review_status:
        conditions.append("f.review_status = ?")
        params.append(review_status)

    where = " AND ".join(conditions) if conditions else "1=1"

    sql = f"""
        SELECT f.*, sd.title AS document_title, sd.source_name AS document_source,
               es.evidence_text
        FROM fact_atom f
        LEFT JOIN source_document sd ON f.document_id = sd.id
        LEFT JOIN evidence_span es ON f.evidence_span_id = es.id
        WHERE {where}
        ORDER BY f.created_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_fact_detail(fact_atom_id: str) -> dict | None:
    """获取单条 fact_atom 的完整信息"""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT f.*, sd.title AS document_title, sd.source_name AS document_source,
                      es.evidence_text, dc.chunk_text
               FROM fact_atom f
               LEFT JOIN source_document sd ON f.document_id = sd.id
               LEFT JOIN evidence_span es ON f.evidence_span_id = es.id
               LEFT JOIN document_chunk dc ON es.chunk_id = dc.id
               WHERE f.id = ?""",
            (fact_atom_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ──────────────────────────── 导出 CSV ────────────────────────────

CSV_COLUMNS = [
    "id", "fact_type", "subject_text", "predicate", "object_text",
    "value_num", "value_text", "unit", "currency",
    "time_expr", "location_text", "qualifier_json",
    "confidence_score", "review_status",
    "document_title", "document_source", "evidence_text",
]


def export_csv(facts: list[dict], filepath: str = "") -> str:
    """
    将查询结果导出为 CSV。

    如果 filepath 非空则写入文件，否则返回 CSV 字符串。
    """
    output = io.StringIO()
    writer = csv.DictWriter(
        output, fieldnames=CSV_COLUMNS, extrasaction="ignore",
    )
    writer.writeheader()
    for f in facts:
        writer.writerow(f)

    csv_text = output.getvalue()

    if filepath:
        with open(filepath, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(csv_text)
        logger.info("CSV 已导出: %s (%d 条)", filepath, len(facts))

    return csv_text


# ──────────────────────────── 统计 ────────────────────────────

def get_stats() -> dict:
    """获取全局统计概览"""
    conn = get_connection()
    try:
        # 文档总量
        doc_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM source_document"
        ).fetchone()["cnt"]

        # fact_atom 总量
        fact_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM fact_atom"
        ).fetchone()["cnt"]

        # 文档维度抽取数量
        doc_fact_counts = conn.execute(
            """SELECT sd.id, sd.title,
                      COUNT(f.id) AS fact_count
               FROM source_document sd
               LEFT JOIN fact_atom f ON sd.id = f.document_id
               GROUP BY sd.id
               ORDER BY fact_count DESC"""
        ).fetchall()

        # fact_type 分布
        type_dist = conn.execute(
            """SELECT fact_type, COUNT(*) AS cnt
               FROM fact_atom
               GROUP BY fact_type
               ORDER BY cnt DESC"""
        ).fetchall()

        # 审核状态分布
        review_dist = conn.execute(
            """SELECT review_status, COUNT(*) AS cnt
               FROM fact_atom
               GROUP BY review_status
               ORDER BY cnt DESC"""
        ).fetchall()

        # Token 使用统计
        token_stats = conn.execute(
            """SELECT task_type,
                      COUNT(*) AS calls,
                      SUM(input_tokens) AS total_input,
                      SUM(output_tokens) AS total_output
               FROM extraction_task
               GROUP BY task_type"""
        ).fetchall()

        return {
            "document_count": doc_count,
            "fact_count": fact_count,
            "doc_fact_counts": [dict(r) for r in doc_fact_counts],
            "fact_type_distribution": [dict(r) for r in type_dist],
            "review_status_distribution": [dict(r) for r in review_dist],
            "token_usage": [dict(r) for r in token_stats],
        }
    finally:
        conn.close()


def get_documents(limit: int = 200, offset: int = 0) -> list[dict]:
    """获取文档列表，附带各流程阶段计数"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT sd.id, sd.title, sd.source_name, sd.source_type,
                      sd.status, sd.crawl_time, sd.publish_time, sd.url,
                      COUNT(DISTINCT dc.id)  AS chunk_count,
                      COUNT(DISTINCT es.id)  AS evidence_count,
                      COUNT(DISTINCT f.id)   AS fact_count
               FROM source_document sd
               LEFT JOIN document_chunk dc  ON dc.document_id = sd.id
               LEFT JOIN evidence_span es   ON es.document_id = sd.id
               LEFT JOIN fact_atom f        ON f.document_id  = sd.id
               GROUP BY sd.id
               ORDER BY sd.crawl_time DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_document(doc_id: str) -> dict | None:
    """获取单个文档基本信息"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM source_document WHERE id=?", (doc_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_document_chunks(doc_id: str) -> list[dict]:
    """获取文档的所有 chunk，附带 evidence 计数"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT dc.*, COUNT(es.id) AS evidence_count
               FROM document_chunk dc
               LEFT JOIN evidence_span es ON es.chunk_id = dc.id
               WHERE dc.document_id = ?
               GROUP BY dc.id
               ORDER BY dc.chunk_index""",
            (doc_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_document_evidences(doc_id: str) -> list[dict]:
    """获取文档的所有 evidence_span，附带所属 chunk_index 和 fact 计数"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT es.*, dc.chunk_index,
                      COUNT(f.id) AS fact_count
               FROM evidence_span es
               LEFT JOIN document_chunk dc ON es.chunk_id = dc.id
               LEFT JOIN fact_atom f       ON f.evidence_span_id = es.id
               WHERE es.document_id = ?
               GROUP BY es.id
               ORDER BY dc.chunk_index, es.rowid""",
            (doc_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_document_tasks(doc_id: str) -> list[dict]:
    """获取文档的抽取任务日志"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM extraction_task
               WHERE document_id = ?
               ORDER BY started_at""",
            (doc_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_passed_facts_stats(fact_type: str = "", document_id: str = "", pass_type: str = "") -> dict:
    """获取已通过事实的统计概览（总数、自动通过数、人工通过数、类型分布）"""
    conn = get_connection()
    try:
        conditions = []
        params = []
        if pass_type:
            conditions.append("f.review_status = ?")
            params.append(pass_type)
        else:
            conditions.append("f.review_status IN ('AUTO_PASS','HUMAN_PASS')")
        if fact_type:
            conditions.append("f.fact_type = ?")
            params.append(fact_type)
        if document_id:
            conditions.append("f.document_id = ?")
            params.append(document_id)
        where = " AND ".join(conditions)

        total = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM fact_atom f WHERE {where}", params
        ).fetchone()["cnt"]

        auto = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM fact_atom f WHERE {where} AND f.review_status='AUTO_PASS'", params
        ).fetchone()["cnt"]

        human = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM fact_atom f WHERE {where} AND f.review_status='HUMAN_PASS'", params
        ).fetchone()["cnt"]

        type_dist = conn.execute(
            f"""SELECT f.fact_type, COUNT(*) AS cnt FROM fact_atom f
                WHERE {where} GROUP BY f.fact_type ORDER BY cnt DESC""",
            params,
        ).fetchall()

        return {
            "total": total,
            "auto_pass": auto,
            "human_pass": human,
            "type_dist": [dict(r) for r in type_dist],
        }
    finally:
        conn.close()


def get_doc_stats(document_id: str) -> dict:
    """单文档处理统计"""
    conn = get_connection()
    try:
        chunks = conn.execute(
            "SELECT COUNT(*) AS cnt FROM document_chunk WHERE document_id=?",
            (document_id,),
        ).fetchone()["cnt"]

        evidences = conn.execute(
            "SELECT COUNT(*) AS cnt FROM evidence_span WHERE document_id=?",
            (document_id,),
        ).fetchone()["cnt"]

        facts = conn.execute(
            "SELECT COUNT(*) AS cnt FROM fact_atom WHERE document_id=?",
            (document_id,),
        ).fetchone()["cnt"]

        review_dist = conn.execute(
            """SELECT review_status, COUNT(*) AS cnt
               FROM fact_atom WHERE document_id=?
               GROUP BY review_status""",
            (document_id,),
        ).fetchall()

        tokens = conn.execute(
            """SELECT SUM(input_tokens) AS total_in,
                      SUM(output_tokens) AS total_out
               FROM extraction_task WHERE document_id=?""",
            (document_id,),
        ).fetchone()

        return {
            "document_id": document_id,
            "chunks": chunks,
            "evidences": evidences,
            "facts": facts,
            "review_distribution": [dict(r) for r in review_dist],
            "total_input_tokens": tokens["total_in"] or 0,
            "total_output_tokens": tokens["total_out"] or 0,
        }
    finally:
        conn.close()
