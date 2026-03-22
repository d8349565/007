"""
实体工具模块 —— 统一基础层

所有实体相关的纯函数、常量词表统一在此模块中管理，
供 entity_linker、entity_merger、entity_analyzer 等服务模块引用。

包含：
- normalize()           : 唯一规范化函数
- contain_score()       : 唯一包含度评分
- fingerprint()         : 快速去重指纹
- infer_entity_type()   : 实体类型推断（返回 primary_type + tags）
- LCS 相似度计算
- 常量词表
"""

import re
from difflib import SequenceMatcher

# ═══════════════════════════════════════════════════════════════
# 常量词表
# ═══════════════════════════════════════════════════════════════

# 法人后缀词表（规范化时去除）
LEGAL_SUFFIXES = ("有限公司", "有限责任公司", "股份有限公司", "股份公司")

# 括号正则：去掉中英文括号内容
PAREN_RE = re.compile(r"[（(][^）)]*[）)]")

# 地理修饰词（括号内出现表示不同注册主体）
GEO_QUALIFIERS = frozenset([
    "香港", "青岛", "上海", "北京", "天津", "广州", "深圳", "南京", "成都",
    "宁波", "武汉", "苏州", "常州", "无锡", "重庆", "济南",
])

# 跳过词（太短的通用词不参与候选生成）
SKIP_NAMES = frozenset([
    "中国", "全国", "全球", "国内", "在华", "我国",
    "华东", "华南", "华北", "华中", "东北", "西南", "西北",
    "船舶", "项目", "技改项目", "迁扩建项目",
])

# 地域前缀词（名称含这些词时不能与其他配对，避免"美国X" vs "中国X"误判）
REGION_PREFIXES = ("中国", "美国", "日本", "印度", "在华", "全球", "国内", "我国")

# 常用地点映射
LOCATION_KEYWORDS = {
    "全国": "REGION", "中国": "COUNTRY", "全球": "REGION",
    "国内": "REGION", "在华": "REGION",
    "华东": "REGION", "华南": "REGION", "华北": "REGION",
    "华中": "REGION", "西南": "REGION", "西北": "REGION", "东北": "REGION",
}

# 关系类型常量
RELATION_TYPES = ("SUBSIDIARY", "SHAREHOLDER", "JV", "BRAND", "PARTNER", "INVESTS_IN")

# 事实类型 → 关联候选类型
FACT_TYPES_WITH_OBJECT = ("COOPERATION", "INVESTMENT", "EXPANSION")

# 实体主类型优先级（数字越小优先级越高）
PRIMARY_TYPE_PRIORITY = {
    "GROUP": 1,
    "COMPANY": 2,
    "BRAND": 3,
    "INDIVIDUAL": 4,
    "PROJECT": 5,
    "INDUSTRY": 6,
    "SEGMENT": 7,
    "MARKET": 8,
    "REGION": 9,
}

# 项目/工程类结尾词
PROJECT_ENDINGS = ("项目", "工程", "专项", "计划")

# 独立设施关键词
FACILITY_KEYWORDS = ("工厂", "生产基地", "研发基地", "产业基地", "产业园区")

# 公司类后缀词
COMPANY_SUFFIXES = (
    "公司", "集团", "股份", "有限", "企业", "涂料", "化工", "科技",
    "工业", "实业", "控股", "国际", "材料", "制造",
)

# 最短参与候选的字符数
MIN_NAME_LEN = 4


# ═══════════════════════════════════════════════════════════════
# 核心函数
# ═══════════════════════════════════════════════════════════════

def normalize(name: str) -> str:
    """
    规范化实体名称：去括号 + 去法人后缀 + 首尾空格。

    示例：
        "中远佐敦船舶涂料（青岛）有限公司" → "中远佐敦船舶涂料"
        "BrandX Co. (Global)" → "BrandX"
    """
    s = PAREN_RE.sub("", name).strip()
    for suffix in LEGAL_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    return s


def fingerprint(name: str) -> str:
    """返回规范化后的字符串，用于唯一性判断和快速去重。"""
    return normalize(name)


