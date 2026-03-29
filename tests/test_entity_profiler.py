"""entity_profiler 单元测试"""

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# 测试环境隔离（必须在 import app 之前设置）
_test_db = os.path.join(tempfile.gettempdir(), "test_profiler.db")
os.environ["DATABASE_PATH_OVERRIDE"] = _test_db

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import get_connection, init_db
from app.services.entity_profiler import (
    build_entity_profile,
    get_entity_profile,
    build_all_profiles,
    _collect_relations,
    _collect_benchmarks,
    _collect_competitors,
)


@pytest.fixture(autouse=True)
def fresh_db():
    """每个测试用例前重建数据库"""
    if os.path.exists(_test_db):
        os.remove(_test_db)
    init_db(_test_db)
    yield
    if os.path.exists(_test_db):
        os.remove(_test_db)


def _insert_entity(conn, name, etype="企业", entity_id=None):
    eid = entity_id or str(uuid.uuid4())
    from app.services.entity_utils import normalize
    conn.execute(
        "INSERT INTO entity (id, canonical_name, normalized_name, entity_type) VALUES (?,?,?,?)",
        (eid, name, normalize(name), etype),
    )
    return eid


def _insert_alias(conn, entity_id, alias):
    conn.execute(
        "INSERT INTO entity_alias (id, entity_id, alias_name) VALUES (?,?,?)",
        (str(uuid.uuid4()), entity_id, alias),
    )


def _insert_relation(conn, from_id, to_id, rel_type, detail=None):
    conn.execute(
        "INSERT INTO entity_relation (id, from_entity_id, to_entity_id, relation_type, detail_json, created_at) VALUES (?,?,?,?,?,datetime('now'))",
        (str(uuid.uuid4()), from_id, to_id, rel_type, json.dumps(detail or {})),
    )


