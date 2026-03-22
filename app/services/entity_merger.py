"""
实体合并服务（规则 + LLM + 人工审核三层架构）：

规则层：LCS + 包含关系快速筛候选对
LLM层：对候选对调 LLM，基于事实样本判断是否同一实体
审核层：结果写入 entity_merge_task 表，人工批准/拒绝后执行

主要函数：
- get_merge_suggestions()          : 纯规则建议（内存计算，向后兼容）
- generate_merge_tasks(max_llm)    : 规则筛 → LLM 分析 → 写 DB，返回生成数
- get_pending_merge_tasks()        : 查DB，返回待审核任务列表
- approve_task(task_id)            : 执行合并 + 标记 executed
- reject_task(task_id)             : 标记 rejected
- swap_and_approve_task(task_id)   : 交换主从后执行合并
- merge_entities(primary, secondary): 底层执行合并（对外也可直接调用）
"""
import json
import os
import uuid
from pathlib import Path

from app.logger import get_logger
from app.models.db import get_connection
from app.services import entity_utils as eu

logger = get_logger(__name__)

# Prompt 文件路径
_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "entity_merge.txt"

# ──────────── 复用 entity_utils ────────────
contain_score = eu.contain_score
lcs_ratio = eu.lcs_ratio
similarity = eu.similarity
extract_geo_paren = eu.extract_geo_paren
has_region_prefix = eu.has_region_prefix
GEO_QUALIFIERS = eu.GEO_QUALIFIERS
SKIP_NAMES = eu.SKIP_NAMES
REGION_PREFIXES = eu.REGION_PREFIXES


def _normalize(text: str) -> str:
    """内部用：去除空格和中文括号，用于相似度计算"""
    return text.strip().replace("（", "(").replace("）", ")").replace(" ", "")


# ──────────── 推荐生成 ────────────

# 匹配阈值
_THRESHOLD_CONTAIN = 0.70   # 包含关系
_THRESHOLD_LCS = 0.82       # LCS相似


def get_merge_suggestions(limit: int = 50) -> list[dict]:
    """
    [向后兼容] 纯规则合并建议，建议迁移到 entity_analyzer 模块。
    返回推荐合并的实体对列表，按置信度排序。
    每项: {primary, secondary, score, reason}
    """
    conn = get_connection()
    try:
        entities = conn.execute(
            "SELECT id, canonical_name, entity_type FROM entity ORDER BY canonical_name"
        ).fetchall()
        entities = [dict(e) for e in entities]
    finally:
        conn.close()

    # 过滤掉通用词、过短名称
    filtered = [e for e in entities
                if e["canonical_name"] not in eu.SKIP_NAMES
                and len(_normalize(e["canonical_name"])) >= 3]

    suggestions = []
    n = len(filtered)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = filtered[i], filtered[j]
            na, nb = _normalize(a['canonical_name']), _normalize(b['canonical_name'])

            contain = _contain_score(a['canonical_name'], b['canonical_name'])
            lcs = _lcs_ratio(a['canonical_name'], b['canonical_name'])
            score = max(contain, lcs)

            if score < _THRESHOLD_LCS and contain < _THRESHOLD_CONTAIN:
                continue

            # 若两者都有地域前缀（但前缀不同），属于地域区分，不应合并
            if eu.has_region_prefix(a["canonical_name"]) and eu.has_region_prefix(b["canonical_name"]):
                # 只有包含关系达到极高才允许（避免"中国上榜"≈"美国上榜"）
                if contain < 0.90:
                    continue
            # 括号内含不同地区词 → 不同注册主体，直接跳过
            geo_a = eu.extract_geo_paren(a["canonical_name"])
            geo_b = eu.extract_geo_paren(b["canonical_name"])
            if geo_a and geo_b and geo_a != geo_b:
                continue
            # 推断谁做主实体（取较短的、包含另一方的为主）
            if na == nb:
                primary, secondary = a, b
                reason = "名称完全相同（归一化后）"
            elif nb in na:
                # b是a的一部分 → b更短，b为主（通用名）
                primary, secondary = b, a
                reason = f"「{b['canonical_name']}」包含于「{a['canonical_name']}」（口语变体）"
            elif na in nb:
                primary, secondary = a, b
                reason = f"「{a['canonical_name']}」包含于「{b['canonical_name']}」（口语变体）"
            else:
                # LCS相似，取事实数多的为主
                primary, secondary = a, b
                reason = f"名称高度相似 (LCS={lcs:.2f})"

            suggestions.append({
                "primary_id":       primary["id"],
                "primary_name":     primary["canonical_name"],
                "primary_type":     primary["entity_type"],
                "secondary_id":     secondary["id"],
                "secondary_name":   secondary["canonical_name"],
                "secondary_type":   secondary["entity_type"],
                "score":            round(score, 3),
                "reason":           reason,
            })

    # 去重（同一对只保留一次）、按分数降序
    suggestions.sort(key=lambda x: x["score"], reverse=True)
    return suggestions[:limit]


