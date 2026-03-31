"""独立提取功能测试：从佐敦文章中提取事实原子，验证质量。

用法:
    python scripts/test_extraction_standalone.py                    # 提取+验证
    python scripts/test_extraction_standalone.py --skip-llm         # 仅测试 normalize
    python scripts/test_extraction_standalone.py --article-id XXX   # 指定文章ID测试
"""

import argparse
import json
import re
import sys
import copy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_config
from app.logger import get_logger

logger = get_logger(__name__)

# ============================================================
# 从 full_extractor.py 拉出的核心函数（可独立修改和测试）
# ============================================================

_FIELD_NAMES = [
    "fact_type", "subject", "predicate", "object", "value_num",
    "value_text", "unit", "currency", "time_expr", "location",
    "qualifiers", "confidence", "evidence_text",
]


def _list_to_dict(record: list) -> dict | None:
    if not isinstance(record, list) or len(record) < 13:
        return None
    result = {}
    for i, field_name in enumerate(_FIELD_NAMES):
        result[field_name] = record[i]
    return result


def _validate_record(rec: dict, cfg: dict) -> dict | None:
    fact_type = rec.get("fact_type", "")
    predicate = rec.get("predicate")
    if not predicate:
        return None
    subject = rec.get("subject")
    if not subject or not str(subject).strip():
        return None
    subject = str(subject).strip()

    valid_types = cfg.get("fact_types", [])
    if valid_types and fact_type not in valid_types:
        return None

    qualifiers = rec.get("qualifiers", {})
    if not isinstance(qualifiers, dict):
        qualifiers = {}

    confidence = rec.get("confidence")
    if confidence is None:
        confidence = 0.0
    elif isinstance(confidence, bool):
        confidence = 0.0
    elif not isinstance(confidence, (int, float)):
        if isinstance(confidence, dict):
            for k in ("score", "value", "confidence"):
                if k in confidence and isinstance(confidence[k], (int, float)):
                    confidence = float(confidence[k])
                    break
            else:
                confidence = 0.0
        else:
            confidence = 0.0
    rec["confidence"] = float(confidence)

    evidence_text = rec.get("evidence_text", "")
    if not isinstance(evidence_text, str):
        evidence_text = str(evidence_text) if evidence_text else ""
    rec["evidence_text"] = evidence_text

    rec["fact_type"] = fact_type
    rec["subject"] = subject
    rec["qualifiers"] = qualifiers
    return rec


# ============================================================
# 增强版 _normalize_record — 在此文件中迭代优化
# ============================================================

# 原有正则
_RE_YEAR_PREFIX = re.compile(r"^(\d{4}年(?:\d{1,2}月)?)")
_RE_SCOPE_PREFIX = re.compile(r"^(全球|全国|中国|国内|中国市场)")
_RE_MISSING_VERB = re.compile(
    r"^(.+(?:产能|收入|销售额|营收|利润|产值|市场规模|市场份额|"
    r"募集资金净额|营业收入|销售量|产量|消费量))$"
)

# === 新增正则 ===

# P1: "在中国/在青岛/在张家港/在全球" 前缀 → 剥离到 location
_RE_LOCATION_PREFIX = re.compile(
    r"^在(中国|全球|全国|国内|"
    r"[\u4e00-\u9fff]{2,8}(?:省|市|区|县|基地|工厂|园区))"
)

# P1b: "在X建厂/建设" — 国家/地区名不带行政后缀
_RE_LOC_VERB_PREFIX = re.compile(
    r"^在([\u4e00-\u9fff]{2,8})(建厂|建设|投产|设厂|投资|扩产)"
)

# P2: 设施/工厂名前缀 → 剥离到 qualifiers.report_scope
# 如 "青岛工厂产能达到" → "产能达到"
_RE_FACILITY_PREFIX = re.compile(
    r"^([\u4e00-\u9fff]{2,10}(?:工厂|基地|厂区|园区|公司|分公司|"
    r"生产基地|新工厂|旧工厂|总部))"
    r"(?=(?:产能|产量|销售|生产效率|一次合格率|年产|设计产能|规划|"
    r"预计|已形成|为集团|年产值))"
)

