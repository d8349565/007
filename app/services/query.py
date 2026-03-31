"""查询与导出 + 统计"""

import csv
import io
import json
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
               sd.url AS document_url, sd.source_type AS document_source_type,
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
    """获取文档列表，附带各流程阶段计数、任务成败统计及费用"""
    conn = get_connection()
    try:
        # 先查文档基础信息和计数
        rows = conn.execute(
            """SELECT sd.id, sd.title, sd.source_name, sd.source_type,
                      sd.status, sd.error_message, sd.crawl_time, sd.publish_time, sd.url,
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

        # 单独查每个文档的 token 费用 + 任务统计（避免 JOIN 倍增）
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
                # 任务成败统计
                task_stats = conn.execute(
                    """SELECT
                          COUNT(*) AS total_tasks,
                          SUM(CASE WHEN status='成功' THEN 1 ELSE 0 END) AS ok_tasks,
                          SUM(CASE WHEN status='失败' THEN 1 ELSE 0 END) AS fail_tasks,
                          SUM(CASE WHEN status='运行中' THEN 1 ELSE 0 END) AS run_tasks
                       FROM extraction_task WHERE document_id=?""",
                    (d["id"],),
                ).fetchone()
                d["total_tasks"] = task_stats["total_tasks"]
                d["ok_tasks"] = task_stats["ok_tasks"]
                d["fail_tasks"] = task_stats["fail_tasks"]
                d["run_tasks"] = task_stats["run_tasks"]
                # 失败任务的错误信息
                if task_stats["fail_tasks"]:
                    fail_rows = conn.execute(
                        """SELECT task_type, error_message FROM extraction_task
                           WHERE document_id=? AND status='失败'""",
                        (d["id"],),
                    ).fetchall()
                    d["failed_task_errors"] = [
                        {"task_type": fr["task_type"], "error": fr["error_message"] or ""}
                        for fr in fail_rows
                    ]
                else:
                    d["failed_task_errors"] = []
                # review_status 分布
                review_dist = conn.execute(
                    """SELECT review_status, COUNT(*) AS cnt
                       FROM fact_atom WHERE document_id=?
                       GROUP BY review_status""",
                    (d["id"],),
                ).fetchall()
                d["review_dist"] = {row["review_status"]: row["cnt"] for row in review_dist}
            else:
                d["total_input_tokens"] = 0
                d["total_output_tokens"] = 0
                d["cost"] = 0.0
                d["total_tasks"] = 0
                d["ok_tasks"] = 0
                d["fail_tasks"] = 0
                d["run_tasks"] = 0
                d["failed_task_errors"] = []
                d["review_dist"] = {}
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
            conditions.append("f.review_status IN ('自动通过','人工通过')")
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
            f"SELECT COUNT(*) AS cnt FROM fact_atom f WHERE {where} AND f.review_status='自动通过'", params
        ).fetchone()["cnt"]

        human = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM fact_atom f WHERE {where} AND f.review_status='人工通过'", params
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
      review_log → entity_relation_suggestion → fact_atom → evidence_span
      → extraction_task → document_sentence → document_chunk → source_document
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

        # 3. 删除 entity_relation_suggestion（引用 fact_atom，必须先删）
        if fact_ids:
            placeholders = ",".join(["?"] * len(fact_ids))
            stats["entity_relation_suggestion"] = conn.execute(
                f"DELETE FROM entity_relation_suggestion WHERE evidence_fact_id IN ({placeholders})",
                fact_ids,
            ).rowcount
        else:
            stats["entity_relation_suggestion"] = 0

        # 4. 删除 fact_atom（清除实体链接，entity 本身保留）
        stats["fact_atom"] = conn.execute(
            "DELETE FROM fact_atom WHERE document_id=?", (document_id,)
        ).rowcount

        # 5. 删除 evidence_span
        stats["evidence_span"] = conn.execute(
            "DELETE FROM evidence_span WHERE document_id=?", (document_id,)
        ).rowcount

        # 6. 删除 extraction_task
        stats["extraction_task"] = conn.execute(
            "DELETE FROM extraction_task WHERE document_id=?", (document_id,)
        ).rowcount

        # 7. 删除 document_sentence
        stats["document_sentence"] = conn.execute(
            "DELETE FROM document_sentence WHERE document_id=?", (document_id,)
        ).rowcount

        # 8. 删除 document_chunk
        stats["document_chunk"] = conn.execute(
            "DELETE FROM document_chunk WHERE document_id=?", (document_id,)
        ).rowcount

        # 9. 删除 source_document
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


# ──────────────────────────── 图谱与时间轴 ────────────────────────────

def get_graph_data(fact_type: str = "", doc_id: str = "") -> dict:
    """
    构建知识图谱数据。

    节点：所有在已通过事实中出现为 subject_text 的实体。
    节点 ID 规则：
      - 有 entity_id 时使用 entity_id（标准化节点，从 entity 表补充名称/类型）
      - 无 entity_id 时使用 "text::{subject_text}"（文本节点，is_text_node=True）
    边：已通过事实中 object_text 或 object_entity_id 不为空的事实，
    对象端同样可以是文本节点。

    返回 {nodes, edges, stats}。
    """
    conn = get_connection()
    try:
        # 基础过滤条件（按 fact_type / doc_id 筛选，可选）
        base_cond = ["f.review_status IN ('自动通过','人工通过')"]
        base_params: list = []
        if fact_type:
            base_cond.append("f.fact_type = ?")
            base_params.append(fact_type)
        if doc_id:
            base_cond.append("f.document_id = ?")
            base_params.append(doc_id)
        where_base = " AND ".join(base_cond)

        # ── 一次性查询所有已通过事实（含 subject_text / object_text） ──
        all_rows = conn.execute(
            f"""SELECT f.id, f.fact_type, f.subject_text, f.predicate,
                       f.object_text, f.value_num, f.value_text, f.unit,
                       f.currency, f.time_expr, f.location_text,
                       f.confidence_score, f.subject_entity_id, f.object_entity_id,
                       es.evidence_text, sd.title AS document_title
                FROM fact_atom f
                LEFT JOIN evidence_span es ON f.evidence_span_id = es.id
                LEFT JOIN source_document sd ON f.document_id = sd.id
                WHERE {where_base} AND f.subject_text IS NOT NULL
                ORDER BY f.created_at DESC
                LIMIT 2000""",
            base_params,
        ).fetchall()

        def make_node_id(entity_id: str | None, text: str | None) -> str | None:
            """优先用 entity_id，否则用文本构建虚拟 ID。"""
            if entity_id:
                return entity_id
            return f"text::{text}" if text else None

        node_map: dict = {}
        edges = []
        entity_ids_to_fetch: set = set()

        for r in all_rows:
            sid = make_node_id(r["subject_entity_id"], r["subject_text"])
            if not sid:
                continue

            is_subj_text = not r["subject_entity_id"]
            if sid not in node_map:
                node_map[sid] = {
                    "id": sid,
                    "name": r["subject_text"] or sid,
                    "entity_type": "UNKNOWN",
                    "fact_count": 0,
                    "is_text_node": is_subj_text,
                }
                if r["subject_entity_id"]:
                    entity_ids_to_fetch.add(r["subject_entity_id"])
            node_map[sid]["fact_count"] += 1

            # 有 object（关系型事实）
            oid = make_node_id(r["object_entity_id"], r["object_text"])
            if oid:
                is_obj_text = not r["object_entity_id"]
                if oid not in node_map:
                    node_map[oid] = {
                        "id": oid,
                        "name": r["object_text"] or oid,
                        "entity_type": "UNKNOWN",
                        "fact_count": 0,
                        "is_text_node": is_obj_text,
                    }
                    if r["object_entity_id"]:
                        entity_ids_to_fetch.add(r["object_entity_id"])
                edges.append({
                    "id": r["id"],
                    "source": sid,
                    "target": oid,
                    "fact_type": r["fact_type"],
                    "subject_text": r["subject_text"],
                    "predicate": r["predicate"],
                    "object_text": r["object_text"],
                    "value_num": r["value_num"],
                    "value_text": r["value_text"],
                    "unit": r["unit"],
                    "currency": r["currency"],
                    "time_expr": r["time_expr"],
                    "location_text": r["location_text"],
                    "confidence_score": r["confidence_score"],
                    "evidence_text": r["evidence_text"],
                    "document_title": r["document_title"],
                    "subject_entity_id": r["subject_entity_id"],
                    "object_entity_id": r["object_entity_id"],
                })

        # ── 补全标准化实体的 canonical_name 和 entity_type ──
        if entity_ids_to_fetch:
            ph = ",".join(["?"] * len(entity_ids_to_fetch))
            entities = conn.execute(
                f"SELECT id, canonical_name, entity_type FROM entity WHERE id IN ({ph})",
                list(entity_ids_to_fetch),
            ).fetchall()
            for e in entities:
                if e["id"] in node_map:
                    node_map[e["id"]]["name"] = e["canonical_name"]
                    node_map[e["id"]]["entity_type"] = e["entity_type"] or "UNKNOWN"

        # ── 补全标准化实体的 canonical_name 和 entity_type（含 entity_relation 侧节点）——
        # 先将 entity_relation 关系加入 edges，节点不足时按需插入 node_map
        # ── entity_relation 表：手动/AI 确认的实体关系 ──
        kb_rels = conn.execute(
            """SELECT r.id, r.from_entity_id, r.to_entity_id, r.relation_type,
                      ef.canonical_name AS from_name, ef.entity_type AS from_type,
                      et.canonical_name AS to_name, et.entity_type AS to_type
               FROM entity_relation r
               LEFT JOIN entity ef ON r.from_entity_id = ef.id
               LEFT JOIN entity et ON r.to_entity_id = et.id"""
        ).fetchall()

        REL_TYPE_ZH = {
            "SUBSIDIARY": "子公司", "SHAREHOLDER": "股东", "JV": "合资",
            "BRAND": "品牌归属", "PARTNER": "合作方", "INVESTS_IN": "投资/持有",
        }

        for r in kb_rels:
            fid = r["from_entity_id"]
            tid = r["to_entity_id"]
            if not fid or not tid:
                continue
            # 确保节点存在（若不在事实中出现也要加入）
            for eid, ename, etype in (
                (fid, r["from_name"], r["from_type"]),
                (tid, r["to_name"], r["to_type"]),
            ):
                if eid not in node_map:
                    node_map[eid] = {
                        "id": eid,
                        "name": ename or eid,
                        "entity_type": etype or "UNKNOWN",
                        "fact_count": 0,
                        "is_text_node": False,
                    }
            label = REL_TYPE_ZH.get(r["relation_type"], r["relation_type"])
            edges.append({
                "id": f"rel::{r['id']}",
                "source": fid,
                "target": tid,
                "fact_type": "ENTITY_RELATION",
                "subject_text": r["from_name"] or "",
                "predicate": label,
                "object_text": r["to_name"] or "",
                "value_num": None,
                "value_text": None,
                "unit": None,
                "currency": None,
                "time_expr": None,
                "location_text": None,
                "confidence_score": 1.0,
                "evidence_text": f"手动确认关系：{r['relation_type']}",
                "document_title": None,
                "is_kb_relation": True,
                "relation_type": r["relation_type"],
            })

        # ── 统计信息 ──
        total_passed = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM fact_atom f WHERE {where_base}",
            base_params,
        ).fetchone()["cnt"]
        relational_count = len(edges)
        metric_count = total_passed - relational_count

        return {
            "nodes": list(node_map.values()),
            "edges": edges,
            "stats": {
                "total_passed_facts": total_passed,
                "entity_count": len(node_map),
                "relational_facts": relational_count,
                "metric_facts": metric_count,
                # 向后兼容旧字段名
                "linked_facts": relational_count,
                "unlinked_facts": metric_count,
            },
        }
    finally:
        conn.close()


def get_entity_list(search: str = "", entity_type: str = "", limit: int = 200) -> list[dict]:
    """
    获取有关联 fact_atom 的实体列表。

    返回 [{id, name, entity_type, fact_count}]，按 fact_count 降序。
    """
    conn = get_connection()
    try:
        conditions = []
        params = []
        if search:
            conditions.append("e.canonical_name LIKE ?")
            params.append(f"%{search}%")
        if entity_type:
            conditions.append("e.entity_type = ?")
            params.append(entity_type)

        extra_where = (" AND " + " AND ".join(conditions)) if conditions else ""

        rows = conn.execute(
            f"""SELECT e.id, e.canonical_name AS name, e.entity_type,
                       COUNT(DISTINCT f.id) AS fact_count
                FROM entity e
                JOIN fact_atom f ON (f.subject_entity_id = e.id OR f.object_entity_id = e.id)
                WHERE f.review_status IN ('自动通过','人工通过')
                {extra_where}
                GROUP BY e.id
                ORDER BY fact_count DESC
                LIMIT ?""",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_entity_detail(entity_id: str) -> dict | None:
    """
    获取实体的完整详情：基本信息、别名、关系、事实统计。
    """
    conn = get_connection()
    try:
        ent = conn.execute(
            "SELECT id, canonical_name, normalized_name, entity_type FROM entity WHERE id=?",
            (entity_id,),
        ).fetchone()
        if not ent:
            return None

        info = dict(ent)

        # 别名
        aliases = conn.execute(
            "SELECT id, alias_name FROM entity_alias WHERE entity_id=? ORDER BY alias_name",
            (entity_id,),
        ).fetchall()
        info["aliases"] = [dict(a) for a in aliases]

        # 关系（from 方向：当前实体是 from）
        rels_from = conn.execute(
            """SELECT r.id, r.relation_type, r.detail_json, r.source,
                      e.id AS target_id, e.canonical_name AS target_name, e.entity_type AS target_type
               FROM entity_relation r
               JOIN entity e ON r.to_entity_id = e.id
               WHERE r.from_entity_id = ?
               ORDER BY r.relation_type, e.canonical_name""",
            (entity_id,),
        ).fetchall()
        # 关系（to 方向：当前实体是 to）
        rels_to = conn.execute(
            """SELECT r.id, r.relation_type, r.detail_json, r.source,
                      e.id AS target_id, e.canonical_name AS target_name, e.entity_type AS target_type
               FROM entity_relation r
               JOIN entity e ON r.from_entity_id = e.id
               WHERE r.to_entity_id = ?
               ORDER BY r.relation_type, e.canonical_name""",
            (entity_id,),
        ).fetchall()
        info["relations_from"] = [dict(r) for r in rels_from]  # 当前实体 → target
        info["relations_to"] = [dict(r) for r in rels_to]      # target → 当前实体

        # 如果 entity_relation 表为空，从事实中推导关系
        if not rels_from and not rels_to:
            # fact_type → 关系类别 映射
            _FT_TO_REL = {
                "COOPERATION": "合作",
                "INVESTMENT": "投资",
                "EXPANSION": "扩建",
            }
            # 查询：逐条获取事实（含 qualifier_json），Python 端分组
            derived_from = conn.execute(
                """SELECT f.id AS fact_id,
                          f.object_entity_id AS target_id,
                          e.canonical_name AS target_name,
                          e.entity_type AS target_type,
                          f.fact_type,
                          f.predicate,
                          f.object_text,
                          f.qualifier_json
                   FROM fact_atom f
                   JOIN entity e ON f.object_entity_id = e.id
                   WHERE f.subject_entity_id = ?
                     AND f.object_entity_id IS NOT NULL
                     AND f.object_entity_id != ?
                     AND f.review_status IN ('自动通过','人工通过')
                     AND e.entity_type IN ('COMPANY','GROUP','企业','集团')
                   ORDER BY f.fact_type, f.object_entity_id""",
                (entity_id, entity_id),
            ).fetchall()
            derived_to = conn.execute(
                """SELECT f.id AS fact_id,
                          f.subject_entity_id AS target_id,
                          e.canonical_name AS target_name,
                          e.entity_type AS target_type,
                          f.fact_type,
                          f.predicate,
                          f.object_text,
                          f.qualifier_json
                   FROM fact_atom f
                   JOIN entity e ON f.subject_entity_id = e.id
                   WHERE f.object_entity_id = ?
                     AND f.subject_entity_id IS NOT NULL
                     AND f.subject_entity_id != ?
                     AND f.review_status IN ('自动通过','人工通过')
                     AND e.entity_type IN ('COMPANY','GROUP','企业','集团')
                   ORDER BY f.fact_type, f.subject_entity_id""",
                (entity_id, entity_id),
            ).fetchall()

            import re
            import json as _json

            # 限定词英文枚举值 → 中文
            _QUAL_VAL_ZH = {
                "equity_cooperation": "股权合作", "strategic_cooperation": "战略合作",
                "joint_venture": "合资合作", "research_cooperation": "科研合作",
                "technical_cooperation": "技术合作", "supply_partnership": "供应合作",
                "co_development": "联合开发", "equity_investment": "股权投资",
                "distribution": "销售合作", "licensing": "许可授权",
                "investment": "投资合作", "partnership": "合作共建",
                "planned": "规划中", "under_construction": "在建",
                "completed": "竣工", "commissioned": "投产",
            }
            from app.web.review_app import QUALIFIER_VALUE_ZH

            # qualifier 中对关系描述有补充价值的字段
            _REL_QUALIFIER_KEYS = (
                "joint_venture", "project_name", "cooperation_type",
                "investment_type", "purpose", "plant_location",
                "partners", "exclusive_agent", "product_type",
            )

            def _merge_derived(rows):
                """按 (fact_type, target_id) 合并，拼接谓词+限定词为关系描述，收集事实 ID"""
                merged = {}
                for r in rows:
                    key = (r["fact_type"], r["target_id"])
                    if key not in merged:
                        merged[key] = {
                            "target_id": r["target_id"],
                            "target_name": r["target_name"],
                            "target_type": r["target_type"],
                            "fact_type": r["fact_type"],
                            "fact_count": 0,
                            "_preds": [],
                            "_qual_parts": [],
                            "fact_ids": [],
                        }
                    m = merged[key]
                    m["fact_count"] += 1
                    m["fact_ids"].append(r["fact_id"])
                    desc = (r["predicate"] or "").strip()
                    # 跳过数值性谓词（以"为"/"达"/"约"/"超"结尾），它们描述指标不描述关系
                    if desc and not re.search(r"[为达约超]$", desc):
                        if desc not in m["_preds"]:
                            m["_preds"].append(desc)
                    # 从 qualifier_json 提取补充描述
                    qj = r["qualifier_json"]
                    if qj:
                        try:
                            qd = _json.loads(qj)
                        except (ValueError, TypeError):
                            qd = {}
                        for qk in _REL_QUALIFIER_KEYS:
                            qv = qd.get(qk)
                            if not qv:
                                continue
                            sv = str(qv).strip()
                            # 跳过布尔值
                            if sv.lower() in ("true", "false"):
                                continue
                            if sv and sv not in m["_qual_parts"]:
                                # 翻译已知英文枚举值
                                sv = _QUAL_VAL_ZH.get(sv, sv)
                                if sv not in m["_qual_parts"]:
                                    m["_qual_parts"].append(sv)
                result = []
                for m in merged.values():
                    preds = m.pop("_preds")
                    qual_parts = m.pop("_qual_parts")
                    m["fact_ids"] = m["fact_ids"][:5]  # 只保留前 5 个事实 ID
                    ft = m["fact_type"]
                    category = _FT_TO_REL.get(ft, ft)
                    if preds:
                        desc = "、".join(preds[:3])
                        # 补充限定词信息（如合资公司名称）
                        if qual_parts:
                            desc += "（" + "、".join(qual_parts[:2]) + "）"
                        m["relation_type"] = desc
                    else:
                        m["relation_type"] = category
                    result.append(m)
                return result

            info["relations_from"] = _merge_derived(derived_from)
            info["relations_to"] = _merge_derived(derived_to)

            # 名称级过滤：排除 target_name 看起来像项目/设施/产品的记录
            _PROJECT_NAME_RE = re.compile(
                r"(?:项目|工程|工厂|基地|园区|产品|涂料供应|涂料生产|产能|专项)$"
            )
            info["relations_from"] = [
                r for r in info["relations_from"]
                if not _PROJECT_NAME_RE.search(r["target_name"])
            ]
            info["relations_to"] = [
                r for r in info["relations_to"]
                if not _PROJECT_NAME_RE.search(r["target_name"])
            ]

        # 事实统计（按 fact_type 分组）
        type_stats = conn.execute(
            """SELECT f.fact_type, COUNT(*) AS cnt
               FROM fact_atom f
               WHERE (f.subject_entity_id = ? OR f.object_entity_id = ?)
                 AND f.review_status IN ('自动通过','人工通过')
               GROUP BY f.fact_type
               ORDER BY cnt DESC""",
            (entity_id, entity_id),
        ).fetchall()
        info["fact_type_stats"] = [dict(r) for r in type_stats]
        info["total_fact_count"] = sum(r["cnt"] for r in type_stats)

        # 关联文档数
        doc_count = conn.execute(
            """SELECT COUNT(DISTINCT f.document_id) AS cnt
               FROM fact_atom f
               WHERE (f.subject_entity_id = ? OR f.object_entity_id = ?)
                 AND f.review_status IN ('自动通过','人工通过')""",
            (entity_id, entity_id),
        ).fetchone()["cnt"]
        info["source_doc_count"] = doc_count

        # 关联文档列表（最多 10 篇）
        source_docs = conn.execute(
            """SELECT sd.id, sd.title, sd.crawl_time,
                      COUNT(f.id) AS fact_count
               FROM fact_atom f
               JOIN source_document sd ON f.document_id = sd.id
               WHERE (f.subject_entity_id = ? OR f.object_entity_id = ?)
                 AND f.review_status IN ('自动通过','人工通过')
               GROUP BY sd.id
               ORDER BY fact_count DESC
               LIMIT 10""",
            (entity_id, entity_id),
        ).fetchall()
        info["source_docs"] = [dict(d) for d in source_docs]

        # 时间跨度
        time_range = conn.execute(
            """SELECT MIN(f.time_expr) AS earliest, MAX(f.time_expr) AS latest
               FROM fact_atom f
               WHERE (f.subject_entity_id = ? OR f.object_entity_id = ?)
                 AND f.review_status IN ('自动通过','人工通过')
                 AND f.time_expr IS NOT NULL AND f.time_expr != ''""",
            (entity_id, entity_id),
        ).fetchone()
        info["time_earliest"] = time_range["earliest"] if time_range else None
        info["time_latest"] = time_range["latest"] if time_range else None

        return info
    finally:
        conn.close()


def get_entity_timeline(
    entity_id: str = "",
    subject_text: str = "",
    fact_type: str = "",
) -> dict:
    """
    获取某实体的时间轴数据。

    支持按 entity_id（精确）或 subject_text（模糊）查询。
    返回 {entity_info, facts, available_types}。
    """
    conn = get_connection()
    try:
        # 确定实体信息
        entity_info = {"name": subject_text or "未知", "entity_type": "", "id": entity_id}
        if entity_id:
            ent = conn.execute(
                "SELECT id, canonical_name, entity_type FROM entity WHERE id=?",
                (entity_id,),
            ).fetchone()
            if ent:
                entity_info = {
                    "id": ent["id"],
                    "name": ent["canonical_name"],
                    "entity_type": ent["entity_type"] or "",
                }

        # 构建查询条件
        conditions = ["f.review_status IN ('自动通过','人工通过')"]
        params = []
        if entity_id:
            conditions.append("(f.subject_entity_id = ? OR f.object_entity_id = ?)")
            params.extend([entity_id, entity_id])
        elif subject_text:
            conditions.append("f.subject_text LIKE ?")
            params.append(f"%{subject_text}%")
        else:
            return {"entity_info": entity_info, "facts": [], "available_types": []}

        if fact_type:
            conditions.append("f.fact_type = ?")
            params.append(fact_type)

        where = " AND ".join(conditions)

        rows = conn.execute(
            f"""SELECT f.id, f.fact_type, f.subject_text, f.predicate,
                       f.object_text, f.value_num, f.value_text, f.unit,
                       f.currency, f.time_expr, f.location_text,
                       f.confidence_score, f.review_status, f.qualifier_json,
                       f.subject_entity_id, f.object_entity_id,
                       es.evidence_text, sd.title AS document_title, sd.id AS document_id
                FROM fact_atom f
                LEFT JOIN evidence_span es ON f.evidence_span_id = es.id
                LEFT JOIN source_document sd ON f.document_id = sd.id
                WHERE {where}
                ORDER BY
                    CASE WHEN f.time_expr IS NULL OR f.time_expr = '' THEN 1 ELSE 0 END,
                    f.time_expr DESC
                LIMIT 500""",
            params,
        ).fetchall()

        facts = [dict(r) for r in rows]

        # 跨文档去重：同一实体的近似事实只保留 confidence 最高的
        def _normalize_time(t):
            """归一化时间表达式用于去重比较"""
            if not t:
                return ""
            import re
            t = t.replace("-至今", "至今").replace("年-", "年").replace("月-", "月")
            # "2024年12月02日" → "2024年12月2日"
            t = re.sub(r"(\d)0(\d日)", r"\1\2", t)
            return t

        def _dedup_key(f):
            """生成去重键，对谓词同义词做归一化"""
            ft = f["fact_type"]
            subj = f["subject_text"] or ""
            vt = f.get("value_text") or ""
            te = _normalize_time(f.get("time_expr") or "")
            obj = f.get("object_text") or ""
            pred = f.get("predicate") or ""

            # 解析 qualifiers
            quals = {}
            qj = f.get("qualifier_json")
            if qj:
                try:
                    quals = json.loads(qj) if isinstance(qj, str) else qj
                except (json.JSONDecodeError, TypeError):
                    pass

            if ft in ("FINANCIAL_METRIC", "SALES_VOLUME"):
                # 区分不同指标：用 metric_name 或 predicate
                metric = quals.get("metric_name") or pred
                return (ft, subj, metric, vt, te)
            elif ft == "COMPETITIVE_RANKING":
                # 归一化排名：用排名值 + 排名上下文
                rank_val = vt or str(f.get("value_num") or "")
                ctx = quals.get("ranking_name") or quals.get("segment") or ""
                return (ft, subj, rank_val, ctx, te)
            elif ft in ("INVESTMENT", "EXPANSION", "COOPERATION"):
                # 用 object + value 区分
                return (ft, subj, obj, vt, te)
            else:
                return (ft, subj, pred, vt, te)

        seen = {}
        deduped = []
        for f in facts:
            key = _dedup_key(f)
            if key in seen:
                existing = seen[key]
                if (f.get("confidence_score") or 0) > (existing.get("confidence_score") or 0):
                    deduped[deduped.index(existing)] = f
                    seen[key] = f
            else:
                seen[key] = f
                deduped.append(f)
        facts = deduped

        # 可用的 fact_type 列表
        type_rows = conn.execute(
            f"""SELECT DISTINCT f.fact_type
                FROM fact_atom f
                WHERE {where.replace("AND f.fact_type = ?", "")}
                ORDER BY f.fact_type""",
            [p for i, p in enumerate(params) if not (fact_type and i == len(params) - 1)],
        ).fetchall()
        available_types = [r["fact_type"] for r in type_rows]

        return {
            "entity_info": entity_info,
            "facts": facts,
            "available_types": available_types,
            "total_count": len(facts),
        }
    finally:
        conn.close()


def get_entity_overview(top_n: int = 10) -> dict:
    """
    为首页提供实体图谱摘要数据。
    返回 {entity_count, linked_fact_count, top_entities, type_dist}
    """
    conn = get_connection()
    try:
        # 只统计有已通过事实关联的实体（与 linked_fact_count 逻辑一致）
        entity_count = conn.execute(
            "SELECT COUNT(DISTINCT subject_entity_id) FROM fact_atom "
            "WHERE review_status IN ('自动通过','人工通过') AND subject_entity_id IS NOT NULL"
        ).fetchone()[0]
        linked_facts = conn.execute(
            "SELECT COUNT(*) FROM fact_atom "
            "WHERE review_status IN ('自动通过','人工通过') AND subject_entity_id IS NOT NULL"
        ).fetchone()[0]
        total_passed = conn.execute(
            "SELECT COUNT(*) FROM fact_atom WHERE review_status IN ('自动通过','人工通过')"
        ).fetchone()[0]
        # Top entities by fact count (subject side)
        top = conn.execute(
            """SELECT e.id, e.canonical_name, e.entity_type, COUNT(f.id) AS fact_count
               FROM entity e
               JOIN fact_atom f ON f.subject_entity_id = e.id
               WHERE f.review_status IN ('自动通过','人工通过')
               GROUP BY e.id
               ORDER BY fact_count DESC
               LIMIT ?""",
            (top_n,),
        ).fetchall()
        # 只统计有已通过事实的实体类型分布
        type_dist = conn.execute(
            """SELECT e.entity_type, COUNT(DISTINCT e.id) AS cnt
               FROM entity e
               JOIN fact_atom f ON f.subject_entity_id = e.id
               WHERE f.review_status IN ('自动通过','人工通过')
               GROUP BY e.entity_type ORDER BY cnt DESC"""
        ).fetchall()
        return {
            "entity_count": entity_count,
            "linked_fact_count": linked_facts,
            "total_passed": total_passed,
            "top_entities": [dict(r) for r in top],
            "type_dist": [dict(r) for r in type_dist],
        }
    finally:
        conn.close()


def get_entity_hierarchy() -> dict:
    """
    从 entity_relation 构建层级树数据。
    返回 {roots: [...]}。
    roots: 嵌套 children 结构的顶层实体列表（无 parent 的为根，含直接子节点）
    """
    conn = get_connection()
    try:
        # 读取所有实体关系
        rows = conn.execute("""
            SELECT r.from_entity_id, r.to_entity_id, r.relation_type,
                   e1.canonical_name AS from_name, e1.entity_type AS from_type,
                   e2.canonical_name AS to_name, e2.entity_type AS to_type
            FROM entity_relation r
            JOIN entity e1 ON e1.id = r.from_entity_id
            JOIN entity e2 ON e2.id = r.to_entity_id
        """).fetchall()

        # 构建 parent_id -> children 映射
        children_map: dict[str, list] = {}
        all_entity_ids = set()
        entity_info: dict = {}

        for r in rows:
            parent_id = r["from_entity_id"]
            child_id = r["to_entity_id"]
            all_entity_ids.add(parent_id)
            all_entity_ids.add(child_id)
            entity_info[parent_id] = {"name": r["from_name"], "type": r["from_type"]}
            entity_info[child_id] = {"name": r["to_name"], "type": r["to_type"]}
            children_map.setdefault(parent_id, []).append({
                "id": child_id,
                "name": r["to_name"],
                "entity_type": r["to_type"],
                "relation_type": r["relation_type"],
                "fact_count": 0,
            })

        # 找出根节点（出现在 from_entity_id 从未出现在 to_entity_id 的）
        to_ids = {r["to_entity_id"] for r in rows}
        root_ids = [eid for eid in all_entity_ids if eid not in to_ids]

        # 获取每个实体的关联事实数
        entity_fact_count: dict = {}
        if all_entity_ids:
            counts = conn.execute(
                f"""SELECT subject_entity_id, COUNT(*) as cnt FROM fact_atom
                    WHERE subject_entity_id IN ({','.join('?' * len(all_entity_ids))})
                    AND review_status IN ('自动通过','人工通过')
                    GROUP BY subject_entity_id""",
                list(all_entity_ids),
            ).fetchall()
            for row in counts:
                entity_fact_count[row["subject_entity_id"]] = row["cnt"]

        # 为 children 补充 fact_count
        for parent_id in children_map:
            for child in children_map[parent_id]:
                child["fact_count"] = entity_fact_count.get(child["id"], 0)

        # 构建 roots 树（只取两层：root + direct children）
        roots = []
        for eid in root_ids:
            info = entity_info.get(eid, {"name": "未知", "type": "UNKNOWN"})
            children = children_map.get(eid, [])
            roots.append({
                "id": eid,
                "name": info["name"],
                "entity_type": info["type"],
                "fact_count": entity_fact_count.get(eid, 0),
                "relation_type": None,
                "children": children,
            })

        return {"roots": roots}
    finally:
        conn.close()
