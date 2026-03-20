"""一次性清理：将重复的同名实体合并到最早的那个（rowid 最小）"""
from app.models.db import get_connection
conn = get_connection()

# 找所有重复组
dups = conn.execute("""
    SELECT canonical_name, GROUP_CONCAT(id, ',') ids
    FROM entity GROUP BY canonical_name HAVING COUNT(*) > 1
""").fetchall()

merged_groups = 0
for d in dups:
    ids = d["ids"].split(",")
    # 取 rowid 最小的为主
    primary_row = conn.execute(
        "SELECT id FROM entity WHERE id IN ({}) ORDER BY rowid ASC LIMIT 1".format(
            ",".join("?" * len(ids))), ids).fetchone()
    primary_id = primary_row["id"]
    duplicates = [i for i in ids if i != primary_id]
    print(f"合并 '{d['canonical_name']}'：主={primary_id}  副={duplicates}")

    for did in duplicates:
        conn.execute("UPDATE fact_atom SET subject_entity_id=? WHERE subject_entity_id=?", (primary_id, did))
        conn.execute("UPDATE fact_atom SET object_entity_id=? WHERE object_entity_id=?", (primary_id, did))
        conn.execute("UPDATE OR IGNORE entity_alias SET entity_id=? WHERE entity_id=?", (primary_id, did))
        conn.execute("UPDATE OR IGNORE entity_relation SET from_entity_id=? WHERE from_entity_id=?", (primary_id, did))
        conn.execute("UPDATE OR IGNORE entity_relation SET to_entity_id=? WHERE to_entity_id=?", (primary_id, did))
        conn.execute("DELETE FROM entity_alias WHERE entity_id=?", (did,))
        conn.execute("DELETE FROM entity WHERE id=?", (did,))
    # 统一设为 GROUP（因为"中远佐敦"是集团/简称）
    conn.execute("UPDATE entity SET entity_type='GROUP' WHERE id=?", (primary_id,))
    merged_groups += 1

conn.commit()
print(f"\n完成：合并了 {merged_groups} 组重复实体")

# 验证
remaining = conn.execute("""
    SELECT canonical_name, COUNT(*) c FROM entity GROUP BY canonical_name HAVING c > 1
""").fetchall()
print("剩余重复：", remaining if remaining else "无")
conn.close()