# P3: 阶段前缀 → qualifiers.phase
# 如 "一期工程年产能增加" → "年产能增加"
_RE_PHASE_PREFIX = re.compile(
    r"^((?:一|二|三|四|两|1|2|3|4)期(?:工程)?)"
    r"(?=(?:年产|产能|已?建成|计划开工|计划投产|投产|完工|项目))"
)

# P4: 产品类型前缀 (非产能场景) → qualifiers.product_type
# 如 "新造船涂料销售量为" → "销售量为"
# 如 "维修保养涂料销售量为" → "销售量为"
_RE_PRODUCT_PREFIX = re.compile(
    r"^([\u4e00-\u9fff]{2,12}(?:涂料|涂层|油漆|树脂|颜料|材料|产品))"
    r"(产销量|产销|销售量|销量|产量|消费量|出货量|销售额|销售收入|营业收入|营收|收入)"
)

# P5: "全厂/集团全球" 范围前缀
_RE_BROAD_SCOPE_PREFIX = re.compile(
    r"^(全厂|集团全球|全球|国内外|全国)"
    r"(?=(?:产能|销售|营收|收入|产量|销量))"
)

# P6: 项目名嵌入谓词 → 剥离到 qualifiers.project_name
# 如 "张家港高性能涂料新工厂及液体涂料研发中心项目建设" → "投资建设"
_RE_PROJECT_IN_PRED = re.compile(
    r"^(?:投建|计划投资建设|投资建设|拟建|计划建设|实施|扩建|签约落地)?"
    r"([\u4e00-\u9fff\w\uff08\uff09\(\)]{5,60}"
    r"(?:项目|工程|工厂|研发中心|基地|园区)"
    r"(?:[\uff08\(][^\uff09\)]*[\uff09\)])?)"  # 可选括号后缀如（一期）
    r"(计划建设|建设|签约|开工|投产|通过验收|达产|试生产|建成)?$"
)

# P7: "连续X年" → 剥离到 qualifiers
_RE_CONSECUTIVE_YEARS = re.compile(r"^(连续\d+年)")

# P9: 排名类谓词中嵌入范围+产品描述
# 如 "位居中国船舶涂料产销量第一" → "位居第一" + qualifiers
_RE_RANKING_EMBEDDED = re.compile(
    r"^(位居|排名|位列|名列|跻身|荣获|入选|是)"
    r"([\u4e00-\u9fff]+(?:涂料|市场|行业|领域)[\u4e00-\u9fff]*?)"
    r"的?"
    r"(第[一二三四五六七八九十\d]+[位名]?|[\u4e00-\u9fff]*领导者|[\u4e00-\u9fff]*前列|[\u4e00-\u9fff]*冠军)"
    r"$"
)

# P10: 谓词前有游离字符(如"区")
_RE_JUNK_PREFIX = re.compile(r"^([^\u4e00-\u9fffa-zA-Z\d]+)")

# P8: 产品+产能（增强版，覆盖更多结尾动词）
_RE_PRODUCT_CAPACITY = re.compile(
    r"^([\u4e00-\u9fff]{2,15}(?:涂料|涂层|油漆|树脂|颜料|材料|产品))"
    r"(产能(?:为|达到|增加|增加至|扩大至)?)"
    r"$"
)


