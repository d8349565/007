"""AI 数据助手服务：Tool-based Agent + SSE 流式输出"""

import json
import re
from pathlib import Path

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection
from app.services.llm_client import get_llm_client, LLMClient

logger = get_logger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


# ──────────────────── 工具定义 ────────────────────

def tool_search_entities(keyword: str, entity_type: str = "", limit: int = 20) -> list[dict]:
    """按关键词和类型搜索实体"""
    conn = get_connection()
    try:
        conditions = ["(e.canonical_name LIKE ? OR ea.alias_name LIKE ?)"]
        params = [f"%{keyword}%", f"%{keyword}%"]
        if entity_type:
            conditions.append("e.entity_type = ?")
            params.append(entity_type)

        sql = f"""
            SELECT DISTINCT e.id, e.canonical_name, e.entity_type,
                   COUNT(DISTINCT f.id) AS fact_count
            FROM entity e
            LEFT JOIN entity_alias ea ON ea.entity_id = e.id
            LEFT JOIN fact_atom f ON (f.subject_entity_id = e.id OR f.object_entity_id = e.id)
                                     AND f.review_status IN ('自动通过','人工通过')
            WHERE {' AND '.join(conditions)}
            GROUP BY e.id
            ORDER BY fact_count DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def tool_get_entity_facts(entity_id: str, fact_type: str = "", limit: int = 30) -> list[dict]:
    """获取指定实体关联的事实原子"""
    conn = get_connection()
    try:
        conditions = [
            "(f.subject_entity_id = ? OR f.object_entity_id = ?)",
            "f.review_status IN ('自动通过','人工通过')",
        ]
        params = [entity_id, entity_id]
        if fact_type:
            conditions.append("f.fact_type = ?")
            params.append(fact_type)

        sql = f"""
            SELECT f.id, f.fact_type, f.subject_text, f.predicate, f.object_text,
                   f.value_num, f.value_text, f.unit, f.currency, f.time_expr,
                   f.location_text, f.qualifier_json, f.confidence_score,
                   es.evidence_text
            FROM fact_atom f
            LEFT JOIN evidence_span es ON f.evidence_span_id = es.id
            WHERE {' AND '.join(conditions)}
            ORDER BY f.time_expr DESC, f.created_at DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def tool_query_facts(subject: str = "", fact_type: str = "",
                     time_from: str = "", time_to: str = "",
                     keyword: str = "", limit: int = 30) -> list[dict]:
    """多条件组合查询事实原子"""
    conn = get_connection()
    try:
        conditions = ["f.review_status IN ('自动通过','人工通过')"]
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
        if keyword:
            conditions.append(
                "(f.subject_text LIKE ? OR f.object_text LIKE ? OR f.predicate LIKE ?)"
            )
            params.extend([f"%{keyword}%"] * 3)

        sql = f"""
            SELECT f.id, f.fact_type, f.subject_text, f.predicate, f.object_text,
                   f.value_num, f.value_text, f.unit, f.currency, f.time_expr,
                   f.location_text, f.qualifier_json, f.confidence_score,
                   es.evidence_text
            FROM fact_atom f
            LEFT JOIN evidence_span es ON f.evidence_span_id = es.id
            WHERE {' AND '.join(conditions)}
            ORDER BY f.time_expr DESC, f.created_at DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def tool_get_relations(entity_id: str) -> list[dict]:
    """获取实体之间的关系"""
    conn = get_connection()
    try:
        sql = """
            SELECT er.id, er.relation_type, er.detail_json,
                   e1.canonical_name AS from_name, e1.entity_type AS from_type,
                   e2.canonical_name AS to_name, e2.entity_type AS to_type
            FROM entity_relation er
            JOIN entity e1 ON er.from_entity_id = e1.id
            JOIN entity e2 ON er.to_entity_id = e2.id
            WHERE er.from_entity_id = ? OR er.to_entity_id = ?
            ORDER BY er.created_at DESC
        """
        rows = conn.execute(sql, (entity_id, entity_id)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def tool_get_stats() -> dict:
    """获取数据库统计概况"""
    conn = get_connection()
    try:
        stats = {}
        stats["total_facts"] = conn.execute(
            "SELECT COUNT(*) FROM fact_atom WHERE review_status IN ('自动通过','人工通过')"
        ).fetchone()[0]
        stats["total_entities"] = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
        stats["total_documents"] = conn.execute("SELECT COUNT(*) FROM source_document").fetchone()[0]
        stats["fact_types"] = [dict(r) for r in conn.execute(
            "SELECT fact_type, COUNT(*) AS cnt FROM fact_atom "
            "WHERE review_status IN ('自动通过','人工通过') GROUP BY fact_type ORDER BY cnt DESC"
        ).fetchall()]
        stats["top_entities"] = [dict(r) for r in conn.execute(
            """SELECT e.canonical_name, e.entity_type, COUNT(f.id) AS fact_count
               FROM entity e
               LEFT JOIN fact_atom f ON (f.subject_entity_id = e.id OR f.object_entity_id = e.id)
                                        AND f.review_status IN ('自动通过','人工通过')
               GROUP BY e.id ORDER BY fact_count DESC LIMIT 10"""
        ).fetchall()]
        return stats
    finally:
        conn.close()


# ──────────────────── 工具注册表 ────────────────────

TOOLS = {
    "search_entities": {
        "fn": tool_search_entities,
        "desc": "搜索实体（按名称关键词、类型）",
        "params": {"keyword": "str, 必填", "entity_type": "str, 可选", "limit": "int, 可选"},
    },
    "get_entity_facts": {
        "fn": tool_get_entity_facts,
        "desc": "获取某实体关联的所有事实原子",
        "params": {"entity_id": "str, 必填", "fact_type": "str, 可选", "limit": "int, 可选"},
    },
    "query_facts": {
        "fn": tool_query_facts,
        "desc": "多条件查询事实原子（主体、类型、时间范围、关键词）",
        "params": {"subject": "str", "fact_type": "str", "time_from": "str", "time_to": "str", "keyword": "str", "limit": "int"},
    },
    "get_relations": {
        "fn": tool_get_relations,
        "desc": "获取实体之间的关系（股权、合资、品牌归属等）",
        "params": {"entity_id": "str, 必填"},
    },
    "get_stats": {
        "fn": tool_get_stats,
        "desc": "获取知识库统计概况（事实总数、实体数、分类统计等）",
        "params": {},
    },
}


def _build_tool_descriptions() -> str:
    """构造工具描述文本，嵌入 system prompt"""
    parts = []
    for name, info in TOOLS.items():
        params = ", ".join(f"{k}: {v}" for k, v in info["params"].items())
        parts.append(f"- {name}({params}): {info['desc']}")
    return "\n".join(parts)


def _execute_tool(name: str, args: dict) -> str:
    """执行工具并返回 JSON 字符串结果"""
    if name not in TOOLS:
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    try:
        fn = TOOLS[name]["fn"]
        result = fn(**args)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error("工具 %s 执行失败: %s", name, e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ──────────────────── 对话主函数 ────────────────────

def _build_system_prompt() -> str:
    """加载并组装系统提示词"""
    base = (_PROMPT_DIR / "ai_chat_system.txt").read_text(encoding="utf-8")
    tool_desc = _build_tool_descriptions()
    return base.replace("{{TOOLS}}", tool_desc)


def chat(messages: list[dict], model_override: str = "", temperature: float = 0.3) -> dict:
    """
    处理一轮对话，支持多轮工具调用。

    参数:
        messages: 对话历史 [{"role": "user"/"assistant", "content": "..."}]
        model_override: 可选，覆盖默认模型
        temperature: 温度

    返回:
        {
            "answer": str,           # 最终回答文本
            "tool_calls": list,      # 工具调用记录 [{name, args, result}]
            "citations": list,       # 引用的事实 ID
            "input_tokens": int,
            "output_tokens": int,
        }
    """
    system_prompt = _build_system_prompt()
    client = get_llm_client()

    # 如需要覆盖模型，创建临时客户端
    if model_override:
        cfg = get_config()["llm"]
        temp_cfg = dict(cfg)
        provider = cfg.get("provider", "deepseek")
        provider_cfg = dict(cfg.get(provider, {}))
        provider_cfg["model"] = model_override
        temp_cfg[provider] = provider_cfg
        client = LLMClient(temp_cfg)

    # 拼装完整消息列表（直接用 role 传递，不做拼接）
    full_messages = [{"role": "system", "content": system_prompt}]
    full_messages.extend(messages)

    tool_calls_log = []
    total_in, total_out = 0, 0
    max_rounds = 8  # 最多工具调用轮次

    for _round in range(max_rounds):
        # 用 chat_messages 发送完整的多轮消息
        result = client.chat_messages(full_messages, temperature=temperature)
        total_in += result["input_tokens"]
        total_out += result["output_tokens"]
        content = result["content"]

        # 找出所有 [TOOL_CALL]...[/TOOL_CALL]
        tool_matches = re.findall(
            r"\[TOOL_CALL\]\s*(\{.*?\})\s*\[/TOOL_CALL\]",
            content, re.DOTALL,
        )

        if not tool_matches:
            # 没有工具调用 => 最终回答
            answer = re.sub(r"\[TOOL_CALL\].*?\[/TOOL_CALL\]", "", content, flags=re.DOTALL).strip()
            citations = re.findall(r"#([a-f0-9\-]{8,36})", answer)
            return {
                "answer": answer,
                "tool_calls": tool_calls_log,
                "citations": citations,
                "input_tokens": total_in,
                "output_tokens": total_out,
            }

        # 解析并执行所有工具调用
        results_text_parts = []
        for match_str in tool_matches:
            try:
                call_data = json.loads(match_str)
                tool_name = call_data.get("tool", "")
                tool_args = call_data.get("args", {})
            except json.JSONDecodeError:
                logger.warning("工具调用 JSON 解析失败: %s", match_str[:200])
                continue

            tool_result = _execute_tool(tool_name, tool_args)
            tool_calls_log.append({"name": tool_name, "args": tool_args, "result": tool_result})
            results_text_parts.append(
                f"[TOOL_RESULT name={tool_name}]\n{tool_result}\n[/TOOL_RESULT]"
            )

        if not results_text_parts:
            # 所有解析都失败，返回清理后的原文
            answer = re.sub(r"\[TOOL_CALL\].*?\[/TOOL_CALL\]", "", content, flags=re.DOTALL).strip()
            return {
                "answer": answer or "工具调用失败，请重试。",
                "tool_calls": tool_calls_log,
                "citations": [],
                "input_tokens": total_in,
                "output_tokens": total_out,
            }

        # 将 assistant 原始回复和工具结果注入对话
        full_messages.append({"role": "assistant", "content": content})
        full_messages.append({
            "role": "user",
            "content": "\n\n".join(results_text_parts),
        })

    # 超过最大轮次
    return {
        "answer": "抱歉，查询过于复杂，请尝试简化问题。",
        "tool_calls": tool_calls_log,
        "citations": [],
        "input_tokens": total_in,
        "output_tokens": total_out,
    }


# ──────────────────── 设置管理 ────────────────────

def get_current_settings() -> dict:
    """获取当前 LLM 和系统设置"""
    cfg = get_config()
    llm = cfg.get("llm", {})
    provider = llm.get("provider", "deepseek")
    provider_cfg = llm.get(provider, {})

    # 获取所有可用 providers
    providers = []
    for key in ("deepseek", "kimi", "minimax"):
        if key in llm:
            pcfg = llm[key]
            providers.append({
                "name": key,
                "model": pcfg.get("model", ""),
                "base_url": pcfg.get("base_url", ""),
                "api_key_env": pcfg.get("api_key_env", ""),
                "has_key": bool(pcfg.get("api_key") or __import__("os").getenv(pcfg.get("api_key_env", ""), "")),
            })

    return {
        "current_provider": provider,
        "current_model": provider_cfg.get("model", llm.get("model", "")),
        "temperature": llm.get("temperature", 0.1),
        "max_tokens": llm.get("max_tokens", 4096),
        "timeout": llm.get("timeout", 120),
        "max_retries": llm.get("max_retries", 3),
        "providers": providers,
        "database_path": cfg.get("database", {}).get("path", ""),
    }
