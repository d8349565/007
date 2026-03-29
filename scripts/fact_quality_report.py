"""事实原子数据质量报告生成脚本"""

import json
from collections import Counter

from app.models.db import get_connection


def generate_quality_report() -> dict:
    """生成事实原子数据质量报告"""
    conn = get_connection()

    # 1. 基础统计
    basic_stats = _get_basic_stats(conn)

    # 2. 状态分布
    status_dist = _get_status_distribution(conn)

    # 3. 置信度分析
    confidence_analysis = _get_confidence_analysis(conn)

    # 4. fact_type 分布
    fact_type_dist = _get_fact_type_distribution(conn)

    # 5. 数据质量问题检测
    quality_issues = _detect_quality_issues(conn)

    # 6. 实体关联情况
    entity_linking = _get_entity_linking_stats(conn)

    conn.close()

    return {
        "basic_stats": basic_stats,
        "status_distribution": status_dist,
        "confidence_analysis": confidence_analysis,
        "fact_type_distribution": fact_type_dist,
        "quality_issues": quality_issues,
        "entity_linking": entity_linking,
    }


def _get_basic_stats(conn) -> dict:
    row = conn.execute("SELECT COUNT(*) as total FROM fact_atom").fetchone()
    return {"total_count": row["total"]}


def _get_status_distribution(conn) -> dict:
    rows = conn.execute("""
        SELECT review_status, COUNT(*) as cnt
        FROM fact_atom
        GROUP BY review_status
        ORDER BY cnt DESC
    """).fetchall()
    return {r["review_status"]: r["cnt"] for r in rows}


def _get_confidence_analysis(conn) -> dict:
    conf_stats = conn.execute("""
        SELECT
            AVG(confidence_score) as avg_conf,
            MIN(confidence_score) as min_conf,
            MAX(confidence_score) as max_conf,
            COUNT(CASE WHEN confidence_score < 0.5 THEN 1 END) as low_conf_count,
            COUNT(CASE WHEN confidence_score >= 0.9 THEN 1 END) as high_conf_count
        FROM fact_atom
    """).fetchone()

    # 置信度分布桶
    buckets = conn.execute("""
        SELECT
            CASE
                WHEN confidence_score < 0.3 THEN '0-0.3'
                WHEN confidence_score < 0.5 THEN '0.3-0.5'
                WHEN confidence_score < 0.7 THEN '0.5-0.7'
                WHEN confidence_score < 0.9 THEN '0.7-0.9'
                ELSE '0.9-1.0'
            END as bucket,
            COUNT(*) as cnt
        FROM fact_atom
        GROUP BY bucket
        ORDER BY bucket
    """).fetchall()

    return {
        "average": round(conf_stats["avg_conf"] or 0, 3),
        "minimum": round(conf_stats["min_conf"] or 0, 3),
        "maximum": round(conf_stats["max_conf"] or 0, 3),
        "low_confidence_count": conf_stats["low_conf_count"],
        "high_confidence_count": conf_stats["high_conf_count"],
        "distribution": {r["bucket"]: r["cnt"] for r in buckets},
    }


def _get_fact_type_distribution(conn) -> dict:
    rows = conn.execute("""
        SELECT fact_type, COUNT(*) as cnt
        FROM fact_atom
        GROUP BY fact_type
        ORDER BY cnt DESC
    """).fetchall()
    return {r["fact_type"]: r["cnt"] for r in rows}


