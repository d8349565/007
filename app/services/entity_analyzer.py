"""
实体关联分析服务（事实驱动 + 名称相似两类候选 → LLM 分析 → entity_relation_suggestion 表）

主要函数：
- analyze_entity(entity_id)   : 分析单个实体，写入建议，返回统计
- get_suggestions(...)        : 查询建议列表
- confirm_suggestion(id)      : 确认建议（建立关系/别名/合并）
- reject_suggestion(id)       : 拒绝建议
"""
import json
import re
import uuid
from pathlib import Path
from typing import Optional

from app.logger import get_logger
from app.models.db import get_connection

logger = get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "entity_relation_analysis.txt"

# 用于名称相似计算的法人后缀（同 entity_linker）
_LEGAL_SUFFIXES = ('有限公司', '有限责任公司', '股份有限公司', '股份公司')
_PAREN_RE = re.compile(r'[（(][^）)]*[）)]')

# 事实类型：这些类型的 object_text 可能暗示实体关联
_FACT_TYPES_WITH_OBJECT = ('COOPERATION', 'INVESTMENT', 'EXPANSION')

# 至少多少字符才参与候选（避免太短的词）
_MIN_LEN = 4

# 网络搜索确认后自动入库的置信度阈值
_AUTO_CONFIRM_THRESHOLD = 0.85


def _strip_legal(name: str) -> str:
    """去括号 + 去法人后缀，用于相似度比较"""
    s = _PAREN_RE.sub('', name).strip()
    for suf in _LEGAL_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
            break
    return s


