"""
实体档案服务 —— 从已有数据聚合 + Web/LLM 丰富，生成结构化实体 Profile

主要函数：
- build_entity_profile(entity_id)  : 聚合现有数据（无 LLM），写入 entity_profile 表
- enrich_entity_profile(entity_id) : 调用 web_searcher + LLM 丰富 profile
- get_entity_profile(entity_id)    : 查询 profile（不存在则实时构建）
- build_all_profiles(min_facts)    : 批量构建所有有事实的实体 profile
"""

import json
import uuid
from pathlib import Path

from app.logger import get_logger
from app.models.db import get_connection
from app.services.llm_client import get_llm_client
from app.services.web_searcher import get_entity_background

logger = get_logger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


# ──────────── 聚合构建（纯查询，无 LLM） ────────────

def build_entity_profile(entity_id: str) -> dict:
    """
    从现有数据库（entity / entity_alias / entity_relation / fact_atom /
    entity_search_cache）聚合生成实体 profile，写入 entity_profile 表。

    纯查询 + 聚合，不调用 LLM / Web 搜索，可安全批量执行。

    返回:
        {"entity_id": str, "canonical_name": str, "aliases": list,
         "relations": list, "benchmarks": list, "competitors": list,
         "summary": str, "fact_count": int}
    """
    conn = get_connection()
    try:
        # 1. 实体基本信息
        ent = conn.execute(
            "SELECT id, canonical_name, entity_type FROM entity WHERE id=?",
            (entity_id,),
        ).fetchone()
        if not ent:
            logger.warning("实体不存在: %s", entity_id[:8])
            return {}

        name = ent["canonical_name"]
        etype = ent["entity_type"]

        # 2. 别名列表
        alias_rows = conn.execute(
            "SELECT alias_name FROM entity_alias WHERE entity_id=? ORDER BY alias_name",
            (entity_id,),
        ).fetchall()
        aliases = [r["alias_name"] for r in alias_rows]

        # 3. 关系
        relations = _collect_relations(entity_id, conn)

        # 4. 基准数据（从 fact_atom 中提取关键指标）
        benchmarks = _collect_benchmarks(entity_id, conn)

        # 5. 竞争对手
        competitors = _collect_competitors(entity_id, conn)

        # 6. 已有的搜索摘要
        cache_row = conn.execute(
            """SELECT summary_text FROM entity_search_cache
               WHERE entity_name = ? AND query = ?
               ORDER BY created_at DESC LIMIT 1""",
            (name, name),
        ).fetchone()
        summary = cache_row["summary_text"] if cache_row else ""

        # 7. 总事实数
        fact_count = conn.execute(
            """SELECT COUNT(*) FROM fact_atom
               WHERE subject_entity_id = ?
                 AND review_status IN ('自动通过', '人工通过')""",
            (entity_id,),
        ).fetchone()[0]

        # 8. 写入 entity_profile
        profile = {
            "entity_id": entity_id,
            "canonical_name": name,
            "entity_type": etype,
            "aliases": aliases,
            "relations": relations,
            "benchmarks": benchmarks,
            "competitors": competitors,
            "summary": summary,
            "fact_count": fact_count,
        }

        _upsert_profile(entity_id, profile, "聚合", conn)
        conn.commit()

        logger.info(
            "构建实体档案: %s — 别名%d 关系%d 指标%d 竞品%d",
            name, len(aliases), len(relations), len(benchmarks), len(competitors),
        )
        return profile

    except Exception as e:
        logger.error("构建实体档案失败 [%s]: %s", entity_id[:8], e)
        return {}
    finally:
        conn.close()