def _normalize_record(rec: dict) -> dict:
    """
    增强版：自动修正常见字段错位问题。
    在验证后、写入 DB 前执行。
    """
    predicate = rec.get("predicate", "")
    qualifiers = rec.get("qualifiers", {})
    if not isinstance(qualifiers, dict):
        qualifiers = {}
    changed = False
    original_predicate = predicate

    # === 1. 剥离年份前缀 ===
    m = _RE_YEAR_PREFIX.match(predicate)
    if m:
        year_part = m.group(1)
        new_pred = predicate[len(year_part):]
        if new_pred:
            if not rec.get("time_expr"):
                rec["time_expr"] = year_part
            predicate = new_pred
            changed = True

    # === 2. 剥离"在X"地域前缀 ===
    m = _RE_LOCATION_PREFIX.match(predicate)
    if m:
        loc_part = m.group(1)
        new_pred = predicate[len("在") + len(loc_part):]
        if new_pred:
            if not rec.get("location"):
                scope_map = {"全国": "中国", "国内": "中国", "中国": "中国", "全球": "全球"}
                rec["location"] = scope_map.get(loc_part, loc_part)
            predicate = new_pred
            changed = True

    # === 2b. "在X建厂/建设" — 国家/地区名无行政后缀 ===
    if not changed or predicate == original_predicate:
        m = _RE_LOC_VERB_PREFIX.match(predicate)
        if m:
            loc_part = m.group(1)
            verb_part = m.group(2)
            if not rec.get("location"):
                rec["location"] = loc_part
            predicate = verb_part
            changed = True

    # === 3. 剥离基础地域前缀（全球/全国/中国/国内/中国市场）===
    m = _RE_SCOPE_PREFIX.match(predicate)
    if m:
        scope_part = m.group(1)
        new_pred = predicate[len(scope_part):]
        if new_pred:
            if not rec.get("location"):
                scope_map = {"全国": "中国", "国内": "中国", "中国市场": "中国"}
                rec["location"] = scope_map.get(scope_part, scope_part)
            predicate = new_pred
            changed = True

    # === 4. 剥离广域范围前缀（全厂/集团全球等）===
    m = _RE_BROAD_SCOPE_PREFIX.match(predicate)
    if m:
        scope_part = m.group(1)
        new_pred = predicate[len(scope_part):]
        if new_pred:
            if not qualifiers.get("report_scope"):
                qualifiers["report_scope"] = scope_part
                rec["qualifiers"] = qualifiers
            predicate = new_pred
            changed = True

    # === 5. 剥离设施/工厂名前缀 ===
    m = _RE_FACILITY_PREFIX.match(predicate)
    if m:
        facility_part = m.group(1)
        new_pred = predicate[len(facility_part):]
        if new_pred:
            if not qualifiers.get("report_scope"):
                qualifiers["report_scope"] = facility_part
                rec["qualifiers"] = qualifiers
            predicate = new_pred
            changed = True

    # === 6. 剥离阶段前缀（一期/二期/三期/两期）===
    m = _RE_PHASE_PREFIX.match(predicate)
    if m:
        phase_part = m.group(1)
        new_pred = predicate[len(phase_part):]
        if new_pred:
            if not qualifiers.get("phase"):
                qualifiers["phase"] = phase_part
                rec["qualifiers"] = qualifiers
            predicate = new_pred
            changed = True

    # === 7. 排名类谓词：剥离嵌入的范围+产品描述（优先于产品前缀）===
    m = _RE_RANKING_EMBEDDED.match(predicate)
    if m:
        verb_part = m.group(1)  # 如 "位居"
        scope_part = m.group(2)  # 如 "中国船舶涂料产销量"
        rank_part = m.group(3)  # 如 "第一"
        if not qualifiers.get("ranking_scope"):
            qualifiers["ranking_scope"] = scope_part
            rec["qualifiers"] = qualifiers
        predicate = verb_part + rank_part
        changed = True

    # === 8. 剥离产品类型前缀（非产能场景）===
    m = _RE_PRODUCT_PREFIX.match(predicate)
    if m:
        product_part = m.group(1)
        metric_part = m.group(2)
        # 保留指标部分作为谓词
        if not qualifiers.get("product_type"):
            qualifiers["product_type"] = product_part
            rec["qualifiers"] = qualifiers
        # 检查是否有"为"结尾
        remaining = predicate[len(product_part):]
        predicate = remaining
        changed = True

    # === 9. 产品+产能 增强版 ===
    m = _RE_PRODUCT_CAPACITY.match(predicate)
    if m:
        product_part = m.group(1)
        capacity_verb = m.group(2)
        if not qualifiers.get("product_type"):
            qualifiers["product_type"] = product_part
            rec["qualifiers"] = qualifiers
        predicate = capacity_verb
        changed = True

    # === 10. 连续X年 → qualifiers ===
    m = _RE_CONSECUTIVE_YEARS.match(predicate)
    if m:
        consec_part = m.group(1)
        new_pred = predicate[len(consec_part):]
        if new_pred:
            qualifiers["duration"] = consec_part
            rec["qualifiers"] = qualifiers
            predicate = new_pred
            changed = True

    # === 10.5 游离字符清理 ===
    # 如 "区2024年销售额接近" — 前导单个无意义汉字+年份
    if predicate and re.match(r'^[\u4e00-\u9fff]\d{4}年', predicate):
        # 单字+年份开头：剥离首字，后续年份由步骤1处理
        predicate = predicate[1:]
        changed = True
        # 再次尝试剥离年份前缀
        m = _RE_YEAR_PREFIX.match(predicate)
        if m:
            year_part = m.group(1)
            new_pred = predicate[len(year_part):]
            if new_pred:
                if not rec.get("time_expr"):
                    rec["time_expr"] = year_part
                predicate = new_pred

    # === 10. 项目名嵌入谓词 ===
    m = _RE_PROJECT_IN_PRED.match(predicate)
    if m:
        project_name = m.group(1)
        action_verb = m.group(2) or "建设"
        if not qualifiers.get("project_name"):
            qualifiers["project_name"] = project_name
            rec["qualifiers"] = qualifiers
        # 尝试从原 predicate 提取前导动词
        pre_project = predicate[:predicate.find(project_name)].rstrip()
        if pre_project:
            # 避免双重动词：如果前导部分已包含 action_verb 则不再追加
            if action_verb in pre_project:
                predicate = pre_project
            else:
                predicate = pre_project + action_verb
        else:
            if "计划" in original_predicate and "计划" not in action_verb:
                predicate = "计划" + action_verb
            else:
                predicate = action_verb
        changed = True

    # === 11. 缺少动词结尾 → 补"为" ===
    if _RE_MISSING_VERB.match(predicate) and not predicate.endswith("为"):
        predicate = predicate + "为"
        changed = True

    # === 12. 原有产品+产能为 拆分 ===
    if predicate.endswith("产能为") and len(predicate) > len("产能为"):
        product_part = predicate[:-len("产能为")]
        product_part = re.sub(r"项目$", "", product_part).strip()
        if product_part:
            if not qualifiers.get("product_type"):
                qualifiers["product_type"] = product_part
                rec["qualifiers"] = qualifiers
            predicate = "产能为"
            changed = True

    if changed:
        rec["predicate"] = predicate
        logger.debug("predicate 修正: '%s' → '%s'", original_predicate, predicate)

    return rec