def _contain_score(a: str, b: str) -> float:
    """若一方包含另一方，返回分数"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer:
        return 0.5 + (len(shorter) / len(longer)) * 0.45
    return 0.0


def _get_entity_info(entity_id: str, conn) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, canonical_name, entity_type FROM entity WHERE id=?",
        (entity_id,),
    ).fetchone()
    return dict(row) if row else None


def _get_sample_facts(entity_id: str, conn, limit: int = 5) -> list[str]:
    """取实体已通过事实的简短摘要，作为 LLM 上下文"""
    rows = conn.execute(
        """SELECT fact_type, predicate, object_text, value_num, value_text, unit, time_expr
           FROM fact_atom
           WHERE subject_entity_id = ?
             AND review_status IN ('AUTO_PASS','HUMAN_PASS')
           LIMIT ?""",
        (entity_id, limit),
    ).fetchall()
    result = []
    for r in rows:
        parts = [r["fact_type"], r["predicate"]]
        if r["object_text"]:
            parts.append(r["object_text"])
        if r["value_num"] is not None:
            parts.append(f"{r['value_num']}{r['unit'] or ''}")
        if r["time_expr"]:
            parts.append(r["time_expr"])
        result.append(" | ".join(parts))
    return result


def _extract_fact_driven_candidates(entity_id: str, conn) -> list[dict]:
    """
    查询 COOPERATION/INVESTMENT/EXPANSION 事实，提取 object_text 作为候选"关联目标"。
    跳过已正式链接的（object_entity_id 指向自己）。
    """
    rows = conn.execute(
        """SELECT id, fact_type, predicate, object_text, object_entity_id,
                  qualifier_json
           FROM fact_atom
           WHERE subject_entity_id = ?
             AND fact_type IN ('COOPERATION','INVESTMENT','EXPANSION')
             AND object_text IS NOT NULL
             AND object_text != ''
             AND review_status IN ('AUTO_PASS','HUMAN_PASS')""",
        (entity_id,),
    ).fetchall()

    seen = set()
    candidates = []
    for r in rows:
        target = (r["object_text"] or "").strip()
        if not target or len(target) < _MIN_LEN or target in seen:
            continue
        seen.add(target)

        # 尝试从已有实体中找到匹配
        linked_id = r["object_entity_id"]
        if not linked_id:
            # 查是否有同名实体
            found = conn.execute(
                "SELECT id FROM entity WHERE canonical_name=?", (target,)
            ).fetchone()
            if found:
                linked_id = found["id"]

        # 推断关联类型（根据 fact_type）
        rel_hint = "PARTNER"
        if r["fact_type"] == "INVESTMENT":
            rel_hint = "INVESTS_IN"
        elif r["fact_type"] == "COOPERATION":
            # 合作类型可能是合资 → JV
            try:
                q = json.loads(r["qualifier_json"] or "{}")
                if q.get("cooperation_type") in ("joint_venture", "合资"):
                    rel_hint = "JV"
            except Exception:
                pass

        # 构造证据摘要
        evidence = r["predicate"] or r["fact_type"]
        if r["object_text"]:
            evidence = f"{evidence}：{r['object_text']}"

        candidates.append({
            "target_name": target,
            "target_entity_id": linked_id,
            "source": "fact_driven",
            "fact_type": r["fact_type"],
            "rel_hint": rel_hint,
            "evidence": evidence,
            "evidence_fact_id": r["id"],
        })

    return candidates


def _extract_name_similar_candidates(entity_id: str, conn) -> list[dict]:
    """
    将本实体名称（含别名）与其他所有实体做包含关系比较，
    返回潜在可合并或上下位关系的候选。
    """
    entity = _get_entity_info(entity_id, conn)
    if not entity:
        return []

    # 本实体及其所有别名
    own_names = {entity["canonical_name"]}
    alias_rows = conn.execute(
        "SELECT alias_name FROM entity_alias WHERE entity_id=?", (entity_id,)
    ).fetchall()
    for a in alias_rows:
        own_names.add(a["alias_name"])

    # 去掉法人后缀后的标准形
    own_stripped = {_strip_legal(n) for n in own_names}
    own_stripped.discard("")

    all_entities = conn.execute(
        "SELECT id, canonical_name, entity_type FROM entity WHERE id != ?",
        (entity_id,),
    ).fetchall()

    candidates = []
    for other in all_entities:
        other_name = other["canonical_name"]
        other_stripped = _strip_legal(other_name)

        # 检查包含关系（用规范化后的名称）
        score = 0.0
        for own in own_stripped:
            if not own or len(own) < _MIN_LEN:
                continue
            s = _contain_score(own, other_stripped if other_stripped else other_name)
            if s > score:
                score = s

        if score < 0.72:
            continue

        # 推断关系倾向：若 other 比 self 更长（other 包含 self）→ self 可能是 other 的品牌/通称 → SUBSIDIARY
        # 若 self 包含 other → other 可能是 self 的上级 → SUBSIDIARY（反向）
        entity_name_stripped = _strip_legal(entity["canonical_name"])
        if other_stripped and entity_name_stripped in other_stripped:
            rel_hint = "SUBSIDIARY"  # other 可能是 self 子公司/工厂
        elif other_stripped and other_stripped in entity_name_stripped:
            rel_hint = "SUBSIDIARY"  # self 可能是 other 的子公司
        else:
            rel_hint = "merge"  # 名称高度相似 → 候选合并

        candidates.append({
            "target_name": other_name,
            "target_entity_id": other["id"],
            "source": "name_similar",
            "score": round(score, 3),
            "rel_hint": rel_hint,
            "evidence": f"名称相似度 {score:.0%}（包含关系）",
            "evidence_fact_id": None,
        })

    # 按分数降序，最多返回 10 条
    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    return candidates[:10]


def analyze_entity(
    entity_id: str,
    llm_client=None,
    use_web_search: bool = False,
) -> dict:
    """
    对单个实体进行全面关联分析：
      1. 提取两类候选（事实驱动 + 名称相似）
      2. （可选）网络搜索 + LLM 知识查询丰富上下文
      3. LLM 综合分析，写入 entity_relation_suggestion 表
      4. 置信度 >= _AUTO_CONFIRM_THRESHOLD 且有网络搜索佐证时，自动确认入库

    返回 {"entity_id", "entity_name", "fact_driven", "name_similar",
           "llm_analyzed", "web_searched", "written", "auto_confirmed"}
    """
    from app.services.web_searcher import get_entity_background, search_entity_pair

    conn = get_connection()
    try:
        entity = _get_entity_info(entity_id, conn)
        if not entity:
            raise ValueError(f"实体不存在: {entity_id}")

        fact_candidates = _extract_fact_driven_candidates(entity_id, conn)
        name_candidates = _extract_name_similar_candidates(entity_id, conn)
        all_candidates = fact_candidates + name_candidates

        # 查已有 pending 记录，避免重复
        existing_pending = set()
        existing_rows = conn.execute(
            """SELECT target_name FROM entity_relation_suggestion
               WHERE entity_id=? AND status='pending'""",
            (entity_id,),
        ).fetchall()
        for row in existing_rows:
            existing_pending.add(row["target_name"])

        # ── 网络搜索：先获取本实体背景，再逐候选搜索关系 ──
        web_searched = False
        entity_bg_summary = ""
        candidate_search_map: dict[str, str] = {}  # target_name → 搜索摘要

        if use_web_search and all_candidates:
            try:
                bg = get_entity_background(
                    entity["canonical_name"], conn, llm_client=llm_client
                )
                entity_bg_summary = bg.summary_text
                web_searched = bg.search_source != "none"

                # 对每个候选搜索关系（事实驱动候选优先，名称候选只搜前5个）
                fact_targets = [c["target_name"] for c in fact_candidates]
                name_targets = [c["target_name"] for c in name_candidates[:5]]
                for target in set(fact_targets + name_targets):
                    pair_result = search_entity_pair(
                        entity["canonical_name"], target, conn, llm_client=llm_client
                    )
                    if pair_result.summary_text:
                        candidate_search_map[target] = pair_result.summary_text
                        web_searched = True
            except Exception as exc:
                logger.warning("网络搜索环节失败 entity=%s: %s", entity_id, exc)

        # ── LLM 综合分析 ──
        llm_analyzed = False
        llm_results: dict = {}

        if llm_client and all_candidates:
            try:
                # 构造搜索上下文字符串（注入给 LLM）
                search_context = _build_search_context(
                    entity_bg_summary, candidate_search_map
                )
                llm_results = _call_llm(
                    entity, all_candidates, conn, llm_client,
                    web_search_context=search_context,
                )
                llm_analyzed = True
            except Exception as exc:
                logger.warning("entity_analyzer LLM 调用失败 entity=%s: %s", entity_id, exc)

        # ── 写入建议记录 ──
        written = 0
        auto_confirmed = 0

        for c in all_candidates:
            target = c["target_name"]
            if target in existing_pending:
                continue

            # LLM 结果
            llm_info = llm_results.get(target, {})
            suggestion_type = llm_info.get("suggestion_type") or _infer_type(c)
            if suggestion_type == "skip":
                continue
            confidence = float(llm_info.get("confidence") or (
                0.6 if c["source"] == "fact_driven" else c.get("score", 0.5)
            ))
            llm_reason = llm_info.get("reason")
            relation_type = llm_info.get("relation_type") or (
                c.get("rel_hint") if suggestion_type == "relation" else None
            )
            search_evidence = candidate_search_map.get(target) or entity_bg_summary or None

            # 自动入库判断：搜索有结果 + 置信度达标
            has_search_evidence = bool(search_evidence)
            should_auto_confirm = (
                use_web_search
                and has_search_evidence
                and confidence >= _AUTO_CONFIRM_THRESHOLD
                and suggestion_type != "skip"
            )

            status = "pending"
            record_auto = 0

            sugg_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO entity_relation_suggestion
                   (id, entity_id, target_name, target_entity_id,
                    suggestion_type, relation_type, evidence, evidence_fact_id,
                    confidence, llm_reason, search_evidence, auto_confirmed, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sugg_id,
                    entity_id,
                    target,
                    c.get("target_entity_id"),
                    suggestion_type,
                    relation_type,
                    c.get("evidence"),
                    c.get("evidence_fact_id"),
                    confidence,
                    llm_reason,
                    search_evidence,
                    record_auto,
                    status,
                ),
            )
            existing_pending.add(target)
            written += 1

            # 自动确认（在同一事务内执行，失败不影响记录写入）
            if should_auto_confirm:
                try:
                    _auto_confirm_suggestion(
                        sugg_id, entity_id, target, c.get("target_entity_id"),
                        suggestion_type, relation_type, conn
                    )
                    # 更新刚写入的记录为 confirmed
                    conn.execute(
                        """UPDATE entity_relation_suggestion
                           SET status='confirmed', confirmed_at=datetime('now'), auto_confirmed=1
                           WHERE id=?""",
                        (sugg_id,),
                    )
                    auto_confirmed += 1
                    logger.info(
                        "自动入库: %s → %s (%s, %.0f%%)",
                        entity["canonical_name"], target, suggestion_type, confidence * 100,
                    )
                except Exception as exc:
                    logger.warning("自动入库失败 %s→%s: %s", entity["canonical_name"], target, exc)

        conn.commit()
        logger.info(
            "实体分析完成: %s (%s)，候选 %d，写入 %d，自动入库 %d，网络搜索 %s",
            entity["canonical_name"], entity_id,
            len(all_candidates), written, auto_confirmed, web_searched,
        )
        return {
            "entity_id": entity_id,
            "entity_name": entity["canonical_name"],
            "fact_driven": len(fact_candidates),
            "name_similar": len(name_candidates),
            "llm_analyzed": llm_analyzed,
            "web_searched": web_searched,
            "written": written,
            "auto_confirmed": auto_confirmed,
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _build_search_context(entity_bg: str, candidate_map: dict[str, str]) -> str:
    """将网络搜索结果拼装成给 LLM 的上下文文本"""
    parts = []
    if entity_bg:
        parts.append(f"【主体背景】\n{entity_bg}")
    for target, summary in list(candidate_map.items())[:8]:  # 最多8个
        parts.append(f"【与 {target} 的关系搜索结果】\n{summary}")
    return "\n\n".join(parts)


def _auto_confirm_suggestion(
    suggestion_id: str,
    entity_id: str,
    target_name: str,
    target_entity_id: Optional[str],
    suggestion_type: str,
    relation_type: Optional[str],
    conn,
) -> None:
    """
    执行自动入库操作（不提交事务，由调用方统一提交）：
      - relation  → 写 entity_relation
      - alias     → 写 entity_alias
      - merge     → 写 entity_merge_task
    """
    if suggestion_type == "relation":
        if not target_entity_id:
            raise ValueError(f"relation类型自动入库需要 target_entity_id: {target_name}")
        rel_type = relation_type or "PARTNER"
        existing = conn.execute(
            "SELECT id FROM entity_relation WHERE from_entity_id=? AND to_entity_id=? AND relation_type=?",
            (entity_id, target_entity_id, rel_type),
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO entity_relation (id, from_entity_id, to_entity_id, relation_type) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), entity_id, target_entity_id, rel_type),
            )

    elif suggestion_type == "alias":
        existing = conn.execute(
            "SELECT id FROM entity_alias WHERE alias_name=? AND entity_id=?",
            (target_name, entity_id),
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO entity_alias (id, entity_id, alias_name) VALUES (?,?,?)",
                (str(uuid.uuid4()), entity_id, target_name),
            )

    elif suggestion_type == "merge":
        if not target_entity_id:
            raise ValueError(f"merge类型自动入库需要 target_entity_id: {target_name}")
        existing = conn.execute(
            "SELECT id FROM entity_merge_task WHERE primary_id=? AND secondary_id=?",
            (entity_id, target_entity_id),
        ).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO entity_merge_task
                   (id, primary_id, secondary_id, rule_score, rule_reason,
                    llm_verdict, llm_confidence, llm_reason, llm_model, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    entity_id, target_entity_id,
                    0.9, "网络搜索自动确认",
                    "merge", 0.9,
                    "网络搜索与AI知识库共同确认",
                    "auto_web",
                    "pending",  # 合并仍需人工最终批准（不可逆操作）
                ),
            )

    else:
        raise ValueError(f"未知 suggestion_type: {suggestion_type}")


def _infer_type(candidate: dict) -> str:
    """没有 LLM 时，根据候选来源推断 suggestion_type"""
    if candidate["source"] == "fact_driven":
        return "relation"
    if candidate.get("rel_hint") == "merge":
        return "merge"
    return "relation"


def _call_llm(
    entity: dict,
    candidates: list[dict],
    conn,
    llm_client,
    web_search_context: str = "",
) -> dict[str, dict]:
    """
    调用 LLM 分析实体关系建议。
    web_search_context: 网络搜索结果拼装的上下文文本（可为空）。
    返回 {target_name: {"suggestion_type", "confidence", "reason", "relation_type"}}
    """
    prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")

    # 获取实体已通过事实（上下文）
    sample_facts = _get_sample_facts(entity["id"], conn, limit=5)

    # 整理候选列表（精简，避免 prompt 过长）
    candidates_summary = []
    for c in candidates[:15]:  # 最多15条
        item = {
            "target": c["target_name"],
            "source": c["source"],
            "evidence": c.get("evidence", ""),
        }
        if c.get("score"):
            item["similarity"] = c["score"]
        candidates_summary.append(item)

    # 搜索上下文（若有）
    search_section = ""
    if web_search_context:
        search_section = f"\n\n## 网络搜索 / AI知识库背景信息\n\n{web_search_context}"

    user_prompt = prompt_template.replace(
        "{{ENTITY_NAME}}", entity["canonical_name"]
    ).replace(
        "{{ENTITY_TYPE}}", entity["entity_type"]
    ).replace(
        "{{SAMPLE_FACTS}}", "\n".join(f"  - {f}" for f in sample_facts) or "  （无已通过事实）"
    ).replace(
        "{{CANDIDATES}}", json.dumps(candidates_summary, ensure_ascii=False, indent=2)
    ).replace(
        "{{WEB_SEARCH_CONTEXT}}", search_section
    )

    result = llm_client.chat_json(
        system_prompt="你是企业实体关系分析专家，擅长从行业资讯中识别实体间的合并、关系和别名关系。",
        user_prompt=user_prompt,
        temperature=0.1,
        max_tokens=1500,
    )

    parsed: dict | list = result.get("data", {})
    if isinstance(parsed, list):
        parsed = {"suggestions": parsed}

    # 转成 {target_name: info} 便于查找
    output: dict[str, dict] = {}
    for item in parsed.get("suggestions", []):
        target = item.get("target", "")
        if target:
            output[target] = {
                "suggestion_type": item.get("suggestion_type", "relation"),
                "confidence": float(item.get("confidence", 0.6)),
                "reason": item.get("reason", ""),
                "relation_type": item.get("relation_type"),
            }
    return output


def get_suggestions(
    entity_id: Optional[str] = None,
    status: str = "pending",
    limit: int = 100,
) -> list[dict]:
    """查询建议列表，附带实体名称"""
    conn = get_connection()
    try:
        conditions = []
        params: list = []

        if entity_id:
            conditions.append("s.entity_id = ?")
            params.append(entity_id)

        if status != "all":
            conditions.append("s.status = ?")
            params.append(status)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"""SELECT s.*, e.canonical_name AS entity_name, e.entity_type AS entity_type_val
                FROM entity_relation_suggestion s
                JOIN entity e ON s.entity_id = e.id
                {where}
                ORDER BY s.confidence DESC, s.created_at DESC
                LIMIT ?""",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def confirm_suggestion(suggestion_id: str) -> dict:
    """
    确认建议：
      - suggestion_type='relation' → 写入 entity_relation 表
      - suggestion_type='alias'    → 写入 entity_alias 表
      - suggestion_type='merge'    → 发起合并任务（写入 entity_merge_task）
    更新 status='confirmed'
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM entity_relation_suggestion WHERE id=?",
            (suggestion_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"建议不存在: {suggestion_id}")

        s = dict(row)
        op_result: dict = {}

        if s["suggestion_type"] == "relation":
            # 在 entity_relation 中新建关系
            from_id = s["entity_id"]
            to_id = s.get("target_entity_id")
            if not to_id:
                raise ValueError("目标实体 ID 未知，无法建立关系")
            rel_type = s.get("relation_type") or "PARTNER"
            # 检查是否已存在
            existing = conn.execute(
                "SELECT id FROM entity_relation WHERE from_entity_id=? AND to_entity_id=? AND relation_type=?",
                (from_id, to_id, rel_type),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO entity_relation (id, from_entity_id, to_entity_id, relation_type) VALUES (?,?,?,?)",
                    (str(uuid.uuid4()), from_id, to_id, rel_type),
                )
            op_result = {"action": "relation_created", "relation_type": rel_type}

        elif s["suggestion_type"] == "alias":
            entity_id = s["entity_id"]
            alias_name = s["target_name"]
            existing = conn.execute(
                "SELECT id FROM entity_alias WHERE alias_name=? AND entity_id=?",
                (alias_name, entity_id),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO entity_alias (id, entity_id, alias_name) VALUES (?,?,?)",
                    (str(uuid.uuid4()), entity_id, alias_name),
                )
            op_result = {"action": "alias_created", "alias": alias_name}

        elif s["suggestion_type"] == "merge":
            # 写入 entity_merge_task，由人工在合并审核 Tab 中批准
            primary_id = s["entity_id"]
            secondary_id = s.get("target_entity_id")
            if not secondary_id:
                raise ValueError("目标实体 ID 未知，无法发起合并任务")
            existing = conn.execute(
                "SELECT id FROM entity_merge_task WHERE primary_id=? AND secondary_id=?",
                (primary_id, secondary_id),
            ).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO entity_merge_task
                       (id, primary_id, secondary_id, rule_score, rule_reason,
                        llm_verdict, llm_confidence, llm_reason, llm_model, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()),
                        primary_id, secondary_id,
                        s["confidence"], "来自实体关联分析建议",
                        "merge", s["confidence"],
                        s.get("llm_reason") or "AI分析建议合并",
                        "entity_analyzer",
                        "pending",
                    ),
                )
            op_result = {"action": "merge_task_created"}

        # 更新建议状态
        conn.execute(
            "UPDATE entity_relation_suggestion SET status='confirmed', confirmed_at=datetime('now') WHERE id=?",
            (suggestion_id,),
        )
        conn.commit()
        logger.info("确认建议 %s: %s", suggestion_id, op_result)
        return op_result

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reject_suggestion(suggestion_id: str) -> None:
    """拒绝建议，标记 status='rejected'"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE entity_relation_suggestion SET status='rejected' WHERE id=?",
            (suggestion_id,),
        )
        conn.commit()
    finally:
        conn.close()
