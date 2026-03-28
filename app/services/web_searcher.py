"""
实体背景搜索服务 —— 两层检索 + 本地缓存

检索策略（按优先级）：
  1. 本地缓存（entity_search_cache 表）命中 → 直接返回，不发请求
  2. DuckDuckGo Instant Answer API（免费，无需 key，秒级响应）
  3. LLM 知识查询（DeepSeek 凭训练数据介绍实体背景）

对外接口：
  get_entity_background(entity_name, conn, llm_client=None) → BackgroundResult
  search_entity_pair(entity_a, entity_b, conn, llm_client=None)  → BackgroundResult

关于缓存 key 规则：
  - 单实体   : 直接使用 entity_name（标准化后）
  - 实体对   : "entity_a||entity_b"（字母序排序，保证双向唯一）
"""
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import requests

from app.logger import get_logger

logger = get_logger(__name__)

# DuckDuckGo Instant Answer API
_DDG_API = "https://api.duckduckgo.com/"
_DDG_TIMEOUT = 8   # 秒

# 搜索摘要字数限制（给 LLM 的上下文不宜过长）
_SUMMARY_MAX_CHARS = 600

# LLM 背景查询的 system prompt（用中文，DeepSeek 效果更好）
_LLM_BACKGROUND_SYSTEM = (
    "你是一个企业背景知识库。你的任务是简洁准确地介绍用户指定的企业或项目主体，"
    "重点说明：正式名称、所属集团/母公司、主要股东、主营业务，以及任何已知的子公司、"
    "合资企业或品牌关系。不确定的信息请标注《不确定》。"
    "回答控制在 200 字以内，不要发散。"
)

_LLM_PAIR_SYSTEM = (
    "你是一个企业关系分析专家。你的任务是简洁说明两个企业/实体之间是否存在已知的"
    "归属、合并、合资、合作或品牌关系。重要：只说你确实知道的，不要猜测。"
    "回答控制在 150 字以内。"
)


@dataclass
class BackgroundResult:
    entity_name: str
    query: str
    summary_text: str
    search_source: str   # 'cache' | '搜索引擎' | 'LLM知识' | '综合'
    raw_results: dict = field(default_factory=dict)


# ─────────────────────── 公共接口 ───────────────────────

def get_entity_background(
    entity_name: str,
    conn,
    llm_client=None,
    force_refresh: bool = False,
) -> BackgroundResult:
    """
    获取单个实体的背景摘要。
    优先从缓存读取；缓存未命中时先尝试 DuckDuckGo，再用 LLM 兜底。
    结果写入缓存后返回。
    """
    query_key = _normalize_key(entity_name)

    if not force_refresh:
        cached = _get_cached(query_key, conn)
        if cached:
            logger.debug("实体背景命中缓存: %s", entity_name)
            return BackgroundResult(
                entity_name=entity_name,
                query=query_key,
                summary_text=cached["summary_text"] or "",
                search_source="cache",
                raw_results=json.loads(cached["raw_results"] or "{}"),
            )

    # 1. DuckDuckGo
    ddg_result = _search_duckduckgo(entity_name)
    ddg_summary = _extract_ddg_summary(ddg_result, entity_name)

    # 2. LLM 知识查询（若 DDG 结果不足或无 LLM 时跳过）
    llm_summary = ""
    if llm_client:
        llm_summary = _query_llm_knowledge(entity_name, llm_client)

    # 合并摘要
    if ddg_summary and llm_summary:
        summary = f"[网络搜索] {ddg_summary}\n[AI知识库] {llm_summary}"
        source = "combined"
    elif ddg_summary:
        summary = f"[网络搜索] {ddg_summary}"
        source = "duckduckgo"
    elif llm_summary:
        summary = f"[AI知识库] {llm_summary}"
        source = "llm_knowledge"
    else:
        summary = ""
        source = "none"

    # 裁剪长度
    summary = summary[:_SUMMARY_MAX_CHARS]

    # 写缓存
    if summary:
        raw = {"ddg": ddg_result, "llm": llm_summary}
        _save_cache(entity_name, query_key, source, raw, summary, conn)

    return BackgroundResult(
        entity_name=entity_name,
        query=query_key,
        summary_text=summary,
        search_source=source,
        raw_results={"ddg": ddg_result, "llm": llm_summary},
    )


def search_entity_pair(
    entity_a: str,
    entity_b: str,
    conn,
    llm_client=None,
    force_refresh: bool = False,
) -> BackgroundResult:
    """
    搜索两个实体之间的已知关系。
    用 "entity_a||entity_b"（字母序）作为缓存 key。
    """
    names = sorted([_normalize_key(entity_a), _normalize_key(entity_b)])
    query_key = f"{names[0]}||{names[1]}"

    if not force_refresh:
        cached = _get_cached(query_key, conn)
        if cached:
            logger.debug("实体对命中缓存: %s / %s", entity_a, entity_b)
            return BackgroundResult(
                entity_name=entity_a,
                query=query_key,
                summary_text=cached["summary_text"] or "",
                search_source="cache",
            )

    # DuckDuckGo 搜索两者关系
    pair_query_zh = f"{entity_a} {entity_b} 关系 母公司 子公司"
    ddg_result = _search_duckduckgo(pair_query_zh)
    ddg_summary = _extract_ddg_summary(ddg_result, entity_a)

    # LLM 关系判断
    llm_summary = ""
    if llm_client:
        llm_summary = _query_llm_pair(entity_a, entity_b, llm_client)

    if ddg_summary and llm_summary:
        summary = f"[网络] {ddg_summary}\n[AI] {llm_summary}"
        source = "combined"
    elif ddg_summary:
        summary = f"[网络] {ddg_summary}"
        source = "duckduckgo"
    elif llm_summary:
        summary = f"[AI] {llm_summary}"
        source = "llm_knowledge"
    else:
        summary = ""
        source = "none"

    summary = summary[:_SUMMARY_MAX_CHARS]

    if summary:
        raw = {"ddg": ddg_result, "llm": llm_summary, "pair": [entity_a, entity_b]}
        _save_cache(entity_a, query_key, source, raw, summary, conn)

    return BackgroundResult(
        entity_name=entity_a,
        query=query_key,
        summary_text=summary,
        search_source=source,
    )


