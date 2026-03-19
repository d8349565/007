"""
回填 fact_atom.subject_entity_id 和 object_entity_id：
将已通过事实的 subject_text / object_text 与 entity.canonical_name / entity_alias.alias_name 做匹配，
把匹配到的 entity_id 写回对应字段。

运行方式：
    python scripts/backfill_entity_links.py [--dry-run]
"""
import sys
import argparse
sys.path.insert(0, '.')
from app.models.db import get_connection

def normalize(text: str) -> str:
    """简单规范化：去空格、全转小写"""
    return text.strip().lower().replace(' ', '').replace('（', '(').replace('）', ')')

def run(dry_run: bool = False):
    conn = get_connection()

    # 1. 加载实体表（canonical_name + alias）
    entities = conn.execute(
        "SELECT id, canonical_name, normalized_name FROM entity"
    ).fetchall()
    aliases = conn.execute(
        "SELECT entity_id, alias_name FROM entity_alias"
    ).fetchall()

    # 构建 normalized -> entity_id 映射
    name_to_entity: dict[str, str] = {}
    for e in entities:
        n = normalize(e["canonical_name"])
        if n:
            name_to_entity[n] = e["id"]
        n2 = normalize(e["normalized_name"])
        if n2:
            name_to_entity[n2] = e["id"]
    for a in aliases:
        n = normalize(a["alias_name"])
        if n:
            name_to_entity[n] = a["entity_id"]

    print(f"实体/别名库大小: {len(name_to_entity)} 条")

    # 2a. 回填 subject_entity_id
    unlinked_subj = conn.execute(
        "SELECT id, subject_text FROM fact_atom "
        "WHERE review_status IN ('AUTO_PASS','HUMAN_PASS') "
        "AND subject_entity_id IS NULL AND subject_text IS NOT NULL"
    ).fetchall()
    print(f"待处理 subject 未链接: {len(unlinked_subj)}")
    matched_s, no_match_s = 0, []
    for row in unlinked_subj:
        entity_id = name_to_entity.get(normalize(row["subject_text"] or ""))
        if entity_id:
            matched_s += 1
            if not dry_run:
                conn.execute("UPDATE fact_atom SET subject_entity_id=? WHERE id=?", (entity_id, row["id"]))
        else:
            no_match_s.append(row["subject_text"])

    # 2b. 回填 object_entity_id（仅有 object_text 的）
    unlinked_obj = conn.execute(
        "SELECT id, object_text FROM fact_atom "
        "WHERE review_status IN ('AUTO_PASS','HUMAN_PASS') "
        "AND object_entity_id IS NULL AND object_text IS NOT NULL AND object_text != ''"
    ).fetchall()
    print(f"待处理 object 未链接:  {len(unlinked_obj)}")
    matched_o, no_match_o = 0, []
    for row in unlinked_obj:
        entity_id = name_to_entity.get(normalize(row["object_text"] or ""))
        if entity_id:
            matched_o += 1
            if not dry_run:
                conn.execute("UPDATE fact_atom SET object_entity_id=? WHERE id=?", (entity_id, row["id"]))
        else:
            no_match_o.append(row["object_text"])

    if not dry_run:
        conn.commit()
        print(f"\n✓ subject_entity_id 更新 {matched_s} 条")
        print(f"✓ object_entity_id  更新 {matched_o} 条")
    else:
        print(f"\n[DRY-RUN] subject 将更新 {matched_s} 条，object 将更新 {matched_o} 条")

    from collections import Counter
    if no_match_s:
        print(f"\nsubject 无匹配 ({len(set(no_match_s))} 种):")
        for name, cnt in Counter(no_match_s).most_common(10):
            print(f"  '{name}' => {cnt}条")
    if no_match_o:
        print(f"\nobject 无匹配 ({len(set(no_match_o))} 种):")
        for name, cnt in Counter(no_match_o).most_common(10):
            print(f"  '{name}' => {cnt}条")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填 fact_atom 实体 ID")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入数据库")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