# ──────────── 执行合并 ────────────

def merge_entities(primary_id: str, secondary_id: str) -> dict:
    """
    将 secondary 合并进 primary：
    1. fact_atom.subject_entity_id / object_entity_id / location_entity_id → primary_id
    2. secondary 的 canonical_name 注册为 primary 的 alias
    3. secondary 的已有 alias 全部移交给 primary
    4. 删除 secondary 实体
    返回操作统计 dict。
    """
    conn = get_connection()
    try:
        # 验证两个实体都存在
        primary = conn.execute(
            "SELECT id, canonical_name FROM entity WHERE id=?", (primary_id,)
        ).fetchone()
        secondary = conn.execute(
            "SELECT id, canonical_name FROM entity WHERE id=?", (secondary_id,)
        ).fetchone()
        if not primary:
            raise ValueError(f"主实体不存在: {primary_id}")
        if not secondary:
            raise ValueError(f"待合并实体不存在: {secondary_id}")

        stats = {
            "primary_name": primary["canonical_name"],
            "secondary_name": secondary["canonical_name"],
            "facts_relinked": 0,
            "aliases_moved": 0,
            "relations_transferred": 0,
            "relations_removed": 0,
        }

        # 1. 重新指向 fact_atom（仅更新实际存在的列）
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(fact_atom)").fetchall()}
        relinkable = [c for c in ("subject_entity_id", "object_entity_id", "location_entity_id") if c in existing_cols]
        for col in relinkable:
            result = conn.execute(
                f"UPDATE fact_atom SET {col}=? WHERE {col}=?",
                (primary_id, secondary_id),
            )
            stats["facts_relinked"] += result.rowcount

        # 2. 将 secondary.canonical_name 注册为 alias（若不已存在）
        existing = conn.execute(
            "SELECT id FROM entity_alias WHERE alias_name=? AND entity_id=?",
            (secondary["canonical_name"], primary_id),
        ).fetchone()
        if not existing:
            # 先检查 alias_name 是否被其他实体占用
            conflict = conn.execute(
                "SELECT id FROM entity_alias WHERE alias_name=?",
                (secondary["canonical_name"],),
            ).fetchone()
            if conflict:
                conn.execute(
                    "UPDATE entity_alias SET entity_id=? WHERE alias_name=?",
                    (primary_id, secondary["canonical_name"]),
                )
            else:
                conn.execute(
                    "INSERT INTO entity_alias (id, entity_id, alias_name) VALUES (?,?,?)",
                    (str(uuid.uuid4()), primary_id, secondary["canonical_name"]),
                )
            stats["aliases_moved"] += 1

        # 3. 移交 secondary 的所有 alias 给 primary
        old_aliases = conn.execute(
            "SELECT id, alias_name FROM entity_alias WHERE entity_id=?",
            (secondary_id,),
        ).fetchall()
        for a in old_aliases:
            # 检查 primary 是否已经有这个 alias
            dup = conn.execute(
                "SELECT id FROM entity_alias WHERE alias_name=? AND entity_id=?",
                (a["alias_name"], primary_id),
            ).fetchone()
            if not dup:
                conn.execute(
                    "UPDATE entity_alias SET entity_id=? WHERE id=?",
                    (primary_id, a["id"]),
                )
                stats["aliases_moved"] += 1
            else:
                conn.execute("DELETE FROM entity_alias WHERE id=?", (a["id"],))

        # 4. 处理 entity_relation（secondary 作为 from_entity 时转移到 primary；作为 to_entity 时删除）
        stats["relations_transferred"] = 0
        stats["relations_removed"] = 0
        # secondary 作为 from_entity → 改为 primary
        result = conn.execute(
            "UPDATE entity_relation SET from_entity_id=? WHERE from_entity_id=?",
            (primary_id, secondary_id),
        )
        stats["relations_transferred"] += result.rowcount
        # secondary 作为 to_entity → 删除整条关系（无法保留，因为 to_entity 代表的是被关联方）
        result = conn.execute(
            "DELETE FROM entity_relation WHERE to_entity_id=?",
            (secondary_id,),
        )
        stats["relations_removed"] += result.rowcount

        # 5. 清理所有引用 secondary 的 merge task（避免外键约束失败）
        #    以 secondary 为 secondary_id 的任务：直接删除（已无意义）
        conn.execute(
            "DELETE FROM entity_merge_task WHERE secondary_id=?", (secondary_id,)
        )
        #    以 secondary 为 primary_id 的任务：将 primary_id 改指向新主实体
        conn.execute(
            "UPDATE entity_merge_task SET primary_id=? WHERE primary_id=?",
            (primary_id, secondary_id),
        )

        # 6. 删除 secondary 实体
        conn.execute("DELETE FROM entity WHERE id=?", (secondary_id,))
        conn.commit()

        logger.info(
            "实体合并: '%s'→'%s'，fact_atom 重指向 %d 条，alias %d 条，relation 转移 %d / 删除 %d",
            secondary["canonical_name"], primary["canonical_name"],
            stats["facts_relinked"], stats["aliases_moved"],
            stats["relations_transferred"], stats["relations_removed"],
        )
        return stats

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ──────────────────────────── LLM 分析 ────────────────────────────