# ─────────────────────── DuckDuckGo ───────────────────────

def _search_duckduckgo(query: str) -> dict:
    """
    调用 DuckDuckGo Instant Answer API。
    失败时返回空 dict，不抛异常（保证调用方不中断）。
    """
    params = {
        "q": query,
        "format": "json",
        "no_html": "1",
        "no_redirect": "1",
        "skip_disambig": "1",
        "kl": "cn-zh",
    }
    try:
        resp = requests.get(_DDG_API, params=params, timeout=_DDG_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0 (entity-analyzer/1.0)"})
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        logger.warning("DuckDuckGo 搜索失败 (query=%s): %s", query[:40], exc)
    return {}


def _extract_ddg_summary(data: dict, entity_name: str) -> str:
    """
    从 DuckDuckGo JSON 提取有用的文字摘要。
    优先 Abstract，其次 RelatedTopics 中包含实体名称的条目。
    """
    if not data:
        return ""

    parts = []

    # 1. Abstract（通常来自 Wikipedia）
    abstract = (data.get("Abstract") or "").strip()
    if abstract and len(abstract) > 20:
        source_url = data.get("AbstractURL", "")
        parts.append(f"{abstract}（来源: {source_url}）" if source_url else abstract)

    # 2. Infobox 字段（公司类型、成立时间、母公司等）
    infobox = data.get("Infobox", {})
    if isinstance(infobox, dict):
        for item in infobox.get("content", []):
            label = item.get("label", "")
            value = item.get("value", "")
            if label and value and len(value) < 100:
                parts.append(f"{label}: {value}")

    # 3. RelatedTopics 中包含实体名的片段
    for topic in data.get("RelatedTopics", [])[:5]:
        text = topic.get("Text", "") or ""
        if entity_name[:4] in text and len(text) > 10:
            parts.append(text[:200])

    return " | ".join(parts)[:_SUMMARY_MAX_CHARS] if parts else ""


# ─────────────────────── LLM 知识查询 ───────────────────────

def _query_llm_knowledge(entity_name: str, llm_client) -> str:
    """让 LLM 凭训练数据介绍单个实体背景"""
    try:
        result = llm_client.chat(
            system_prompt=_LLM_BACKGROUND_SYSTEM,
            user_prompt=f"请介绍：{entity_name}",
            temperature=0.1,
            max_tokens=300,
        )
        return (result.get("content") or "").strip()
    except Exception as exc:
        logger.warning("LLM 知识查询失败 (%s): %s", entity_name, exc)
        return ""


def _query_llm_pair(entity_a: str, entity_b: str, llm_client) -> str:
    """让 LLM 判断两个实体之间的关系"""
    try:
        result = llm_client.chat(
            system_prompt=_LLM_PAIR_SYSTEM,
            user_prompt=f"请说明这两个实体之间是否存在已知的企业关系：\n实体A：{entity_a}\n实体B：{entity_b}",
            temperature=0.1,
            max_tokens=200,
        )
        return (result.get("content") or "").strip()
    except Exception as exc:
        logger.warning("LLM 关系查询失败 (%s / %s): %s", entity_a, entity_b, exc)
        return ""


# ─────────────────────── 缓存操作 ───────────────────────

def _normalize_key(name: str) -> str:
    return name.strip().lower()


def _get_cached(query_key: str, conn) -> Optional[dict]:
    """从 entity_search_cache 查询（未命中返回 None）"""
    try:
        row = conn.execute(
            "SELECT summary_text, raw_results, search_source FROM entity_search_cache WHERE query=?",
            (query_key,),
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("缓存读取失败: %s", exc)
        return None


def _save_cache(
    entity_name: str,
    query_key: str,
    source: str,
    raw_results: dict,
    summary_text: str,
    conn,
) -> None:
    """写入 entity_search_cache（若已存在则更新）"""
    try:
        conn.execute(
            """INSERT INTO entity_search_cache
               (id, entity_name, query, search_source, raw_results, summary_text)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(query) DO UPDATE SET
                 search_source=excluded.search_source,
                 raw_results=excluded.raw_results,
                 summary_text=excluded.summary_text,
                 created_at=datetime('now')""",
            (
                str(uuid.uuid4()),
                entity_name,
                query_key,
                source,
                json.dumps(raw_results, ensure_ascii=False),
                summary_text,
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("缓存写入失败: %s", exc)


def get_cache_stats(conn) -> dict:
    """返回缓存统计信息"""
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, MAX(created_at) AS latest FROM entity_search_cache"
        ).fetchone()
        return {"total": row["total"], "latest": row["latest"]}
    except Exception:
        return {"total": 0, "latest": None}