def _collect_relations(entity_id: str, conn) -> list[dict]:
    """从 entity_relation 表聚合关系（双向）"""
    rels_from = conn.execute(
        """SELECT r.relation_type, r.detail_json,
                  e.id AS target_id, e.canonical_name AS target_name
           FROM entity_relation r
           JOIN entity e ON r.to_entity_id = e.id
           WHERE r.from_entity_id = ?""",
        (entity_id,),
    ).fetchall()

    rels_to = conn.execute(
        """SELECT r.relation_type, r.detail_json,
                  e.id AS target_id, e.canonical_name AS target_name
           FROM entity_relation r
           JOIN entity e ON r.from_entity_id = e.id
           WHERE r.to_entity_id = ?""",
        (entity_id,),
    ).fetchall()

    relations = []
    for r in rels_from:
        detail = json.loads(r["detail_json"]) if r["detail_json"] else {}
        relations.append({
            "target_name": r["target_name"],
            "target_id": r["target_id"],
            "relation_type": r["relation_type"],
            "direction": "outgoing",
            "detail": detail,
        })
    for r in rels_to:
        detail = json.loads(r["detail_json"]) if r["detail_json"] else {}
        relations.append({
            "target_name": r["target_name"],
            "target_id": r["target_id"],
            "relation_type": r["relation_type"],
            "direction": "incoming",
            "detail": detail,
        })
    return relations


def _collect_benchmarks(entity_id: str, conn) -> list[dict]:
    """从 fact_atom 中提取关键指标，按 (fact_type, predicate, time) 去重"""
    rows = conn.execute(
        """SELECT id, fact_type, predicate, value_num, value_text,
                  unit, currency, time_expr
           FROM fact_atom
           WHERE subject_entity_id = ?
             AND review_status IN ('自动通过', '人工通过')
             AND value_num IS NOT NULL
           ORDER BY time_expr DESC
           LIMIT 50""",
        (entity_id,),
    ).fetchall()

    benchmarks = []
    seen = set()
    for r in rows:
        key = (r["fact_type"], r["predicate"], r["time_expr"])
        if key in seen:
            continue
        seen.add(key)
        benchmarks.append({
            "fact_type": r["fact_type"],
            "metric": r["predicate"],
            "value": r["value_num"],
            "value_text": r["value_text"],
            "unit": r["unit"],
            "currency": r["currency"],
            "time": r["time_expr"],
            "fact_id": r["id"],
        })
    return benchmarks


def _collect_competitors(entity_id: str, conn) -> list[dict]:
    """从 COMPETITIVE_RANKING 事实中提取竞争对手"""
    # 方式1：该实体 COMPETITIVE_RANKING 事实中的 object_text
    direct = conn.execute(
        """SELECT DISTINCT object_text, object_entity_id
           FROM fact_atom
           WHERE subject_entity_id = ?
             AND fact_type = 'COMPETITIVE_RANKING'
             AND object_text IS NOT NULL AND object_text != ''
             AND review_status IN ('自动通过', '人工通过')""",
        (entity_id,),
    ).fetchall()

    competitors = {}
    for r in direct:
        name = r["object_text"]
        if name not in competitors:
            competitors[name] = {"name": name, "entity_id": r["object_entity_id"]}

    # 方式2：同篇文档中 COMPETITIVE_RANKING 的其他主体
    co_occurring = conn.execute(
        """SELECT DISTINCT f2.subject_text, f2.subject_entity_id
           FROM fact_atom f1
           JOIN fact_atom f2 ON f1.document_id = f2.document_id
           WHERE f1.subject_entity_id = ?
             AND f1.fact_type = 'COMPETITIVE_RANKING'
             AND f2.fact_type = 'COMPETITIVE_RANKING'
             AND f2.subject_entity_id IS NOT NULL
             AND f2.subject_entity_id != ?
             AND f2.review_status IN ('自动通过', '人工通过')
           LIMIT 20""",
        (entity_id, entity_id),
    ).fetchall()

    for r in co_occurring:
        name = r["subject_text"]
        if name not in competitors:
            competitors[name] = {"name": name, "entity_id": r["subject_entity_id"]}

    return list(competitors.values())