def _detect_quality_issues(conn) -> dict:
    issues = {}

    # 1. subject 为空
    empty_subject = conn.execute(
        "SELECT COUNT(*) as cnt FROM fact_atom WHERE subject_text IS NULL OR subject_text = ''"
    ).fetchone()["cnt"]
    issues["empty_subject"] = empty_subject

    # 2. predicate 为空
    empty_predicate = conn.execute(
        "SELECT COUNT(*) as cnt FROM fact_atom WHERE predicate IS NULL OR predicate = ''"
    ).fetchone()["cnt"]
    issues["empty_predicate"] = empty_predicate

    # 3. object 和 value_num 都为空（对于需要有值的fact_type）
    no_value = conn.execute("""
        SELECT COUNT(*) as cnt FROM fact_atom
        WHERE (object_text IS NULL OR object_text = '')
          AND (value_num IS NULL)
          AND fact_type NOT IN ('YES_NO', 'QUALITY_ASSESSMENT')
    """).fetchone()["cnt"]
    issues["no_value_provided"] = no_value

    # 4. 时间表达式异常（未来时间）
    future_time = conn.execute("""
        SELECT COUNT(*) as cnt FROM fact_atom
        WHERE time_expr IS NOT NULL
          AND time_expr != ''
          AND time_expr > date('now')
    """).fetchone()["cnt"]
    issues["future_time_expr"] = future_time

    # 5. 无效的置信度
    invalid_conf = conn.execute("""
        SELECT COUNT(*) as cnt FROM fact_atom
        WHERE confidence_score IS NULL
           OR confidence_score < 0
           OR confidence_score > 1
    """).fetchone()["cnt"]
    issues["invalid_confidence"] = invalid_conf

    # 6. 重复的事实原子（同document_id + fact_type + predicate + subject_text）
    duplicates = conn.execute("""
        SELECT COUNT(*) - COUNT(DISTINCT document_id || '|' || fact_type || '|' || predicate || '|' || subject_text) as dup_count
        FROM fact_atom
    """).fetchone()["dup_count"]
    issues["potential_duplicates"] = duplicates

    # 7. qualifier_json 格式错误
    bad_qualifier = 0
    rows = conn.execute("SELECT id, qualifier_json FROM fact_atom WHERE qualifier_json IS NOT NULL").fetchall()
    for row in rows:
        try:
            if row["qualifier_json"]:
                json.loads(row["qualifier_json"])
        except Exception:
            bad_qualifier += 1
    issues["bad_qualifier_json"] = bad_qualifier

    return issues


def _get_entity_linking_stats(conn) -> dict:
    # 主体实体关联情况
    subject_linked = conn.execute(
        "SELECT COUNT(*) as cnt FROM fact_atom WHERE subject_entity_id IS NOT NULL"
    ).fetchone()["cnt"]

    # 客体实体关联情况
    object_linked = conn.execute(
        "SELECT COUNT(*) as cnt FROM fact_atom WHERE object_entity_id IS NOT NULL"
    ).fetchone()["cnt"]

    total = conn.execute("SELECT COUNT(*) FROM fact_atom").fetchone()[0]

    return {
        "subject_entity_linked": subject_linked,
        "subject_entity_link_rate": round(subject_linked / total * 100, 1) if total > 0 else 0,
        "object_entity_linked": object_linked,
        "object_entity_link_rate": round(object_linked / total * 100, 1) if total > 0 else 0,
    }


def print_report(report: dict) -> None:
    """格式化打印报告"""
    print("=" * 60)
    print("          事实原子数据质量报告")
    print("=" * 60)

    # 基础统计
    print("\n## 基础统计")
    print(f"  总记录数: {report['basic_stats']['total_count']}")

    # 状态分布
    print("\n## 审核状态分布")
    for status, cnt in report['status_distribution'].items():
        pct = cnt / report['basic_stats']['total_count'] * 100
        print(f"  {status}: {cnt} ({pct:.1f}%)")

    # 置信度分析
    print("\n## 置信度分析")
    ca = report['confidence_analysis']
    print(f"  平均置信度: {ca['average']}")
    print(f"  最低置信度: {ca['minimum']}")
    print(f"  最高置信度: {ca['maximum']}")
    print(f"  低置信度(<0.5)记录数: {ca['low_confidence_count']}")
    print(f"  高置信度(>=0.9)记录数: {ca['high_confidence_count']}")
    print("  置信度分布:")
    for bucket, cnt in ca['distribution'].items():
        print(f"    {bucket}: {cnt}")

    # fact_type 分布
    print("\n## Fact Type 分布")
    for ft, cnt in report['fact_type_distribution'].items():
        print(f"  {ft}: {cnt}")

    # 质量问题
    print("\n## 数据质量问题")
    qi = report['quality_issues']
    total = report['basic_stats']['total_count']

    def pct_str(cnt):
        return f"{cnt} ({cnt/total*100:.1f}%)" if total > 0 else "0"

    print(f"  空 subject: {pct_str(qi['empty_subject'])}")
    print(f"  空 predicate: {pct_str(qi['empty_predicate'])}")
    print(f"  无数值(object和value_num都为空): {pct_str(qi['no_value_provided'])}")
    print(f"  未来时间表达式: {pct_str(qi['future_time_expr'])}")
    print(f"  无效置信度: {pct_str(qi['invalid_confidence'])}")
    print(f"  潜在重复: {pct_str(qi['potential_duplicates'])}")
    print(f"  损坏的qualifier_json: {pct_str(qi['bad_qualifier_json'])}")

    # 实体关联
    print("\n## 实体关联情况")
    el = report['entity_linking']
    print(f"  主体实体已关联: {el['subject_entity_linked']} ({el['subject_entity_link_rate']}%)")
    print(f"  客体实体已关联: {el['object_entity_linked']} ({el['object_entity_link_rate']}%)")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    report = generate_quality_report()
    print_report(report)