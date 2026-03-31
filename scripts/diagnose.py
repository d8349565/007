"""数据质量综合诊断工具 — 供数据副驾驶(阿粒)使用

用法:
  python scripts/diagnose.py                   # 全面诊断
  python scripts/diagnose.py --check entity     # 仅检查实体
  python scripts/diagnose.py --check fact       # 仅检查事实
  python scripts/diagnose.py --check link       # 仅检查链接
  python scripts/diagnose.py --check relation   # 仅检查关系
  python scripts/diagnose.py --check duplicate  # 仅检查重复
  python scripts/diagnose.py --json             # JSON 格式输出
"""

import sys
import json
import argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import get_connection
from app.logger import get_logger

logger = get_logger(__name__)


def check_entity_types(conn) -> dict:
    """检查实体类型分布与 UNKNOWN 实体"""
    dist = conn.execute(
        "SELECT entity_type, COUNT(*) cnt FROM entity GROUP BY entity_type ORDER BY cnt DESC"
    ).fetchall()
    total = sum(r["cnt"] for r in dist)
    unknown = next((r["cnt"] for r in dist if r["entity_type"] == "UNKNOWN"), 0)

    # UNKNOWN 实体中有事实关联的
    unknown_with_facts = conn.execute("""
        SELECT e.canonical_name, COUNT(DISTINCT f.id) fc
        FROM entity e
        JOIN fact_atom f ON (f.subject_entity_id = e.id OR f.object_entity_id = e.id)
        WHERE e.entity_type = 'UNKNOWN'
        GROUP BY e.id ORDER BY fc DESC LIMIT 15
    """).fetchall()

    return {
        "check": "entity_types",
        "total_entities": total,
        "distribution": {r["entity_type"]: r["cnt"] for r in dist},
        "unknown_count": unknown,
        "unknown_pct": round(unknown * 100 / total, 1) if total else 0,
        "top_unknown_with_facts": [
            {"name": r["canonical_name"], "fact_count": r["fc"]}
            for r in unknown_with_facts
        ],
        "severity": "high" if unknown > total * 0.2 else ("medium" if unknown > total * 0.1 else "low"),
    }


def check_fact_quality(conn) -> dict:
    """检查事实原子质量"""
    status_dist = conn.execute(
        "SELECT review_status, COUNT(*) cnt FROM fact_atom GROUP BY review_status ORDER BY cnt DESC"
    ).fetchall()
    total = sum(r["cnt"] for r in status_dist)

    type_dist = conn.execute(
        "SELECT fact_type, COUNT(*) cnt FROM fact_atom GROUP BY fact_type ORDER BY cnt DESC"
    ).fetchall()

    # 缺失关键字段
    missing_subject = conn.execute(
        "SELECT COUNT(*) FROM fact_atom WHERE subject_text IS NULL OR subject_text = ''"
    ).fetchone()[0]
    missing_predicate = conn.execute(
        "SELECT COUNT(*) FROM fact_atom WHERE predicate IS NULL OR predicate = ''"
    ).fetchone()[0]
    missing_time = conn.execute(
        "SELECT COUNT(*) FROM fact_atom WHERE (time_expr IS NULL OR time_expr = '') AND review_status IN ('自动通过','人工通过')"
    ).fetchone()[0]

    # 无证据文本
    no_evidence = conn.execute("""
        SELECT COUNT(*) FROM fact_atom f
        LEFT JOIN evidence_span es ON f.evidence_span_id = es.id
        WHERE (es.evidence_text IS NULL OR es.evidence_text = '')
        AND f.review_status IN ('自动通过','人工通过')
    """).fetchone()[0]

    issues = []
    if missing_subject > 0:
        issues.append(f"subject 为空: {missing_subject} 条")
    if missing_predicate > 0:
        issues.append(f"predicate 为空: {missing_predicate} 条")
    if missing_time > 5:
        issues.append(f"已通过事实缺少时间: {missing_time} 条")
    if no_evidence > 0:
        issues.append(f"已通过事实无证据原文: {no_evidence} 条")

    return {
        "check": "fact_quality",
        "total_facts": total,
        "status_distribution": {r["review_status"]: r["cnt"] for r in status_dist},
        "type_distribution": {r["fact_type"]: r["cnt"] for r in type_dist},
        "missing_subject": missing_subject,
        "missing_predicate": missing_predicate,
        "missing_time_approved": missing_time,
        "no_evidence_approved": no_evidence,
        "issues": issues,
        "severity": "high" if issues else "low",
    }


