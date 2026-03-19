"""reviewer 审核阈值映射测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.reviewer import _map_verdict_to_status


def _make_cfg(force_types=None, force_preds=None, threshold=0.90):
    return {
        "review": {
            "auto_pass_confidence": threshold,
            "force_human_review_types": force_types or [],
            "force_human_review_predicates": force_preds or [],
        }
    }


# --- REJECT 始终映射为 REJECTED ---

def test_reject_always_rejected():
    cfg = _make_cfg()
    result = _map_verdict_to_status("REJECT", 0.98, {"fact_type": "FINANCIAL_METRIC", "qualifiers": {}}, cfg)
    assert result == "REJECTED"


# --- UNCERTAIN 始终映射为 HUMAN_REVIEW_REQUIRED ---

def test_uncertain_always_human_review():
    cfg = _make_cfg()
    result = _map_verdict_to_status("UNCERTAIN", 0.80, {"fact_type": "FINANCIAL_METRIC", "qualifiers": {}}, cfg)
    assert result == "HUMAN_REVIEW_REQUIRED"


# --- PASS + 高分 → AUTO_PASS ---

def test_pass_high_score_auto_pass():
    cfg = _make_cfg()
    result = _map_verdict_to_status("PASS", 0.95, {"fact_type": "FINANCIAL_METRIC", "qualifiers": {}}, cfg)
    assert result == "AUTO_PASS"


# --- PASS + 低分 → HUMAN_REVIEW_REQUIRED ---

def test_pass_low_score_human_review():
    cfg = _make_cfg()
    result = _map_verdict_to_status("PASS", 0.85, {"fact_type": "FINANCIAL_METRIC", "qualifiers": {}}, cfg)
    assert result == "HUMAN_REVIEW_REQUIRED"


# --- force_human_review_types 测试 ---

def test_force_human_review_type_overrides_pass():
    """COMPETITIVE_RANKING 在 force_human_review_types 中，即使高分也强制人工"""
    cfg = _make_cfg(force_types=["COMPETITIVE_RANKING"])
    result = _map_verdict_to_status("PASS", 0.98, {"fact_type": "COMPETITIVE_RANKING", "qualifiers": {}}, cfg)
    assert result == "HUMAN_REVIEW_REQUIRED"


def test_market_share_not_forced_after_config_change():
    """修改配置后，MARKET_SHARE 不再强制人工审核（当前配置只有 COMPETITIVE_RANKING）"""
    cfg = _make_cfg(force_types=["COMPETITIVE_RANKING"])  # MARKET_SHARE 已移除
    result = _map_verdict_to_status("PASS", 0.98, {"fact_type": "MARKET_SHARE", "qualifiers": {}}, cfg)
    assert result == "AUTO_PASS"


# --- force_human_review_predicates 测试 ---

def test_force_human_review_predicate():
    """qualifier 中包含 yoy 时强制人工审核"""
    cfg = _make_cfg(force_preds=["yoy"])
    result = _map_verdict_to_status("PASS", 0.98, {"fact_type": "FINANCIAL_METRIC", "qualifiers": {"yoy": "10%"}}, cfg)
    assert result == "HUMAN_REVIEW_REQUIRED"


def test_no_force_predicates_after_config_change():
    """清空 force_human_review_predicates 后，有 yoy 的也可以 AUTO_PASS"""
    cfg = _make_cfg(force_preds=[])  # 已清空
    result = _map_verdict_to_status("PASS", 0.98, {"fact_type": "FINANCIAL_METRIC", "qualifiers": {"yoy": "10%"}}, cfg)
    assert result == "AUTO_PASS"


# --- 边界条件 ---

def test_pass_exactly_at_threshold():
    cfg = _make_cfg(threshold=0.90)
    result = _map_verdict_to_status("PASS", 0.90, {"fact_type": "FINANCIAL_METRIC", "qualifiers": {}}, cfg)
    assert result == "AUTO_PASS"


def test_pass_just_below_threshold():
    cfg = _make_cfg(threshold=0.90)
    result = _map_verdict_to_status("PASS", 0.899, {"fact_type": "FINANCIAL_METRIC", "qualifiers": {}}, cfg)
    assert result == "HUMAN_REVIEW_REQUIRED"