def _insert_fact(conn, entity_id, fact_type, predicate, value_num=None,
                 unit=None, currency=None, time_expr=None, object_text=None,
                 object_entity_id=None, status="自动通过"):
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    ev_id = str(uuid.uuid4())
    fact_id = str(uuid.uuid4())
    # 确保 source_document 和 document_chunk 存在
    conn.execute(
        "INSERT OR IGNORE INTO source_document (id, source_type, raw_text, content_hash, status) VALUES (?,?,?,?,?)",
        (doc_id, "测试", "test", str(uuid.uuid4()), "已完成"),
    )
    conn.execute(
        "INSERT INTO document_chunk (id, document_id, chunk_index, chunk_text, char_count) VALUES (?,?,0,?,4)",
        (chunk_id, doc_id, "test"),
    )
    conn.execute(
        "INSERT INTO evidence_span (id, document_id, chunk_id, evidence_text, fact_type, created_at) VALUES (?,?,?,?,?,datetime('now'))",
        (ev_id, doc_id, chunk_id, "test evidence", fact_type),
    )
    conn.execute(
        """INSERT INTO fact_atom
           (id, document_id, evidence_span_id, fact_type, subject_text, predicate,
            object_text, value_num, unit, currency, time_expr, review_status,
            subject_entity_id, object_entity_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        (fact_id, doc_id, ev_id, fact_type, "test", predicate,
         object_text, value_num, unit, currency, time_expr, status,
         entity_id, object_entity_id),
    )
    return fact_id, doc_id


# ──────────── 测试 build_entity_profile ────────────

class TestBuildEntityProfile:

    def test_basic_build(self):
        """基本构建：实体 + 别名 + 事实"""
        conn = get_connection()
        try:
            eid = _insert_entity(conn, "佐敦集团", "集团")
            _insert_alias(conn, eid, "Jotun")
            _insert_alias(conn, eid, "佐敦")
            _insert_fact(conn, eid, "FINANCIAL_METRIC", "销售额",
                         value_num=343.33, unit="亿挪威克朗", time_expr="2025年")
            conn.commit()
        finally:
            conn.close()

        profile = build_entity_profile(eid)

        assert profile["entity_id"] == eid
        assert profile["canonical_name"] == "佐敦集团"
        assert "Jotun" in profile["aliases"]
        assert "佐敦" in profile["aliases"]
        assert len(profile["benchmarks"]) == 1
        assert profile["benchmarks"][0]["value"] == 343.33
        assert profile["fact_count"] == 1

    def test_nonexistent_entity(self):
        """不存在的实体返回空 dict"""
        result = build_entity_profile("nonexistent-id")
        assert result == {}

    def test_entity_without_facts(self):
        """没有事实的实体也能构建 profile"""
        conn = get_connection()
        try:
            eid = _insert_entity(conn, "空实体")
            conn.commit()
        finally:
            conn.close()

        profile = build_entity_profile(eid)
        assert profile["entity_id"] == eid
        assert profile["fact_count"] == 0
        assert profile["benchmarks"] == []

    def test_relations_both_directions(self):
        """关系双向收集"""
        conn = get_connection()
        try:
            parent_id = _insert_entity(conn, "佐敦集团", "集团")
            child_id = _insert_entity(conn, "中远佐敦", "企业")
            _insert_relation(conn, parent_id, child_id, "子公司")
            conn.commit()
        finally:
            conn.close()

        profile = build_entity_profile(parent_id)
        assert len(profile["relations"]) == 1
        assert profile["relations"][0]["direction"] == "outgoing"
        assert profile["relations"][0]["target_name"] == "中远佐敦"

        # 子公司视角
        child_profile = build_entity_profile(child_id)
        assert len(child_profile["relations"]) == 1
        assert child_profile["relations"][0]["direction"] == "incoming"
        assert child_profile["relations"][0]["target_name"] == "佐敦集团"

    def test_benchmark_dedup(self):
        """同 (fact_type, predicate, time) 的基准数据去重"""
        conn = get_connection()
        try:
            eid = _insert_entity(conn, "测试公司")
            _insert_fact(conn, eid, "FINANCIAL_METRIC", "销售额",
                         value_num=100, unit="亿元", time_expr="2024年")
            _insert_fact(conn, eid, "FINANCIAL_METRIC", "销售额",
                         value_num=100.5, unit="亿元", time_expr="2024年")
            _insert_fact(conn, eid, "FINANCIAL_METRIC", "销售额",
                         value_num=80, unit="亿元", time_expr="2023年")
            conn.commit()
        finally:
            conn.close()

        profile = build_entity_profile(eid)
        # 两个不同时间的指标，同时间的去重
        assert len(profile["benchmarks"]) == 2

    def test_only_passed_facts(self):
        """只聚合已通过的事实"""
        conn = get_connection()
        try:
            eid = _insert_entity(conn, "测试公司B")
            _insert_fact(conn, eid, "FINANCIAL_METRIC", "营收",
                         value_num=50, status="自动通过")
            _insert_fact(conn, eid, "FINANCIAL_METRIC", "利润",
                         value_num=10, status="已拒绝")
            conn.commit()
        finally:
            conn.close()

        profile = build_entity_profile(eid)
        assert profile["fact_count"] == 1
        assert len(profile["benchmarks"]) == 1
        assert profile["benchmarks"][0]["metric"] == "营收"


# ──────────── 测试 _collect_competitors ────────────

class TestCollectCompetitors:

    def test_direct_competitors(self):
        """从 COMPETITIVE_RANKING 的 object_text 中提取竞品"""
        conn = get_connection()
        try:
            eid = _insert_entity(conn, "佐敦")
            comp_id = _insert_entity(conn, "海虹老人")
            _insert_fact(conn, eid, "COMPETITIVE_RANKING", "市场排名第一",
                         object_text="海虹老人", object_entity_id=comp_id)
            conn.commit()
            competitors = _collect_competitors(eid, conn)
        finally:
            conn.close()

        assert len(competitors) == 1
        assert competitors[0]["name"] == "海虹老人"
        assert competitors[0]["entity_id"] == comp_id

    def test_co_occurring_competitors(self):
        """同文档中 COMPETITIVE_RANKING 的其他主体作为竞品"""
        conn = get_connection()
        try:
            eid_a = _insert_entity(conn, "佐敦A")
            eid_b = _insert_entity(conn, "海虹B")

            doc_id = str(uuid.uuid4())
            chunk_id = str(uuid.uuid4())
            ev_id_a = str(uuid.uuid4())
            ev_id_b = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO source_document (id, source_type, raw_text, content_hash, status) VALUES (?,?,?,?,?)",
                (doc_id, "测试", "test", str(uuid.uuid4()), "已完成"),
            )
            conn.execute(
                "INSERT INTO document_chunk (id, document_id, chunk_index, chunk_text, char_count) VALUES (?,?,0,?,4)",
                (chunk_id, doc_id, "test"),
            )
            conn.execute(
                "INSERT INTO evidence_span (id, document_id, chunk_id, evidence_text, fact_type, created_at) VALUES (?,?,?,?,?,datetime('now'))",
                (ev_id_a, doc_id, chunk_id, "ev1", "COMPETITIVE_RANKING"),
            )
            conn.execute(
                "INSERT INTO evidence_span (id, document_id, chunk_id, evidence_text, fact_type, created_at) VALUES (?,?,?,?,?,datetime('now'))",
                (ev_id_b, doc_id, chunk_id, "ev2", "COMPETITIVE_RANKING"),
            )
            conn.execute(
                """INSERT INTO fact_atom (id, document_id, evidence_span_id, fact_type,
                   subject_text, predicate, review_status, subject_entity_id,
                   created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
                (str(uuid.uuid4()), doc_id, ev_id_a, "COMPETITIVE_RANKING",
                 "佐敦A", "排名", "自动通过", eid_a),
            )
            conn.execute(
                """INSERT INTO fact_atom (id, document_id, evidence_span_id, fact_type,
                   subject_text, predicate, review_status, subject_entity_id,
                   created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
                (str(uuid.uuid4()), doc_id, ev_id_b, "COMPETITIVE_RANKING",
                 "海虹B", "排名", "自动通过", eid_b),
            )
            conn.commit()
            competitors = _collect_competitors(eid_a, conn)
        finally:
            conn.close()

        assert len(competitors) == 1
        assert competitors[0]["name"] == "海虹B"


# ──────────── 测试 get_entity_profile ────────────

class TestGetEntityProfile:

    def test_auto_build_on_miss(self):
        """profile 不存在时自动构建"""
        conn = get_connection()
        try:
            eid = _insert_entity(conn, "自动构建测试")
            _insert_alias(conn, eid, "AutoBuild")
            conn.commit()
        finally:
            conn.close()

        profile = get_entity_profile(eid)
        assert profile["entity_id"] == eid
        assert "AutoBuild" in profile["aliases"]

    def test_return_cached_profile(self):
        """已存在的 profile 直接返回"""
        conn = get_connection()
        try:
            eid = _insert_entity(conn, "缓存测试")
            conn.commit()
        finally:
            conn.close()

        # 先构建
        build_entity_profile(eid)
        # 再获取（应从 DB 读取）
        profile = get_entity_profile(eid)
        assert profile["entity_id"] == eid
        assert "last_built_at" in profile


# ──────────── 测试 build_all_profiles ────────────

class TestBuildAllProfiles:

    def test_batch_build(self):
        """批量构建"""
        conn = get_connection()
        try:
            eid1 = _insert_entity(conn, "公司A")
            eid2 = _insert_entity(conn, "公司B")
            eid3 = _insert_entity(conn, "公司C")
            _insert_fact(conn, eid1, "FINANCIAL_METRIC", "营收", value_num=100)
            _insert_fact(conn, eid2, "SALES_VOLUME", "销量", value_num=50)
            # eid3 没有事实
            conn.commit()
        finally:
            conn.close()

        stats = build_all_profiles(min_facts=1)
        assert stats["total"] == 2
        assert stats["built"] == 2
        assert stats["failed"] == 0

    def test_min_facts_filter(self):
        """min_facts 过滤"""
        conn = get_connection()
        try:
            eid1 = _insert_entity(conn, "多事实公司")
            eid2 = _insert_entity(conn, "少事实公司")
            _insert_fact(conn, eid1, "FINANCIAL_METRIC", "营收", value_num=100)
            _insert_fact(conn, eid1, "SALES_VOLUME", "销量", value_num=50)
            _insert_fact(conn, eid1, "CAPACITY", "产能", value_num=20)
            _insert_fact(conn, eid2, "FINANCIAL_METRIC", "营收", value_num=10)
            conn.commit()
        finally:
            conn.close()

        stats = build_all_profiles(min_facts=3)
        assert stats["total"] == 1  # 只有 eid1 有 3+ 事实
        assert stats["built"] == 1


# ──────────── 测试 profile 持久化 ────────────

class TestProfilePersistence:

    def test_upsert_idempotent(self):
        """重复构建不会报错，会更新"""
        conn = get_connection()
        try:
            eid = _insert_entity(conn, "幂等测试")
            _insert_fact(conn, eid, "FINANCIAL_METRIC", "营收", value_num=100)
            conn.commit()
        finally:
            conn.close()

        # 构建两次
        p1 = build_entity_profile(eid)
        p2 = build_entity_profile(eid)

        assert p1["entity_id"] == p2["entity_id"]
        assert p1["fact_count"] == p2["fact_count"]

        # DB 中只有一条
        conn = get_connection()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM entity_profile WHERE entity_id=?", (eid,)
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()

    def test_profile_json_roundtrip(self):
        """profile 中的 JSON 字段正确序列化/反序列化"""
        conn = get_connection()
        try:
            eid = _insert_entity(conn, "JSON测试公司")
            _insert_alias(conn, eid, "JsonTest")
            _insert_fact(conn, eid, "FINANCIAL_METRIC", "销售额",
                         value_num=42.36, unit="亿港元", currency="HKD",
                         time_expr="2024年")
            conn.commit()
        finally:
            conn.close()

        build_entity_profile(eid)
        profile = get_entity_profile(eid)

        assert isinstance(profile["aliases"], list)
        assert "JsonTest" in profile["aliases"]
        assert isinstance(profile["benchmarks"], list)
        assert profile["benchmarks"][0]["value"] == 42.36
        assert profile["benchmarks"][0]["currency"] == "HKD"