# ============================================================
# 质量检查函数
# ============================================================

# predicate 不应包含的模式
_BAD_PATTERNS = [
    (re.compile(r"^\d{4}年"), "包含年份前缀"),
    (re.compile(r"^在[\u4e00-\u9fff]+"), "包含'在X'地域前缀"),
    (re.compile(r"^(全球|全国|中国|国内|中国市场)"), "包含地域前缀"),
    (re.compile(r"^[\u4e00-\u9fff]{2,}(?:工厂|基地|园区|厂区)(?=(?:产能|产量|销售|生产效率|合格率|年产|预计|规划|已形成|年产值))"), "包含设施名"),
    (re.compile(r"^(?:一|二|三|两)\s*期"), "包含阶段前缀"),
    (re.compile(r"项目$"), "以'项目'结尾"),
    (re.compile(r"连续\d+年"), "包含'连续X年'"),
    (re.compile(r"[\u4e00-\u9fff]+涂料(?:销售|产|消费)"), "包含产品类型前缀"),
]


def check_fact_quality(fact: dict) -> list[str]:
    """检查单条事实质量，返回问题列表"""
    issues = []
    pred = fact.get("predicate", "")
    
    # 检查 predicate 是否包含不该有的模式
    for pattern, desc in _BAD_PATTERNS:
        if pattern.search(pred):
            issues.append(f"predicate '{pred}': {desc}")

    # 检查必填字段
    if not fact.get("subject"):
        issues.append("缺少 subject")
    if not pred:
        issues.append("缺少 predicate")
    
    # 检查 subject 不应是代词
    subject = fact.get("subject", "")
    if subject in ("该公司", "其", "本公司", "公司"):
        issues.append(f"subject 是代词: '{subject}'")

    # 检查 FINANCIAL_METRIC 应有 currency
    if fact.get("fact_type") == "FINANCIAL_METRIC":
        if fact.get("value_num") is not None and not fact.get("currency"):
            # 从 unit 推断
            unit = fact.get("unit", "") or ""
            if any(w in unit for w in ("元", "美元", "港元", "日元")):
                issues.append("有货币单位但缺少 currency 字段")

    # 检查 COMPETITIVE_RANKING 应有 qualifiers
    if fact.get("fact_type") == "COMPETITIVE_RANKING":
        quals = fact.get("qualifiers", {})
        if not isinstance(quals, dict):
            quals = {}
        if not any(quals.get(k) for k in ("ranking_name", "ranking_scope", "segment")):
            issues.append("COMPETITIVE_RANKING 缺少上下文限定词")

    # 检查 value_num/value_text 一致性
    if fact.get("value_num") is not None and not fact.get("value_text"):
        issues.append("有 value_num 但缺少 value_text")

    return issues


