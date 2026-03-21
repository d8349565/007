-- ============================================================
-- 资讯颗粒化收集系统 — 数据库 Schema
-- ============================================================

-- 原始文档表
CREATE TABLE IF NOT EXISTS source_document (
    id           TEXT PRIMARY KEY,
    source_type  TEXT NOT NULL,           -- 'file' | 'url' | 'paste'
    source_name  TEXT,                    -- 文件名或来源域名
    title        TEXT,
    author       TEXT,
    url          TEXT,
    publish_time TEXT,                    -- 文章发布时间（原文）
    raw_text     TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,    -- SHA256 去重键
    status       TEXT NOT NULL DEFAULT 'ACTIVE',
    crawl_time   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_source_document_status
    ON source_document(status);

-- 文本块表（切分后）
CREATE TABLE IF NOT EXISTS document_chunk (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES source_document(id),
    chunk_index  INTEGER NOT NULL,
    chunk_text   TEXT NOT NULL,
    char_count   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_document_chunk_document
    ON document_chunk(document_id);

-- 句子表（可选，按句切分时使用）
CREATE TABLE IF NOT EXISTS document_sentence (
    id              TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES source_document(id),
    chunk_id        TEXT REFERENCES document_chunk(id),
    sentence_index  INTEGER NOT NULL,
    sentence_text   TEXT NOT NULL,
    char_start      INTEGER,
    char_end        INTEGER
);

CREATE INDEX IF NOT EXISTS idx_document_sentence_doc
    ON document_sentence(document_id, sentence_index);

-- 证据片段表（Agent 1 输出）
CREATE TABLE IF NOT EXISTS evidence_span (
    id                  TEXT PRIMARY KEY,
    document_id         TEXT NOT NULL REFERENCES source_document(id),
    chunk_id            TEXT NOT NULL REFERENCES document_chunk(id),
    evidence_text       TEXT NOT NULL,
    fact_type           TEXT NOT NULL,
    priority            TEXT NOT NULL DEFAULT 'medium',
    extraction_task_id  TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_evidence_span_chunk
    ON evidence_span(chunk_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_span_dedup
    ON evidence_span(document_id, fact_type, evidence_text);

-- 事实原子表（Agent 2 输出 + Agent 3 审核结果）
CREATE TABLE IF NOT EXISTS fact_atom (
    id                  TEXT PRIMARY KEY,
    document_id         TEXT NOT NULL REFERENCES source_document(id),
    evidence_span_id    TEXT NOT NULL REFERENCES evidence_span(id),
    fact_type           TEXT NOT NULL,
    subject_text        TEXT,
    predicate           TEXT NOT NULL,
    object_text         TEXT,
    value_num           REAL,
    value_text          TEXT,
    unit                TEXT,
    currency            TEXT,
    time_expr           TEXT,
    location_text       TEXT,
    qualifier_json      TEXT DEFAULT '{}',
    confidence_score    REAL DEFAULT 0.0,
    extraction_model    TEXT,
    extraction_version  TEXT,
    review_status       TEXT NOT NULL DEFAULT 'PENDING',
    -- PENDING | AUTO_PASS | HUMAN_REVIEW_REQUIRED | HUMAN_PASS | REJECTED | UNCERTAIN
    review_note         TEXT,
    subject_entity_id   TEXT REFERENCES entity(id),
    object_entity_id    TEXT REFERENCES entity(id),
    location_entity_id  TEXT REFERENCES entity(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fact_atom_document
    ON fact_atom(document_id);
CREATE INDEX IF NOT EXISTS idx_fact_atom_review_status
    ON fact_atom(review_status);
CREATE INDEX IF NOT EXISTS idx_fact_atom_fact_type
    ON fact_atom(fact_type);

-- 标准实体表
CREATE TABLE IF NOT EXISTS entity (
    id               TEXT PRIMARY KEY,
    canonical_name   TEXT NOT NULL,
    normalized_name  TEXT NOT NULL,
    entity_type      TEXT NOT NULL DEFAULT 'UNKNOWN',
    -- COMPANY | GROUP | PROJECT | REGION | COUNTRY | UNKNOWN
    UNIQUE(canonical_name, entity_type)
);

CREATE INDEX IF NOT EXISTS idx_entity_canonical
    ON entity(canonical_name);

-- 实体别名表
CREATE TABLE IF NOT EXISTS entity_alias (
    id          TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL REFERENCES entity(id),
    alias_name  TEXT NOT NULL UNIQUE
);

-- 实体关系表（股权、母子公司、合资、品牌归属等）
CREATE TABLE IF NOT EXISTS entity_relation (
    id               TEXT PRIMARY KEY,
    from_entity_id   TEXT NOT NULL REFERENCES entity(id),
    to_entity_id     TEXT NOT NULL REFERENCES entity(id),
    relation_type    TEXT NOT NULL,
    -- SUBSIDIARY(子公司) | SHAREHOLDER(股东) | JV(合资) | BRAND(品牌归属) | PARTNER(合作方) | INVESTS_IN(投资/持有项目)
    detail_json      TEXT DEFAULT '{}',  -- 扩展信息，如 {"share_pct": 42.53}
    source           TEXT NOT NULL DEFAULT 'manual',  -- manual | auto_extracted
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(from_entity_id, to_entity_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_entity_relation_from
    ON entity_relation(from_entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_relation_to
    ON entity_relation(to_entity_id);

-- 审核操作日志
CREATE TABLE IF NOT EXISTS review_log (
    id            TEXT PRIMARY KEY,
    target_type   TEXT NOT NULL DEFAULT 'fact_atom',
    target_id     TEXT NOT NULL,
    old_status    TEXT,
    new_status    TEXT NOT NULL,
    reviewer      TEXT NOT NULL DEFAULT 'system',
    review_action TEXT,
    review_note   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_review_log_target
    ON review_log(target_id);

-- 抽取任务追踪（三个 Agent 的调用记录）
CREATE TABLE IF NOT EXISTS extraction_task (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL REFERENCES source_document(id),
    chunk_id      TEXT,
    task_type     TEXT NOT NULL,   -- 'evidence_finder' | 'fact_extractor' | 'reviewer'
    status        TEXT NOT NULL DEFAULT 'running',
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at   TEXT,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    model_name    TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_extraction_task_document
    ON extraction_task(document_id);
CREATE INDEX IF NOT EXISTS idx_extraction_task_status
    ON extraction_task(status);

-- 实体合并任务表（规则候选 + LLM 分析 + 人工审核）
CREATE TABLE IF NOT EXISTS entity_merge_task (
    id             TEXT PRIMARY KEY,
    primary_id     TEXT NOT NULL REFERENCES entity(id),
    secondary_id   TEXT NOT NULL REFERENCES entity(id),
    rule_score     REAL NOT NULL DEFAULT 0.0,  -- 规则相似度 (0~1)
    rule_reason    TEXT,                        -- 规则初步理由
    llm_verdict    TEXT,   -- 'merge' | 'keep' | 'uncertain' | NULL(未分析)
    llm_confidence REAL,   -- LLM 置信度 0~1
    llm_reason     TEXT,   -- LLM 分析理由
    llm_model      TEXT,   -- 调用的模型名
    status         TEXT NOT NULL DEFAULT 'pending',
    -- pending(待审核) | approved(已批准待执行) | rejected(已拒绝)
    -- executed(已执行合并) | skipped(无需 LLM，规则确定)
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at    TEXT,
    UNIQUE(primary_id, secondary_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_merge_task_status
    ON entity_merge_task(status);
