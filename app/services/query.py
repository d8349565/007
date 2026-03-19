"""查询与导出 + 统计"""

import csv
import io
from datetime import datetime

from app.logger import get_logger
from app.models.db import get_connection
from app.config import get_config

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

def get_documents(limit: int = 200, offset: int = 0) -> list[dict]:
    """获取文档列表，附带各流程阶段计数及费用"""
    conn = get_connection()
    try:
        # 先查文档基础信息和计数
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

        # 单独查每个文档的 token 费用（避免 JOIN 倍增）
        doc_ids = [r["id"] for r in rows]
        result = []
        for r in rows:
            d = dict(r)
            if doc_ids:
                toks = conn.execute(
                    """SELECT
                          COALESCE(SUM(input_tokens), 0) AS total_in,
                          COALESCE(SUM(output_tokens), 0) AS total_out
                       FROM extraction_task WHERE document_id=?""",
                    (d["id"],),
                ).fetchone()
                d["total_input_tokens"] = toks["total_in"]
                d["total_output_tokens"] = toks["total_out"]
                d["cost"] = calculate_cost(toks["total_in"] or 0, toks["total_out"] or 0)
            else:
                d["total_input_tokens"] = 0
                d["total_output_tokens"] = 0
                d["cost"] = 0.0
            result.append(d)
        return result
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


def clear_document_results(document_id: str) -> dict:
    """
    清除文档的所有处理结果（保留文档本身）。
    用于重新处理前的幂等性清理。
    删除顺序：review_log → fact_atom → evidence_span → extraction_task → document_sentence → document_chunk
    """
    conn = get_connection()
    stats = {}
    try:
        # review_log via fact_atom
        fact_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM fact_atom WHERE document_id=?", (document_id,)
        ).fetchall()]
        if fact_ids:
            placeholders = ",".join(["?"] * len(fact_ids))
            stats["review_log"] = conn.execute(
                f"DELETE FROM review_log WHERE target_id IN ({placeholders})",
                fact_ids,
            ).rowcount
        else:
            stats["review_log"] = 0

        # review_log via evidence_span
        ev_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM evidence_span WHERE document_id=?", (document_id,)
        ).fetchall()]
        if ev_ids:
            placeholders = ",".join(["?"] * len(ev_ids))
            stats["review_log"] += conn.execute(
                f"DELETE FROM review_log WHERE target_id IN ({placeholders})",
                ev_ids,
            ).rowcount

        stats["fact_atom"] = conn.execute(
            "DELETE FROM fact_atom WHERE document_id=?", (document_id,)
        ).rowcount
        stats["evidence_span"] = conn.execute(
            "DELETE FROM evidence_span WHERE document_id=?", (document_id,)
        ).rowcount
        stats["extraction_task"] = conn.execute(
            "DELETE FROM extraction_task WHERE document_id=?", (document_id,)
        ).rowcount
        stats["document_sentence"] = conn.execute(
            "DELETE FROM document_sentence WHERE document_id=?", (document_id,)
        ).rowcount
        stats["document_chunk"] = conn.execute(
            "DELETE FROM document_chunk WHERE document_id=?", (document_id,)
        ).rowcount

        conn.commit()
        if any(v > 0 for v in stats.values()):
            logger.info("清除文档 %s 旧结果: %s", document_id[:8], stats)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return stats


def cascade_delete_document(document_id: str) -> dict:
    """
    级联删除文档及其所有关联数据。
    删除顺序（尊重外键）：
      review_log → fact_atom → evidence_span → extraction_task
      → document_sentence → document_chunk → source_document
    返回各表删除行数。
    """
    conn = get_connection()
    stats = {}
    try:
        # 1. 删除 review_log（关联 fact_atom）
        fact_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM fact_atom WHERE document_id=?", (document_id,)
        ).fetchall()]
        if fact_ids:
            placeholders = ",".join(["?"] * len(fact_ids))
            stats["review_log"] = conn.execute(
                f"DELETE FROM review_log WHERE target_id IN ({placeholders})",
                fact_ids,
            ).rowcount
        else:
            stats["review_log"] = 0

        # 2. 删除 review_log（关联 evidence_span）
        ev_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM evidence_span WHERE document_id=?", (document_id,)
        ).fetchall()]
        if ev_ids:
            placeholders = ",".join(["?"] * len(ev_ids))
            stats["review_log"] += conn.execute(
                f"DELETE FROM review_log WHERE target_id IN ({placeholders})",
                ev_ids,
            ).rowcount

        # 3. 删除 fact_atom（清除实体链接，entity 本身保留）
        stats["fact_atom"] = conn.execute(
            "DELETE FROM fact_atom WHERE document_id=?", (document_id,)
        ).rowcount

        # 4. 删除 evidence_span
        stats["evidence_span"] = conn.execute(
            "DELETE FROM evidence_span WHERE document_id=?", (document_id,)
        ).rowcount

        # 5. 删除 extraction_task
        stats["extraction_task"] = conn.execute(
            "DELETE FROM extraction_task WHERE document_id=?", (document_id,)
        ).rowcount

        # 6. 删除 document_sentence
        stats["document_sentence"] = conn.execute(
            "DELETE FROM document_sentence WHERE document_id=?", (document_id,)
        ).rowcount

        # 7. 删除 document_chunk
        stats["document_chunk"] = conn.execute(
            "DELETE FROM document_chunk WHERE document_id=?", (document_id,)
        ).rowcount

        # 8. 删除 source_document
        stats["source_document"] = conn.execute(
            "DELETE FROM source_document WHERE id=?", (document_id,)
        ).rowcount

        conn.commit()
        logger.info("级联删除文档 %s: %s", document_id[:8], stats)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return stats


