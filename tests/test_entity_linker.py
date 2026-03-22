"""entity_linker 测试 —— 自动发现、创建、链接实体"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import init_db, get_connection
from app.services.entity_linker import (
    link_entity,
    add_entity,
    add_alias,
    batch_link_fact_atoms,
    infer_entity_type,
    _auto_discover_entities,
)


def _setup_db():
    """初始化数据库（清空旧数据，确保隔离）"""
    conn = get_connection()
    try:
        # 清空所有相关表数据
        for table in ("fact_atom", "evidence_span", "entity_alias", "entity",
                       "review_log", "extraction_task", "document_chunk", "source_document"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
    init_db()


def _insert_fact_atom(conn, fact_id, subject_text, object_text, fact_type="FINANCIAL_METRIC"):
    """辅助：插入一条 fact_atom 测试记录"""
    import uuid
    doc_id = str(uuid.uuid4())
    ev_id = str(uuid.uuid4())
    # 确保依赖记录存在
    conn.execute(
        "INSERT OR IGNORE INTO source_document (id, source_type, title, raw_text, content_hash) VALUES (?, 'paste', '测试', '测试内容', ?)",
        (doc_id, str(uuid.uuid4())),
    )
    conn.execute(
        "INSERT OR IGNORE INTO evidence_span (id, document_id, evidence_text) VALUES (?, ?, '测试证据')",
        (ev_id, doc_id),
    )
    conn.execute(
        """INSERT INTO fact_atom (id, document_id, evidence_span_id, fact_type,
           subject_text, predicate, object_text)
           VALUES (?, ?, ?, ?, ?, '测试谓词', ?)""",
        (fact_id, doc_id, ev_id, fact_type, subject_text, object_text),
    )
    conn.commit()


# --- 测试实体类型推断 ---

def test_infer_company():
    assert infer_entity_type("立邦涂料")[0] == "COMPANY"
    assert infer_entity_type("中涂化工")[0] == "COMPANY"
    assert infer_entity_type("PPG工业集团")[0] == "COMPANY"


def test_infer_group():
    assert infer_entity_type("前十强企业")[0] == "GROUP"
    assert infer_entity_type("外资品牌")[0] == "GROUP"


def test_infer_unknown():
    assert infer_entity_type("年产值")[0] == "UNKNOWN"
    assert infer_entity_type("GDP")[0] == "UNKNOWN"


# --- 测试精确匹配 ---

def test_link_entity_exact_match():
    _setup_db()
    eid = add_entity("立邦涂料", "COMPANY")
    result = link_entity("立邦涂料")
    assert result["matched"] is True
    assert result["entity_id"] == eid
    assert result["canonical_name"] == "立邦涂料"


def test_link_entity_alias_match():
    _setup_db()
    eid = add_entity("PPG工业集团", "COMPANY")
    add_alias(eid, "PPG")
    result = link_entity("PPG")
    assert result["matched"] is True
    assert result["canonical_name"] == "PPG工业集团"


def test_link_entity_no_match():
    _setup_db()
    result = link_entity("不存在的公司")
    assert result["matched"] is False
    assert result["canonical_name"] == "不存在的公司"


def test_link_entity_empty():
    result = link_entity("")
    assert result["matched"] is False
    result = link_entity(None)
    assert result["matched"] is False


# --- 测试自动发现 ---

def test_auto_discover_creates_entities():
    _setup_db()
    conn = get_connection()
    try:
        _insert_fact_atom(conn, "f1", "立邦涂料", "船舶涂料", "FINANCIAL_METRIC")
        _insert_fact_atom(conn, "f2", "中涂化工", None, "COMPETITIVE_RANKING")

        rows = conn.execute("SELECT id, subject_text, object_text, fact_type FROM fact_atom").fetchall()
        created = _auto_discover_entities(rows, conn)

        assert created >= 2  # 至少创建了立邦涂料和中涂化工

        # 验证实体已在数据库中
        ent = conn.execute("SELECT * FROM entity WHERE canonical_name='立邦涂料'").fetchone()
        assert ent is not None
        assert ent["entity_type"] == "COMPANY"

        ent2 = conn.execute("SELECT * FROM entity WHERE canonical_name='中涂化工'").fetchone()
        assert ent2 is not None
        assert ent2["entity_type"] == "COMPANY"
    finally:
        conn.close()


def test_auto_discover_dedup():
    """相同 subject_text 只创建一次"""
    _setup_db()
    conn = get_connection()
    try:
        _insert_fact_atom(conn, "dedup_f1", "立邦涂料", None, "FINANCIAL_METRIC")
        _insert_fact_atom(conn, "dedup_f2", "立邦涂料", None, "MARKET_SHARE")

        rows = conn.execute("SELECT id, subject_text, object_text, fact_type FROM fact_atom").fetchall()
        created = _auto_discover_entities(rows, conn)

        assert created == 1  # 去重后只创建一次
        count = conn.execute("SELECT COUNT(*) as c FROM entity WHERE canonical_name='立邦涂料'").fetchone()
        assert count[0] == 1
    finally:
        conn.close()


# --- 测试完整链接流程 ---

def test_batch_link_auto_creates_and_links():
    """测试 batch_link_fact_atoms 自动发现+创建+链接"""
    _setup_db()
    conn = get_connection()
    try:
        _insert_fact_atom(conn, "link_f1", "立邦涂料", "船舶涂料", "FINANCIAL_METRIC")

        # 链接前 subject_entity_id 应为空
        row = conn.execute("SELECT subject_entity_id FROM fact_atom WHERE id='link_f1'").fetchone()
        assert row["subject_entity_id"] is None
    finally:
        conn.close()

    stats = batch_link_fact_atoms(["link_f1"])

    assert stats["created"] >= 1  # 自动创建了实体
    assert stats["matched"] >= 1  # 至少链接了 subject

    conn = get_connection()
    try:
        row = conn.execute("SELECT subject_entity_id FROM fact_atom WHERE id='link_f1'").fetchone()
        assert row["subject_entity_id"] is not None  # 链接成功
    finally:
        conn.close()