def check_entity_links(conn) -> dict:
    """检查实体链接完整性"""
    approved_total = conn.execute(
        "SELECT COUNT(*) FROM fact_atom WHERE review_status IN ('自动通过','人工通过')"
    ).fetchone()[0]

    no_subject_link = conn.execute(
        "SELECT COUNT(*) FROM fact_atom WHERE subject_entity_id IS NULL AND review_status IN ('自动通过','人工通过')"
    ).fetchone()[0]

    no_object_link = conn.execute("""
        SELECT COUNT(*) FROM fact_atom
        WHERE object_entity_id IS NULL AND object_text IS NOT NULL AND object_text != ''
        AND review_status IN ('自动通过','人工通过')
    """).fetchone()[0]

    # 无别名实体
    no_alias = conn.execute("""
        SELECT COUNT(*) FROM entity e
        WHERE NOT EXISTS (SELECT 1 FROM entity_alias WHERE entity_id = e.id)
    """).fetchone()[0]
    total_entities = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]

    # 孤立实体（无任何事实关联）
    orphan = conn.execute("""
        SELECT COUNT(*) FROM entity e
        WHERE NOT EXISTS (
            SELECT 1 FROM fact_atom f
            WHERE f.subject_entity_id = e.id OR f.object_entity_id = e.id
        )
    """).fetchone()[0]

    issues = []
    if no_subject_link > 0:
        issues.append(f"已通过事实未链接 subject: {no_subject_link} 条")
    if no_object_link > 0:
        issues.append(f"已通过事实未链接 object: {no_object_link} 条")
    if orphan > total_entities * 0.3:
        issues.append(f"孤立实体 (无事实关联): {orphan}/{total_entities}")

    return {
        "check": "entity_links",
        "approved_facts": approved_total,
        "no_subject_link": no_subject_link,
        "no_object_link": no_object_link,
        "orphan_entities": orphan,
        "no_alias_entities": no_alias,
        "total_entities": total_entities,
        "issues": issues,
        "severity": "high" if no_subject_link > 10 else ("medium" if issues else "low"),
    }


def check_relations(conn) -> dict:
    """检查实体关系数据"""
    relation_count = conn.execute("SELECT COUNT(*) FROM entity_relation").fetchone()[0]
    suggestion_count = conn.execute("SELECT COUNT(*) FROM entity_relation_suggestion").fetchone()[0]
    pending_suggestions = conn.execute(
        "SELECT COUNT(*) FROM entity_relation_suggestion WHERE status = '待处理'"
    ).fetchone()[0]
    merge_pending = conn.execute(
        "SELECT COUNT(*) FROM entity_merge_task WHERE status = '待处理'"
    ).fetchone()[0]

    # 关系类型分布
    rel_types = conn.execute(
        "SELECT relation_type, COUNT(*) cnt FROM entity_relation GROUP BY relation_type ORDER BY cnt DESC"
    ).fetchall()

    issues = []
    if relation_count == 0:
        issues.append("entity_relation 表为空，层级视图无法展示")
    if pending_suggestions > 0:
        issues.append(f"待处理关系建议: {pending_suggestions} 条")
    if merge_pending > 0:
        issues.append(f"待处理合并任务: {merge_pending} 条")

    return {
        "check": "relations",
        "confirmed_relations": relation_count,
        "relation_types": {r["relation_type"]: r["cnt"] for r in rel_types},
        "total_suggestions": suggestion_count,
        "pending_suggestions": pending_suggestions,
        "pending_merges": merge_pending,
        "issues": issues,
        "severity": "high" if relation_count == 0 else ("medium" if issues else "low"),
    }