def _upsert_profile(entity_id: str, data: dict, source: str, conn):
    """写入或更新 entity_profile 表"""
    existing = conn.execute(
        "SELECT id FROM entity_profile WHERE entity_id=?", (entity_id,),
    ).fetchone()

    aliases_json = json.dumps(data["aliases"], ensure_ascii=False)
    relations_json = json.dumps(data["relations"], ensure_ascii=False)
    benchmarks_json = json.dumps(data["benchmarks"], ensure_ascii=False)
    competitors_json = json.dumps(data["competitors"], ensure_ascii=False)

    if existing:
        conn.execute(
            """UPDATE entity_profile
               SET aliases_json=?, relations_json=?, benchmarks_json=?,
                   competitors_json=?, summary_text=?, fact_count=?,
                   profile_source=?, last_built_at=datetime('now')
               WHERE entity_id=?""",
            (aliases_json, relations_json, benchmarks_json,
             competitors_json, data["summary"], data["fact_count"],
             source, entity_id),
        )
    else:
        conn.execute(
            """INSERT INTO entity_profile
               (id, entity_id, aliases_json, relations_json, benchmarks_json,
                competitors_json, summary_text, fact_count, profile_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), entity_id, aliases_json, relations_json,
             benchmarks_json, competitors_json, data["summary"],
             data["fact_count"], source),
        )


# ──────────── Web/LLM 丰富 ────────────

def enrich_entity_profile(entity_id: str) -> dict:
    """
    通过 Web 搜索 + LLM 丰富实体 profile。

    流程：
    1. 调用 web_searcher.get_entity_background() 获取/缓存背景
    2. 用 LLM 从背景文本中提取结构化字段（别名、竞争对手、摘要）
    3. 新发现的别名写入 entity_alias（后续 entity_linker 自动受益）
    4. 重新聚合构建 profile

    返回:
        {"entity_id": str, "new_aliases": list, "enriched_fields": list, ...}
    """
    # 1. 获取实体信息 + Web 搜索
    conn = get_connection()
    try:
        ent = conn.execute(
            "SELECT id, canonical_name, entity_type FROM entity WHERE id=?",
            (entity_id,),
        ).fetchone()
        if not ent:
            logger.warning("实体不存在: %s", entity_id[:8])
            return {}

        name = ent["canonical_name"]

        # Web 搜索（结果自动缓存到 entity_search_cache）
        client = get_llm_client()
        bg_result = get_entity_background(name, conn, llm_client=client)
        summary = bg_result.summary_text if bg_result else ""

        # 已知别名（排除用）
        existing_aliases = [r["alias_name"] for r in conn.execute(
            "SELECT alias_name FROM entity_alias WHERE entity_id=?", (entity_id,),
        ).fetchall()]
    finally:
        conn.close()

    if not summary:
        logger.info("未获取到背景信息，直接聚合: %s", name)
        return build_entity_profile(entity_id)

    # 2. LLM 结构化提取
    prompt_text = (_PROMPT_DIR / "entity_profile_enrich.txt").read_text(encoding="utf-8")
    user_input = json.dumps({
        "entity_name": name,
        "entity_type": ent["entity_type"],
        "background": summary,
        "known_aliases": existing_aliases,
    }, ensure_ascii=False)

    try:
        result = client.chat_json(prompt_text, user_input)
        parsed = result.get("data", {})
    except Exception as e:
        logger.error("LLM 提取失败 [%s]: %s", name, e)
        return build_entity_profile(entity_id)

    # 3. 写入新发现的别名
    new_aliases_added = []
    enriched_fields = []
    new_aliases = parsed.get("aliases", [])

    if new_aliases:
        conn = get_connection()
        try:
            for alias in new_aliases:
                alias = str(alias).strip()
                if not alias or alias == name or alias in existing_aliases:
                    continue
                try:
                    conn.execute(
                        "INSERT INTO entity_alias (id, entity_id, alias_name) VALUES (?, ?, ?)",
                        (str(uuid.uuid4()), entity_id, alias),
                    )
                    new_aliases_added.append(alias)
                    logger.info("新增别名: %s → %s", name, alias)
                except Exception:
                    pass  # UNIQUE 冲突，别名已存在
            conn.commit()
        finally:
            conn.close()

    if new_aliases_added:
        enriched_fields.append("aliases")

    # 4. 重新聚合构建 profile（会包含新别名 + 搜索摘要）
    profile = build_entity_profile(entity_id)

    # 5. 更新丰富元信息 + 补充 LLM 发现的竞争对手
    conn = get_connection()
    try:
        # 补充竞争对手
        llm_competitors = parsed.get("competitors", [])
        if llm_competitors:
            existing_comp = json.loads(
                conn.execute(
                    "SELECT competitors_json FROM entity_profile WHERE entity_id=?",
                    (entity_id,),
                ).fetchone()["competitors_json"]
            )
            existing_names = {c["name"] for c in existing_comp}
            added_comp = False
            for comp_name in llm_competitors:
                comp_name = str(comp_name).strip()
                if comp_name and comp_name not in existing_names:
                    existing_comp.append({"name": comp_name, "entity_id": None})
                    added_comp = True
            if added_comp:
                conn.execute(
                    "UPDATE entity_profile SET competitors_json=? WHERE entity_id=?",
                    (json.dumps(existing_comp, ensure_ascii=False), entity_id),
                )
                enriched_fields.append("competitors")

        # 补充摘要
        llm_summary = parsed.get("summary", "")
        if llm_summary and len(llm_summary) > len(profile.get("summary", "")):
            conn.execute(
                "UPDATE entity_profile SET summary_text=? WHERE entity_id=?",
                (llm_summary, entity_id),
            )
            enriched_fields.append("summary")

        # 更新来源标记
        conn.execute(
            """UPDATE entity_profile
               SET profile_source='聚合+搜索', last_enriched_at=datetime('now')
               WHERE entity_id=?""",
            (entity_id,),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info(
        "丰富实体档案: %s — 新别名%d 丰富字段%s",
        name, len(new_aliases_added), enriched_fields,
    )
    return {
        "entity_id": entity_id,
        "canonical_name": name,
        "new_aliases": new_aliases_added,
        "enriched_fields": enriched_fields,
        "profile": profile,
    }


# ──────────── 查询 ────────────

def get_entity_profile(entity_id: str) -> dict:
    """获取实体 profile，不存在则实时聚合构建"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM entity_profile WHERE entity_id=?", (entity_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return build_entity_profile(entity_id)

    return {
        "entity_id": row["entity_id"],
        "aliases": json.loads(row["aliases_json"]),
        "relations": json.loads(row["relations_json"]),
        "benchmarks": json.loads(row["benchmarks_json"]),
        "competitors": json.loads(row["competitors_json"]),
        "summary": row["summary_text"],
        "profile_source": row["profile_source"],
        "fact_count": row["fact_count"],
        "last_built_at": row["last_built_at"],
        "last_enriched_at": row["last_enriched_at"],
    }


# ──────────── 批量构建 ────────────

def build_all_profiles(min_facts: int = 1) -> dict:
    """
    批量构建所有有已通过事实的实体 profile。

    参数:
        min_facts: 最少关联事实数（低于此数的实体跳过）

    返回:
        {"total": int, "built": int, "failed": int}
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT subject_entity_id
               FROM fact_atom
               WHERE subject_entity_id IS NOT NULL
                 AND review_status IN ('自动通过', '人工通过')
               GROUP BY subject_entity_id
               HAVING COUNT(*) >= ?""",
            (min_facts,),
        ).fetchall()
    finally:
        conn.close()

    entity_ids = [r["subject_entity_id"] for r in rows]
    built = 0
    failed = 0

    for eid in entity_ids:
        result = build_entity_profile(eid)
        if result:
            built += 1
        else:
            failed += 1

    logger.info("批量构建档案: 总%d 成功%d 失败%d", len(entity_ids), built, failed)
    return {"total": len(entity_ids), "built": built, "failed": failed}