def contain_score(a: str, b: str) -> float:
    """
    若一方包含另一方，返回 0.5~1.0 的分数；否则返回 0。

    - 完全相等 = 1.0
    - 包含关系：0.5 + (len(shorter)/len(longer)) * 0.45
      → 范围 [0.5, 0.95]
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer:
        return 0.5 + (len(shorter) / len(longer)) * 0.45
    return 0.0


def lcs_ratio(a: str, b: str) -> float:
    """
    最长公共子序列 / 较长字符串长度。
    用于衡量两字符串的编辑相似度。
    """
    a, b = a.strip().replace("（", "(").replace("）", ")").replace(" ", ""), \
           b.strip().replace("（", "(").replace("）", ")").replace(" ", "")
    if not a or not b:
        return 0.0
    m, n = len(a), len(b)
    if m > 40 or n > 40:
        return 0.0
    seq_matcher = SequenceMatcher(None, a, b)
    return seq_matcher.ratio()


def similarity(a: str, b: str) -> float:
    """
    综合相似度 (0~1)。
    先看包含关系（门槛 0.95），再看 LCS 相似度。
    """
    contain = contain_score(a, b)
    if contain >= 0.95:
        return contain
    lcs = lcs_ratio(a, b)
    return max(contain, lcs)


def extract_geo_paren(text: str) -> str | None:
    """
    提取括号内的地区关键词。
    如 "EntityA（香港）有限公司" → "香港"
    若括号内容不在地区词表中，返回 None。
    """
    m = re.search(r"[（(]([^）)]+)[）)]", text)
    if not m:
        return None
    content = m.group(1).strip()
    if content in GEO_QUALIFIERS:
        return content
    return None


def has_region_prefix(name: str) -> bool:
    """判断名称是否以地域前缀开头或结尾。"""
    return any(name.startswith(p) or name.endswith(p) for p in REGION_PREFIXES)


def infer_entity_type(text: str, fact_type: str = "") -> tuple[str, list[str]]:
    """
    根据文本特征推断实体的主类型和多标签。

    返回: (primary_type, tags)
    例如：
        "集团A" → ("GROUP", ["company", "brand"])
        "有限公司" → ("COMPANY", [])

    主类型推断优先级（从高到低）：
      1. 以"项目/工程"结尾 → PROJECT
      2. 含独立设施词且无公司法人后缀 → PROJECT
      3. 含集合主体词 → GROUP
      4. 含公司法人后缀 → COMPANY
      5. COOPERATION fact_type → PROJECT
      6. 其他 → UNKNOWN

    多标签推断：
      - 含 "集团" 但以公司后缀结尾 → 额外 tag: ["group"]
      - 含 "品牌" → 额外 tag: ["brand"]
      - 等等
    """
    if not text:
        return ("UNKNOWN", [])

    tags: list[str] = []

    # 0. 含指标/概念后缀 → 非实体，直接返回 UNKNOWN
    _METRIC_ENDINGS = ("产能", "产量", "销量", "份额", "价格", "市场", "营收", "利润", "增速", "增长率")
    for me in _METRIC_ENDINGS:
        if text.endswith(me):
            return ("UNKNOWN", tags)

    # 1. 以项目/工程结尾 → PROJECT（最高优先级）
    for ending in PROJECT_ENDINGS:
        if text.endswith(ending):
            return ("PROJECT", tags)

    # 2. 含独立设施词且无公司法人后缀 → PROJECT
    if any(kw in text for kw in FACILITY_KEYWORDS):
        if not any(s in text for s in ("公司", "集团", "股份", "有限")):
            return ("PROJECT", tags)

    # 3. 集合主体检测 → GROUP
    if any(kw in text for kw in ("前十强", "前五强", "前三强", "上榜")):
        return ("GROUP", tags)
    if "品牌" in text and any(kw in text for kw in ("外资", "国产", "本土")):
        return ("GROUP", tags)

    # 4. 含公司法人后缀 → COMPANY
    has_company_suffix = False
    for suffix in COMPANY_SUFFIXES:
        if suffix in text:
            has_company_suffix = True
            break
    if has_company_suffix:
        if "集团" in text:
            tags.append("group")
        if "品牌" in text:
            tags.append("brand")
        return ("COMPANY", tags)

    # 5. COOPERATION 类型的 object 通常是项目
    if fact_type == "COOPERATION":
        return ("PROJECT", tags)

    return ("UNKNOWN", tags)