def check_duplicates(conn) -> dict:
    """检查可能的重复实体"""
    # 规范化名称相同但 entity_type 不同的记录
    same_name_diff_type = conn.execute("""
        SELECT canonical_name, GROUP_CONCAT(entity_type) types, COUNT(*) cnt
        FROM entity GROUP BY canonical_name HAVING cnt > 1
    """).fetchall()

    # 包含关系的疑似重复（A 包含 B 或 B 包含 A）
    contains_pairs = conn.execute("""
        SELECT a.canonical_name name_a, b.canonical_name name_b,
               a.entity_type type_a, b.entity_type type_b
        FROM entity a, entity b
        WHERE a.id < b.id
          AND LENGTH(a.canonical_name) >= 2 AND LENGTH(b.canonical_name) >= 2
          AND (a.canonical_name LIKE '%' || b.canonical_name || '%'
               OR b.canonical_name LIKE '%' || a.canonical_name || '%')
          AND a.entity_type NOT IN ('REGION','COUNTRY')
          AND b.entity_type NOT IN ('REGION','COUNTRY')
        LIMIT 20
    """).fetchall()

    return {
        "check": "duplicates",
        "same_name_diff_type": [
            {"name": r["canonical_name"], "types": r["types"], "count": r["cnt"]}
            for r in same_name_diff_type
        ],
        "contains_pairs": [
            {"a": r["name_a"], "b": r["name_b"], "type_a": r["type_a"], "type_b": r["type_b"]}
            for r in contains_pairs
        ],
        "severity": "medium" if same_name_diff_type or len(contains_pairs) > 5 else "low",
    }


def run_diagnosis(checks: list[str] | None = None) -> dict:
    """运行诊断并返回结果"""
    conn = get_connection()
    results = {}
    try:
        check_funcs = {
            "entity": check_entity_types,
            "fact": check_fact_quality,
            "link": check_entity_links,
            "relation": check_relations,
            "duplicate": check_duplicates,
        }
        targets = checks if checks else list(check_funcs.keys())
        for name in targets:
            if name in check_funcs:
                results[name] = check_funcs[name](conn)
    finally:
        conn.close()

    # 总体健康度
    severities = [r.get("severity", "low") for r in results.values()]
    if "high" in severities:
        results["overall"] = "需要关注"
    elif "medium" in severities:
        results["overall"] = "基本正常"
    else:
        results["overall"] = "健康"

    return results


def print_report(results: dict):
    """人类可读格式输出"""
    overall = results.pop("overall", "未知")
    severity_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}

    print(f"\n{'='*60}")
    print(f"  数据质量诊断报告  |  总体状态: {overall}")
    print(f"{'='*60}")

    for name, data in results.items():
        sev = data.get("severity", "low")
        icon = severity_icon.get(sev, "⚪")
        check_name = data.get("check", name)
        print(f"\n{icon} [{check_name}]")

        # 打印关键数字
        for key, val in data.items():
            if key in ("check", "severity", "issues", "top_unknown_with_facts",
                       "same_name_diff_type", "contains_pairs"):
                continue
            if isinstance(val, dict):
                print(f"  {key}:")
                for k, v in val.items():
                    print(f"    {k}: {v}")
            else:
                print(f"  {key}: {val}")

        # 打印问题
        issues = data.get("issues", [])
        if issues:
            print("  ⚠ 问题:")
            for issue in issues:
                print(f"    - {issue}")

        # 特殊列表
        if "top_unknown_with_facts" in data and data["top_unknown_with_facts"]:
            print("  UNKNOWN 实体 (有事实关联):")
            for item in data["top_unknown_with_facts"][:8]:
                print(f"    {item['name']}: {item['fact_count']} facts")

        if "same_name_diff_type" in data and data["same_name_diff_type"]:
            print("  同名不同类型:")
            for item in data["same_name_diff_type"][:5]:
                print(f"    {item['name']}: {item['types']}")

        if "contains_pairs" in data and data["contains_pairs"]:
            print("  疑似重复 (包含关系):")
            for item in data["contains_pairs"][:8]:
                print(f"    {item['a']} ({item['type_a']}) <-> {item['b']} ({item['type_b']})")

    print(f"\n{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="数据质量综合诊断")
    parser.add_argument("--check", choices=["entity", "fact", "link", "relation", "duplicate"],
                        help="仅运行指定检查项")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    checks = [args.check] if args.check else None
    results = run_diagnosis(checks)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_report(results)


if __name__ == "__main__":
    main()