def _load_prompt() -> str:
    """加载 entity_merge.txt prompt"""
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _get_entity_sample_facts(entity_id: str, conn, limit: int = 3) -> list[str]:
    """获取实体的若干事实摘要，用于给 LLM 参考"""
    rows = conn.execute(
        """SELECT fact_type, predicate, object_text, value_num, value_text, unit, time_expr
           FROM fact_atom
           WHERE subject_entity_id = ?
             AND review_status IN ('AUTO_PASS','HUMAN_PASS')
           LIMIT ?""",
        (entity_id, limit),
    ).fetchall()
    facts = []
    for r in rows:
        parts = [r["fact_type"], r["predicate"]]
        if r["object_text"]:
            parts.append(r["object_text"])
        if r["value_num"] is not None:
            parts.append(f"{r['value_num']}{r['unit'] or ''}")
        elif r["value_text"]:
            parts.append(r["value_text"])
        if r["time_expr"]:
            parts.append(r["time_expr"])
        facts.append(" ".join(p for p in parts if p))
    return facts


def _call_llm_for_pair(entity_a: dict, entity_b: dict, conn) -> dict:
    """
    调用 LLM 判断两个实体是否应合并。
    返回 {verdict, confidence, primary_name, reason, model}
    出错时返回 verdict='uncertain'。
    """
    from app.services.llm_client import LLMClient

    facts_a = _get_entity_sample_facts(entity_a["id"], conn)
    facts_b = _get_entity_sample_facts(entity_b["id"], conn)

    user_input = json.dumps({
        "entity_a": {
            "name": entity_a["canonical_name"],
            "type": entity_a.get("entity_type", ""),
            "sample_facts": facts_a,
        },
        "entity_b": {
            "name": entity_b["canonical_name"],
            "type": entity_b.get("entity_type", ""),
            "sample_facts": facts_b,
        },
    }, ensure_ascii=False)

    try:
        client = LLMClient()
        result = client.chat_json(
            system_prompt=_load_prompt(),
            user_prompt=user_input,
            temperature=0.0,
            max_tokens=256,
        )
        data = result["data"]
        return {
            "verdict":      data.get("verdict", "uncertain"),
            "confidence":   float(data.get("confidence", 0.5)),
            "primary_name": data.get("primary_name", ""),
            "reason":       data.get("reason", ""),
            "model":        result.get("model", ""),
        }
    except Exception as e:
        logger.warning("LLM 实体分析失败 (%s vs %s): %s",
                       entity_a["canonical_name"], entity_b["canonical_name"], e)
        return {
            "verdict": "uncertain",
            "confidence": 0.0,
            "primary_name": "",
            "reason": f"LLM调用失败: {str(e)[:60]}",
            "model": "",
        }


