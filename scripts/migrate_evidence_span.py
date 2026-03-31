"""一次性迁移脚本：为 evidence_span 表添加 created_at 列和去重唯一索引"""
import sqlite3

conn = sqlite3.connect("data/mvp.db")

# 1. 检查 created_at 列是否存在
cols = [row[1] for row in conn.execute("PRAGMA table_info(evidence_span)").fetchall()]
print("当前列:", cols)

if "created_at" not in cols:
    conn.execute("ALTER TABLE evidence_span ADD COLUMN created_at TEXT")
    # 回填已有记录
    conn.execute("UPDATE evidence_span SET created_at = datetime('now') WHERE created_at IS NULL")
    print("已添加 created_at 列并回填")
else:
    print("created_at 列已存在，跳过")

# 2. 检查去重索引是否存在
indexes = [row[0] for row in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='evidence_span'"
).fetchall()]
print("当前索引:", indexes)

if "idx_evidence_span_dedup" not in indexes:
    conn.execute(
        "CREATE UNIQUE INDEX idx_evidence_span_dedup ON evidence_span(document_id, fact_type, evidence_text)"
    )
    print("已创建去重唯一索引 idx_evidence_span_dedup")
else:
    print("去重索引已存在，跳过")

conn.commit()
conn.close()
print("迁移完成")