# ============================================================
# 测试驱动
# ============================================================

def get_jotun_articles():
    """从数据库获取佐敦相关文章"""
    from app.models.db import get_connection
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT sd.id, sd.title, sd.raw_text, sd.publish_time
            FROM source_document sd
            WHERE sd.raw_text LIKE '%佐敦%' OR sd.title LIKE '%佐敦%'
            ORDER BY LENGTH(sd.raw_text) DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_existing_jotun_facts():
    """获取数据库中现有的佐敦相关事实"""
    from app.models.db import get_connection
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT f.id, f.fact_type, f.subject_text, f.predicate,
                   f.object_text, f.value_num, f.value_text, f.unit,
                   f.currency, f.time_expr, f.location_text,
                   f.qualifier_json, f.confidence_score, f.review_status,
                   f.review_note, f.evidence_span_id,
                   es.evidence_text
            FROM fact_atom f
            LEFT JOIN evidence_span es ON f.evidence_span_id = es.id
            WHERE (f.subject_text LIKE '%佐敦%' OR f.object_text LIKE '%佐敦%')
            ORDER BY f.review_status, f.fact_type
        """).fetchall()
        facts = []
        for r in rows:
            f = dict(r)
            f["qualifiers"] = json.loads(f.get("qualifier_json") or "{}")
            facts.append(f)
        return facts
    finally:
        conn.close()


def test_normalize_existing_facts():
    """测试：对现有佐敦事实做 normalize，看有多少能修复"""
    print("=" * 80)
    print("测试1: 对现有佐敦事实应用增强版 _normalize_record")
    print("=" * 80)

    facts = get_existing_jotun_facts()
    print(f"共 {len(facts)} 条佐敦相关事实\n")

    fixed_count = 0
    still_bad_count = 0
    already_good_count = 0

    rejected_facts = [f for f in facts if f["review_status"] in ("已拒绝", "人工拒绝")]
    uncertain_facts = [f for f in facts if f["review_status"] == "待人工审核"]
    passed_facts = [f for f in facts if f["review_status"] == "自动通过"]

    for category, cat_facts, label in [
        ("已拒绝/人工拒绝", rejected_facts, "REJECTED"),
        ("待人工审核", uncertain_facts, "UNCERTAIN"),
        ("自动通过", passed_facts, "AUTO_PASS"),
    ]:
        print(f"\n--- {category} ({len(cat_facts)}条) ---")
        cat_fixed = 0
        cat_bad = 0

        for f in cat_facts:
            # 构建 normalize 的输入
            rec = {
                "fact_type": f["fact_type"],
                "subject": f["subject_text"],
                "predicate": f["predicate"],
                "object": f.get("object_text"),
                "value_num": f.get("value_num"),
                "value_text": f.get("value_text"),
                "unit": f.get("unit"),
                "currency": f.get("currency"),
                "time_expr": f.get("time_expr"),
                "location": f.get("location_text"),
                "qualifiers": copy.deepcopy(f.get("qualifiers", {})),
                "confidence": f.get("confidence_score", 0),
                "evidence_text": f.get("evidence_text", ""),
            }

            # 检查修正前的问题
            issues_before = check_fact_quality(rec)

            # 应用 normalize
            rec_copy = copy.deepcopy(rec)
            normalized = _normalize_record(rec_copy)

            # 检查修正后的问题
            issues_after = check_fact_quality(normalized)

            if issues_before and not issues_after:
                cat_fixed += 1
                fixed_count += 1
                print(f"  [已修复] {f['subject_text']} | {f['predicate']}")
                print(f"    修正前: {issues_before}")
                print(f"    修正后 predicate: '{normalized['predicate']}'")
                if normalized.get("location") != f.get("location_text"):
                    print(f"    location: '{f.get('location_text')}' → '{normalized.get('location')}'")
                quals_diff = {k: v for k, v in normalized.get("qualifiers", {}).items()
                              if k not in f.get("qualifiers", {})}
                if quals_diff:
                    print(f"    新增 qualifiers: {quals_diff}")
            elif issues_before and issues_after:
                cat_bad += 1
                still_bad_count += 1
                if label != "AUTO_PASS":  # 通过的不打印详情
                    print(f"  [仍有问题] {f['subject_text']} | {f['predicate']}")
                    print(f"    修正前: {issues_before}")
                    print(f"    修正后: {issues_after}")
                    print(f"    修正后 predicate: '{normalized['predicate']}'")
            elif not issues_before:
                already_good_count += 1

        print(f"  小计: 已修复 {cat_fixed}, 仍有问题 {cat_bad}, "
              f"原已合格 {len(cat_facts) - cat_fixed - cat_bad}")

    print(f"\n总计: 已修复 {fixed_count}, 仍有问题 {still_bad_count}, "
          f"原已合格 {already_good_count}")
    return fixed_count, still_bad_count, already_good_count


def test_normalize_specific_cases():
    """测试：用已知的坏 predicate 直接测试 normalize 效果"""
    print("\n" + "=" * 80)
    print("测试2: 特定坏谓词修正测试")
    print("=" * 80)

    test_cases = [
        # (原始predicate, 期望修正后predicate, 期望新增的字段)
        {
            "input": {"predicate": "青岛工厂产能达到", "qualifiers": {}},
            "expected_pred": "产能达到",
            "desc": "设施名前缀剥离",
        },
        {
            "input": {"predicate": "在中国销售收入为", "qualifiers": {}},
            "expected_pred": "销售收入为",
            "desc": "'在X'地域前缀剥离",
        },
        {
            "input": {"predicate": "一期工程年产能增加", "qualifiers": {}},
            "expected_pred": "年产能增加",
            "desc": "阶段前缀剥离",
        },
        {
            "input": {"predicate": "新造船涂料销售量为", "qualifiers": {}},
            "expected_pred": "销售量为",
            "desc": "产品类型前缀剥离",
        },
        {
            "input": {"predicate": "维修保养涂料销售量为", "qualifiers": {}},
            "expected_pred": "销售量为",
            "desc": "产品类型前缀剥离2",
        },
        {
            "input": {"predicate": "全厂船舶涂料产能增加至", "qualifiers": {}},
            "expected_pred": "产能增加至",
            "desc": "全厂+产品前缀剥离",
        },
        {
            "input": {"predicate": "2024年销售额为", "qualifiers": {}},
            "expected_pred": "销售额为",
            "desc": "年份前缀剥离",
        },
        {
            "input": {"predicate": "连续3年创下销售额和盈利纪录", "qualifiers": {}},
            "expected_pred": "创下销售额和盈利纪录",
            "desc": "连续X年剥离",
        },
        {
            "input": {"predicate": "张家港高性能涂料新工厂及液体涂料研发中心项目建设",
                      "qualifiers": {}},
            "expected_pred": "建设",
            "desc": "项目名嵌入剥离",
        },
        {
            "input": {"predicate": "船舶涂料改造项目（一期）通过验收",
                      "qualifiers": {}},
            "expected_pred": "通过验收",
            "desc": "含括号阶段的项目名",
        },
        {
            "input": {"predicate": "张家港基地预计年产值冲刺", "qualifiers": {}},
            "expected_pred": "预计年产值冲刺",
            "desc": "基地名前缀",
        },
        {
            "input": {"predicate": "区2024年销售额接近", "qualifiers": {}},
            "expected_pred": "销售额接近",
            "desc": "异常字符+年份前缀",
        },
        {
            "input": {"predicate": "青岛工厂生产效率为", "qualifiers": {}},
            "expected_pred": "生产效率为",
            "desc": "工厂+生产效率",
        },
        {
            "input": {"predicate": "青岛工厂一次合格率为", "qualifiers": {}},
            "expected_pred": "一次合格率为",
            "desc": "工厂+合格率",
        },
        {
            "input": {"predicate": "位居中国船舶涂料产销量第一", "qualifiers": {}},
            "expected_pred": "位居第一",
            "desc": "地域+产品嵌入排名谓词",
        },
        {
            "input": {"predicate": "是北欧涂料市场的领导者", "qualifiers": {}},
            "expected_pred": "是领导者",
            "desc": "地域+市场嵌入",
        },
        {
            "input": {"predicate": "投建高性能涂料新工厂及研发中心项目签约",
                      "qualifiers": {}},
            "expected_pred": "投建签约",
            "desc": "投建+项目名+签约",
        },
        {
            "input": {"predicate": "计划投资建设张家港高性能涂料新工厂及液体涂料研发中心项目",
                      "qualifiers": {}},
            "expected_pred": "计划投资建设",
            "desc": "计划投资建设+长项目名",
        },
        {
            "input": {"predicate": "一期工程已建成", "qualifiers": {}},
            "expected_pred": "已建成",
            "desc": "阶段+已建成",
        },
        {
            "input": {"predicate": "二期工程计划开工", "qualifiers": {}},
            "expected_pred": "计划开工",
            "desc": "阶段+计划开工",
        },
        {
            "input": {"predicate": "在埃塞俄比亚建厂", "qualifiers": {}},
            "expected_pred": "建厂",
            "desc": "在+国家名(无行政后缀)+建厂",
        },
        {
            "input": {"predicate": "在阿尔及利亚建厂", "qualifiers": {}},
            "expected_pred": "建厂",
            "desc": "在+国家名(无行政后缀)+建厂2",
        },
        {
            "input": {"predicate": "船舶涂料产销量排名第一", "qualifiers": {}},
            "expected_pred": "产销量排名第一",
            "desc": "产品类型+产销量排名",
        },
    ]

    passed = 0
    failed = 0
    for tc in test_cases:
        rec = {
            "fact_type": "TEST",
            "subject": "测试主体",
            "predicate": tc["input"]["predicate"],
            "qualifiers": copy.deepcopy(tc["input"].get("qualifiers", {})),
        }
        result = _normalize_record(rec)
        actual_pred = result["predicate"]
        expected = tc["expected_pred"]

        if actual_pred == expected:
            passed += 1
            status = "PASS"
        else:
            failed += 1
            status = "FAIL"

        print(f"  [{status}] {tc['desc']}")
        print(f"    输入: '{tc['input']['predicate']}'")
        print(f"    期望: '{expected}'")
        if actual_pred != expected:
            print(f"    实际: '{actual_pred}'  ← 不匹配!")
        else:
            print(f"    实际: '{actual_pred}'")
        # 显示新增的 qualifiers
        new_quals = {k: v for k, v in result.get("qualifiers", {}).items()
                     if k not in tc["input"].get("qualifiers", {})}
        if new_quals:
            print(f"    新增 qualifiers: {new_quals}")
        print()

    print(f"通过 {passed}/{passed + failed}, 失败 {failed}/{passed + failed}")
    return passed, failed


def test_extraction_with_llm(article_id=None):
    """测试：实际调用 LLM 提取一篇文章，然后 normalize 并检查质量"""
    print("\n" + "=" * 80)
    print("测试3: 实际 LLM 提取 + normalize + 质量检查")
    print("=" * 80)

    articles = get_jotun_articles()
    if not articles:
        print("没有找到佐敦相关文章")
        return

    if article_id:
        article = next((a for a in articles if a["id"].startswith(article_id)), None)
        if not article:
            print(f"未找到 ID 以 '{article_id}' 开头的文章")
            return
    else:
        # 选择中远佐敦交出亮眼成绩那篇（内容丰富，问题典型）
        article = next(
            (a for a in articles if "中远佐敦交出亮眼成绩" in (a.get("title") or "")),
            articles[0],
        )

    print(f"选择文章: {article['title']}")
    print(f"文章长度: {len(article['raw_text'])} 字")
    print(f"文章ID: {article['id']}")

    # 调用提取
    from app.services.cleaner import clean_document
    from app.services.full_extractor import _load_prompt, _list_to_dict as orig_list_to_dict
    from app.services.llm_client import get_llm_client

    cfg = get_config()

    # 清洗文本
    cleaned = clean_document(article["raw_text"])
    print(f"清洗后长度: {len(cleaned)} 字\n")

    # 加载 prompt 并调用 LLM
    system_prompt = _load_prompt()
    user_input = json.dumps({
        "document_title": article.get("title", ""),
        "document_source": "",
        "document_publish_time": article.get("publish_time", ""),
        "article_text": cleaned,
    }, ensure_ascii=False)

    print("正在调用 LLM 提取...")
    client = get_llm_client()
    result = client.chat_json(system_prompt, user_input)
    raw_data = result["data"]
    print(f"LLM 返回类型: {type(raw_data).__name__}")
    print(f"Token 消耗: 输入={result['input_tokens']}, 输出={result['output_tokens']}")

    # 解析
    parsed_records = []
    if isinstance(raw_data, dict):
        for fact_type, facts in raw_data.items():
            if not isinstance(facts, list):
                continue
            for item in facts:
                if isinstance(item, list):
                    rec = _list_to_dict(item)
                elif isinstance(item, dict):
                    rec = dict(item)
                    rec["fact_type"] = fact_type
                else:
                    continue
                if rec:
                    validated = _validate_record(rec, cfg)
                    if validated:
                        parsed_records.append(validated)
    print(f"解析出 {len(parsed_records)} 条原始记录\n")

    # 对每条做 normalize 前后对比
    good_before = 0
    fixed_by_norm = 0
    still_bad = 0

    for i, rec in enumerate(parsed_records):
        issues_before = check_fact_quality(rec)
        rec_orig_pred = rec["predicate"]

        # Normalize
        normalized = _normalize_record(copy.deepcopy(rec))
        issues_after = check_fact_quality(normalized)

        status_icon = "✓" if not issues_after else ("→" if issues_before and not issues_after else "✗")

        if not issues_before:
            good_before += 1
            status_icon = "✓"
        elif issues_before and not issues_after:
            fixed_by_norm += 1
            status_icon = "→修复"
        else:
            still_bad += 1
            status_icon = "✗"

        print(f"  [{status_icon}] [{normalized['fact_type']}] "
              f"{normalized.get('subject', '')} | {rec_orig_pred}")
        if rec_orig_pred != normalized["predicate"]:
            print(f"    → 修正为: '{normalized['predicate']}'")
        if issues_before:
            print(f"    修正前问题: {issues_before}")
        if issues_after:
            print(f"    修正后问题: {issues_after}")

    print(f"\n总计 {len(parsed_records)} 条:")
    print(f"  原已合格: {good_before}")
    print(f"  normalize 修复: {fixed_by_norm}")
    print(f"  仍有问题: {still_bad}")

    return parsed_records


def main():
    parser = argparse.ArgumentParser(description="独立提取功能测试")
    parser.add_argument("--skip-llm", action="store_true",
                        help="跳过 LLM 调用，仅测试 normalize 逻辑")
    parser.add_argument("--article-id", type=str, default=None,
                        help="指定文章ID（前缀匹配）")
    args = parser.parse_args()

    # 测试1: 特定坏谓词修正
    passed, failed = test_normalize_specific_cases()

    # 测试2: 对现有数据库中的事实做 normalize
    test_normalize_existing_facts()

    if not args.skip_llm:
        # 测试3: 实际 LLM 提取
        test_extraction_with_llm(args.article_id)

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)


if __name__ == "__main__":
    main()