# ──────────────────────────── 任务生成 ────────────────────────────

def generate_merge_tasks(max_llm_calls: int = 30) -> dict:
    """
    规则层筛选候选对 → LLM 分析 → 写入 entity_merge_task 表。

    已存在于 entity_merge_task 的对（任意状态）不会重复生成。
    max_llm_calls 控制本次最多调用 LLM 的次数，避免费用过高。

    返回 {"new_tasks": int, "llm_analyzed": int, "skipped_existing": int}
    """
    conn = get_connection()
    try:
        # 1. 用规则层生成全量候选对（内存计算）
        candidates = get_merge_suggestions(limit=200)

        # 2. 查询已存在的 (primary_id, secondary_id) 对（任意顺序）
        existing_rows = conn.execute(
            "SELECT primary_id, secondary_id FROM entity_merge_task"
        ).fetchall()
        existing_pairs: set[frozenset] = {
            frozenset([r["primary_id"], r["secondary_id"]]) for r in existing_rows
        }

        # 3. 过滤已处理的对
        new_candidates = [
            c for c in candidates
            if frozenset([c["primary_id"], c["secondary_id"]]) not in existing_pairs
        ]

        skipped = len(candidates) - len(new_candidates)
        new_tasks = 0
        llm_analyzed = 0

        # 4. 按规则分数从高到低处理
        for c in sorted(new_candidates, key=lambda x: x["score"], reverse=True):
            # 先取实体详情
            primary = conn.execute(
                "SELECT id, canonical_name, entity_type FROM entity WHERE id=?",
                (c["primary_id"],)
            ).fetchone()
            secondary = conn.execute(
                "SELECT id, canonical_name, entity_type FROM entity WHERE id=?",
                (c["secondary_id"],)
            ).fetchone()
            if not primary or not secondary:
                continue

            # 规则置信度极高（完全相同归一化名称）→ 直接存 pending，不消耗 LLM
            if c["score"] >= 0.98:
                task_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT OR IGNORE INTO entity_merge_task
                       (id, primary_id, secondary_id, rule_score, rule_reason,
                        llm_verdict, llm_confidence, llm_reason, llm_model, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (task_id, c["primary_id"], c["secondary_id"],
                     c["score"], c["reason"],
                     "merge", 1.0, "名称归一化后完全相同", "rule-only",
                     "pending"),
                )
                new_tasks += 1
                continue

            # LLM 分析（受 max_llm_calls 限制）
            if llm_analyzed >= max_llm_calls:
                # 超额的先不写入，等下次调用
                continue

            llm_result = _call_llm_for_pair(dict(primary), dict(secondary), conn)
            llm_analyzed += 1

            # 如果 LLM 推荐不同的主实体，交换顺序
            final_primary_id = c["primary_id"]
            final_secondary_id = c["secondary_id"]
            if (llm_result["verdict"] == "merge"
                    and llm_result["primary_name"]
                    and llm_result["primary_name"] == secondary["canonical_name"]):
                final_primary_id, final_secondary_id = c["secondary_id"], c["primary_id"]

            task_id = str(uuid.uuid4())
            conn.execute(
                """INSERT OR IGNORE INTO entity_merge_task
                   (id, primary_id, secondary_id, rule_score, rule_reason,
                    llm_verdict, llm_confidence, llm_reason, llm_model, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (task_id, final_primary_id, final_secondary_id,
                 c["score"], c["reason"],
                 llm_result["verdict"],
                 llm_result["confidence"],
                 llm_result["reason"],
                 llm_result["model"],
                 "pending"),
            )
            new_tasks += 1

        conn.commit()
        logger.info("生成合并任务: 新增 %d 条，LLM 分析 %d 次，跳过已有 %d 对",
                    new_tasks, llm_analyzed, skipped)
        return {
            "new_tasks": new_tasks,
            "llm_analyzed": llm_analyzed,
            "skipped_existing": skipped,
        }
    finally:
        conn.close()


# ──────────────────────────── 任务查询 ────────────────────────────

def get_pending_merge_tasks(status: str = "pending") -> list[dict]:
    """
    返回指定状态的合并任务列表，附带实体名称信息。
    status: 'pending' | 'approved' | 'rejected' | 'executed' | 'all'
    """
    conn = get_connection()
    try:
        if status == "all":
            rows = conn.execute(
                """SELECT t.*, ep.canonical_name AS primary_name, ep.entity_type AS primary_type,
                          es.canonical_name AS secondary_name, es.entity_type AS secondary_type
                   FROM entity_merge_task t
                   JOIN entity ep ON t.primary_id = ep.id
                   JOIN entity es ON t.secondary_id = es.id
                   ORDER BY t.llm_verdict DESC, t.rule_score DESC, t.created_at DESC"""
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT t.*, ep.canonical_name AS primary_name, ep.entity_type AS primary_type,
                          es.canonical_name AS secondary_name, es.entity_type AS secondary_type
                   FROM entity_merge_task t
                   JOIN entity ep ON t.primary_id = ep.id
                   JOIN entity es ON t.secondary_id = es.id
                   WHERE t.status = ?
                   ORDER BY t.llm_verdict DESC, t.rule_score DESC, t.created_at DESC""",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_merge_task_stats() -> dict:
    """返回各状态的任务计数"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM entity_merge_task GROUP BY status"
        ).fetchall()
        stats = {"pending": 0, "approved": 0, "rejected": 0, "executed": 0, "total": 0}
        for r in rows:
            stats[r["status"]] = r["cnt"]
            stats["total"] += r["cnt"]
        return stats
    finally:
        conn.close()


# ──────────────────────────── 任务执行 ────────────────────────────

def approve_task(task_id: str) -> dict:
    """
    批准合并任务：执行实体合并 + 标记 executed。
    返回合并操作统计。
    """
    conn = get_connection()
    try:
        task = conn.execute(
            "SELECT * FROM entity_merge_task WHERE id=?", (task_id,)
        ).fetchone()
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        if task["status"] == "executed":
            raise ValueError("任务已执行，不能重复操作")
    finally:
        conn.close()

    # 执行合并
    result = merge_entities(task["primary_id"], task["secondary_id"])

    # 更新任务状态
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE entity_merge_task SET status='executed', reviewed_at=datetime('now') WHERE id=?",
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()

    return result


def reject_task(task_id: str) -> None:
    """拒绝合并任务，标记 rejected。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE entity_merge_task SET status='rejected', reviewed_at=datetime('now') WHERE id=?",
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()


def swap_and_approve_task(task_id: str) -> dict:
    """
    交换主从顺序后执行合并
    （当 LLM 或人工判断认为当前主实体选错了时使用）。
    """
    conn = get_connection()
    try:
        task = conn.execute(
            "SELECT * FROM entity_merge_task WHERE id=?", (task_id,)
        ).fetchone()
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        # 交换主从
        conn.execute(
            "UPDATE entity_merge_task SET primary_id=?, secondary_id=? WHERE id=?",
            (task["secondary_id"], task["primary_id"], task_id),
        )
        conn.commit()
    finally:
        conn.close()
    return approve_task(task_id)


# ──────────────────────────── 批量去重归一化 ────────────────────────────

def dedup_batch_rename(texts: list[str], canonical_name: str, entity_type: str, existing_entity_id: str = "") -> dict:
    """
    批量将多个文本归一化到同一个标准实体名称。

    参数:
        texts: 要归一化的文本列表（不含 canonical_name）
        canonical_name: 标准实体名称
        entity_type: 实体类型
        existing_entity_id: 可选，指定已存在的目标实体 ID

    返回:
        {"entity_id": str, "updated_facts": int, "added_aliases": int}
    """
    from app.services.entity_linker import add_entity

    texts = [t.strip() for t in texts if t.strip()]
    canonical_name = canonical_name.strip()
    entity_type = entity_type.strip()

    conn = get_connection()
    try:
        # 1. 确定目标实体
        if existing_entity_id:
            row = conn.execute("SELECT id FROM entity WHERE id = ?", (existing_entity_id,)).fetchone()
            eid = row["id"] if row else None
        else:
            eid = None

        if not eid:
            # 查找同名实体
            dup_rows = list(conn.execute(
                "SELECT id FROM entity WHERE canonical_name = ? ORDER BY rowid ASC",
                (canonical_name,),
            ).fetchall())
            if dup_rows:
                eid = dup_rows[0]["id"]
                # 自动合并多余的同名实体
                for dup in dup_rows[1:]:
                    _merge_into(dup["id"], eid, conn)
            else:
                eid = add_entity(canonical_name, entity_type)

        # 确保 canonical_name 与目标一致
        conn.execute(
            "UPDATE entity SET canonical_name = ?, entity_type = ? WHERE id = ?",
            (canonical_name, entity_type, eid),
        )

        # 2. 将每个文本作为别名，并合并同名实体
        updated_facts = 0
        added_aliases = 0
        all_texts = list(set(texts + [canonical_name]))

        for text in texts:
            if text == canonical_name:
                continue
            # 若该文本是另一个实体的 canonical_name，先合并
            dup_ents = list(conn.execute(
                "SELECT id FROM entity WHERE canonical_name = ? AND id != ? ORDER BY rowid ASC",
                (text, eid),
            ).fetchall())
            for dup in dup_ents:
                _merge_into(dup["id"], eid, conn)
            # 添加别名
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO entity_alias (id, entity_id, alias_name) VALUES (?,?,?)",
                    (str(uuid.uuid4()), eid, text),
                )
                added_aliases += 1
            except Exception:
                pass

        # 3. 批量更新 fact_atom
        ph = ",".join(["?"] * len(all_texts))
        r1 = conn.execute(
            f"UPDATE fact_atom SET subject_entity_id = ?, subject_text = ? WHERE subject_text IN ({ph})",
            [eid, canonical_name] + all_texts,
        )
        updated_facts += r1.rowcount
        r2 = conn.execute(
            f"UPDATE fact_atom SET object_entity_id = ?, object_text = ? WHERE object_text IN ({ph})",
            [eid, canonical_name] + all_texts,
        )
        updated_facts += r2.rowcount
        conn.commit()

        return {"entity_id": eid, "updated_facts": updated_facts, "added_aliases": added_aliases}

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _merge_into(from_id: str, to_id: str, conn) -> None:
    """将 from_id 实体合并进 to_id（内部用，不提交事务）。"""
    conn.execute("UPDATE fact_atom SET subject_entity_id = ? WHERE subject_entity_id = ?", (to_id, from_id))
    conn.execute("UPDATE fact_atom SET object_entity_id = ? WHERE object_entity_id = ?", (to_id, from_id))
    conn.execute("UPDATE OR IGNORE entity_alias SET entity_id = ? WHERE entity_id = ?", (to_id, from_id))
    conn.execute("UPDATE OR IGNORE entity_relation SET from_entity_id = ? WHERE from_entity_id = ?", (to_id, from_id))
    conn.execute("UPDATE OR IGNORE entity_relation SET to_entity_id = ? WHERE to_entity_id = ?", (to_id, from_id))
    conn.execute("DELETE FROM entity_relation WHERE from_entity_id = ? OR to_entity_id = ?", (from_id, from_id))
    conn.execute("DELETE FROM entity_alias WHERE entity_id = ?", (from_id,))
    conn.execute("DELETE FROM entity WHERE id = ?", (from_id,))
