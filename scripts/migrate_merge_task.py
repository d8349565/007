"""迁移脚本：添加 entity_merge_task 表"""
import sys
sys.path.insert(0, '.')
from app.models.db import get_connection

conn = get_connection()
conn.executescript("""
CREATE TABLE IF NOT EXISTS entity_merge_task (
    id             TEXT PRIMARY KEY,
    primary_id     TEXT NOT NULL REFERENCES entity(id),
    secondary_id   TEXT NOT NULL REFERENCES entity(id),
    rule_score     REAL NOT NULL DEFAULT 0.0,
    rule_reason    TEXT,
    llm_verdict    TEXT,
    llm_confidence REAL,
    llm_reason     TEXT,
    llm_model      TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at    TEXT,
    UNIQUE(primary_id, secondary_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_merge_task_status
    ON entity_merge_task(status);
""")
conn.commit()
print("OK - entity_merge_task 表已创建")
conn.close()