def update_document_meta(document_id: str, title: str, author: str | None,
                         source_name: str | None, publish_time: str | None) -> bool:
    """更新文档元信息（标题、作者、来源、发布时间）"""
    conn = get_connection()
    try:
        rowcount = conn.execute(
            """UPDATE source_document SET
               title=?, author=?, source_name=?, publish_time=?,
               updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (title, author, source_name, publish_time, document_id),
        ).rowcount
        conn.commit()
        return rowcount > 0
    finally:
        conn.close()


# ──────────────────────────── 费用计算 ────────────────────────────

def _get_model_pricing() -> dict:
    """从配置获取模型价格"""
    try:
        cfg = get_config()
        return cfg.get("model_pricing", {}).get("deepseek-chat", {
            "input_cached": 0.2,
            "input_uncached": 2.0,
            "output": 3.0,
        })
    except Exception:
        # 保守估算（缓存未命中）
        return {"input_cached": 0.2, "input_uncached": 2.0, "output": 3.0}


def calculate_cost(input_tokens: int, output_tokens: int,
                   cache_hit_ratio: float = 0.0) -> float:
    """
    计算单次 API 调用的费用。

    Args:
        input_tokens: 输入 token 数
        output_tokens: 输出 token 数
        cache_hit_ratio: 缓存命中率（0.0-1.0），默认 0（全部按未命中计算）

    Returns:
        费用（元）
    """
    p = _get_model_pricing()
    cached_input = int(input_tokens * cache_hit_ratio)
    uncached_input = input_tokens - cached_input

    input_cost = (cached_input / 1_000_000) * p["input_cached"]
    input_cost += (uncached_input / 1_000_000) * p["input_uncached"]
    output_cost = (output_tokens / 1_000_000) * p["output"]
    return round(input_cost + output_cost, 4)


def get_document_cost(document_id: str) -> dict:
    """获取单文档处理费用明细"""
    conn = get_connection()
    try:
        tasks = conn.execute(
            """SELECT task_type, model_name,
                      SUM(input_tokens) AS total_in,
                      SUM(output_tokens) AS total_out,
                      COUNT(*) AS calls
               FROM extraction_task
               WHERE document_id = ?
               GROUP BY task_type, model_name""",
            (document_id,),
        ).fetchall()

        total_cost = 0.0
        details = []
        for t in tasks:
            cost = calculate_cost(t["total_in"] or 0, t["total_out"] or 0)
            total_cost += cost
            details.append({
                "task_type": t["task_type"],
                "model_name": t["model_name"] or "deepseek-chat",
                "calls": t["calls"],
                "input_tokens": t["total_in"] or 0,
                "output_tokens": t["total_out"] or 0,
                "cost": cost,
            })
        return {"document_id": document_id, "total_cost": round(total_cost, 4), "details": details}
    finally:
        conn.close()


def get_stats() -> dict:
    """获取全局统计概览（含费用）"""
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

        # Token 使用统计（含费用）
        token_stats = conn.execute(
            """SELECT task_type,
                      COUNT(*) AS calls,
                      SUM(input_tokens) AS total_input,
                      SUM(output_tokens) AS total_output
               FROM extraction_task
               GROUP BY task_type"""
        ).fetchall()

        p = _get_model_pricing()
        total_cost = 0.0
        token_usage = []
        for s in token_stats:
            cost = calculate_cost(s["total_input"] or 0, s["total_output"] or 0)
            total_cost += cost
            token_usage.append({
                **dict(s),
                "cost": cost,
                "input_price": p["input_uncached"],
                "output_price": p["output"],
            })

        return {
            "document_count": doc_count,
            "fact_count": fact_count,
            "doc_fact_counts": [dict(r) for r in doc_fact_counts],
            "fact_type_distribution": [dict(r) for r in type_dist],
            "review_status_distribution": [dict(r) for r in review_dist],
            "token_usage": token_usage,
            "total_cost": round(total_cost, 4),
            "cost_per_document": round(total_cost / doc_count, 4) if doc_count > 0 else 0,
        }
    finally:
        conn.close()
