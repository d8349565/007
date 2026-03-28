"""事实原子内容保真度审核脚本 — 取出事实原子与证据原文的对照数据。

用法：
  python .github/skills/fact-fidelity-audit/scripts/audit_facts.py
  python .github/skills/fact-fidelity-audit/scripts/audit_facts.py --doc-id <DOC_ID>
  python .github/skills/fact-fidelity-audit/scripts/audit_facts.py --fact-type FINANCIAL_METRIC --limit 20
  python .github/skills/fact-fidelity-audit/scripts/audit_facts.py --status 自动通过 --limit 30
"""

import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))

from app.models.db import get_connection
from app.logger import get_logger

logger = get_logger(__name__)


def fetch_facts_for_audit(
    doc_id: str = "",
    fact_type: str = "",
    status: str = "自动通过",
    limit: int = 50,
) -> list[dict]:
    """获取待审核事实原子及其证据原文。"""
    conditions = []
    params = []

    if doc_id:
        conditions.append("f.document_id = ?")
        params.append(doc_id)
    if fact_type:
        conditions.append("f.fact_type = ?")
        params.append(fact_type)
    if status:
        conditions.append("f.review_status = ?")
        params.append(status)

    where = " AND ".join(conditions) if conditions else "1=1"

    sql = f"""
        SELECT f.id, f.fact_type, f.subject_text, f.predicate, f.object_text,
               f.value_num, f.value_text, f.unit, f.currency,
               f.time_expr, f.location_text, f.qualifier_json,
               f.confidence_score, f.review_status,
               es.evidence_text,
               sd.title AS document_title
        FROM fact_atom f
        JOIN evidence_span es ON f.evidence_span_id = es.id
        JOIN source_document sd ON f.document_id = sd.id
        WHERE {where}
        ORDER BY f.created_at DESC
        LIMIT ?
    """
    params.append(limit)

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def format_fact_vs_evidence(fact: dict) -> str:
    """格式化单条事实原子与证据原文的对照输出。"""
    quals = {}
    if fact.get("qualifier_json"):
        try:
            quals = json.loads(fact["qualifier_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    # 拼读还原
    parts = [fact.get("subject_text") or "?"]
    parts.append(fact.get("predicate") or "?")
    if fact.get("object_text"):
        parts.append(fact["object_text"])
    if fact.get("value_text"):
        parts.append(fact["value_text"])
    elif fact.get("value_num") is not None:
        v = str(fact["value_num"])
        if fact.get("unit"):
            v += " " + fact["unit"]
        parts.append(v)
    if fact.get("time_expr"):
        parts.append(f"({fact['time_expr']})")
    reconstruction = " ".join(parts)

    lines = [
        f"{'─' * 60}",
        f"  事实ID: {fact['id'][:12]}…  类型: {fact['fact_type']}  置信: {fact.get('confidence_score', '?')}",
        f"  文档:   {fact.get('document_title', '?')}",
        f"",
        f"  📄 证据原文:",
        f"     {fact.get('evidence_text', '(无)')}",
        f"",
        f"  🔬 事实原子:",
        f"     主体:   {fact.get('subject_text', '—')}",
        f"     谓词:   {fact.get('predicate', '—')}",
        f"     客体:   {fact.get('object_text', '—')}",
        f"     数值:   {fact.get('value_num', '—')} / {fact.get('value_text', '—')}",
        f"     单位:   {fact.get('unit', '—')}  货币: {fact.get('currency', '—')}",
        f"     时间:   {fact.get('time_expr', '—')}",
        f"     地点:   {fact.get('location_text', '—')}",
    ]
    if quals:
        lines.append(f"     限定词: {json.dumps(quals, ensure_ascii=False)}")
    lines.append(f"")
    lines.append(f"  🔁 还原拼读: {reconstruction}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="事实原子内容保真度审核 — 对照数据提取")
    parser.add_argument("--doc-id", default="", help="限定文档 ID")
    parser.add_argument("--fact-type", default="", help="限定事实类型（如 FINANCIAL_METRIC）")
    parser.add_argument("--status", default="", help="审核状态过滤（默认: 空=全部状态）")
    parser.add_argument("--limit", type=int, default=50, help="最多取多少条（默认: 50）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式（便于程序处理）")
    args = parser.parse_args()

    facts = fetch_facts_for_audit(
        doc_id=args.doc_id,
        fact_type=args.fact_type,
        status=args.status,
        limit=args.limit,
    )

    if not facts:
        print("未找到符合条件的事实原子。")
        return

    if args.json:
        print(json.dumps(facts, ensure_ascii=False, indent=2))
    else:
        print(f"\n事实原子内容保真度审核 — 共 {len(facts)} 条待审\n")
        print(f"  过滤条件: status={args.status or '全部'} fact_type={args.fact_type or '全部'} doc_id={args.doc_id or '全部'}")
        for i, f in enumerate(facts, 1):
            print(f"\n  [{i}/{len(facts)}]")
            print(format_fact_vs_evidence(f))
        print(f"\n{'─' * 60}")
        print(f"共 {len(facts)} 条，请逐条比对证据原文与事实原子各字段。")


if __name__ == "__main__":
    main()
