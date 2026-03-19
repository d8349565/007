# 事实原子数据质量改进实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 解决从 fact_atom 使用角度发现的 7 大数据质量问题，提升数据可用性

**Architecture:** 6 个子问题相互独立，可并行分析后按优先级执行修复。

**Tech Stack:** Python/SQLite, LLM API, 数据迁移脚本

---

## 一、数据问题全景分析

### 当前数据质量总览

| 问题 | 严重程度 | 影响范围 |
|------|---------|---------|
| 重复事实（3x/2x） | 🔴 高 | 7组，~15条 |
| object_entity_id 未链接 | 🔴 高 | 68.1% 缺失 |
| location_entity_id 未链接 | 🔴 高 | 100% 缺失 |
| time_expr 空值 | 🟡 中 | 57.5% |
| location_text 空值 | 🟡 中 | 75.3% |
| Currency 空值（Financial类） | 🟡 中 | 60.3% |
| 单位字段不一致 | 🟡 中 | 15种格式 |
| qualifier 结构不统一 | 🟡 中 | 变化量分散在 qualifier 和 value_text |

---

## 二、子问题 1: 重复事实去重

### 问题描述

同一篇文档中相同事实被多次抽取：
- `[3x] MARKET_SHARE | 前十强企业 | 占全国市场份额 | None`
- `[3x] SALES_VOLUME | 中远佐敦船舶涂料 | 销售量为 | None`
- 多篇文档中 `[2x] FINANCIAL_METRIC | 上榜品牌船舶涂料业务 | 累计销售收入为 | None`

**根因：** evidence_finder 对同一 evidence_span 多次触发 fact_extractor，或 pipeline 重跑时未清除旧数据。

**Files:**
- 新增: `scripts/deduplicate_facts.py`
- 修改: `app/services/pipeline.py` — 增加幂等性检查

- [ ] **Step 1: 编写重复检测脚本**

```python
# scripts/deduplicate_facts.py
"""
检测并报告重复事实组。
按 (fact_type, subject_text, predicate, object_text) 分组，
保留 confidence_score 最高的一条，其余标记为 REJECTED。
"""
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.db import get_connection
from app.services.query import calculate_cost

def find_duplicate_groups():
    conn = get_connection()
    groups = conn.execute('''
        SELECT fact_type, subject_text, predicate, object_text, COUNT(*) as cnt
        FROM fact_atom
        GROUP BY fact_type, subject_text, predicate, object_text
        HAVING COUNT(*) > 1
    ''').fetchall()
    return [dict(g) for g in groups]

def deduplicate():
    groups = find_duplicate_groups()
    conn = get_connection()
    removed = 0
    for g in groups:
        facts = conn.execute('''
            SELECT id, confidence_score FROM fact_atom
            WHERE fact_type=? AND subject_text=? AND predicate=? AND object_text=?
            ORDER BY confidence_score DESC
        ''', (g['fact_type'], g['subject_text'], g['predicate'], g['object_text'])).fetchall()

        # 保留最高分，其余标记为 REJECTED
        keep_id = facts[0]['id']
        for f in facts[1:]:
            conn.execute(
                "UPDATE fact_atom SET review_status='REJECTED', review_note='重复抽取，已被去重' WHERE id=?",
                (f['id'],)
            )
            removed += 1
    conn.commit()
    conn.close()
    return removed
```

- [ ] **Step 2: 运行检测，报告重复情况**

```bash
python scripts/deduplicate_facts.py --dry-run
```
Expected: 报告 7 组重复，涉及 ~15 条事实

- [ ] **Step 3: 执行去重（非 dry-run）**

```bash
python scripts/deduplicate_facts.py
```
Expected: 更新重复事实的 review_status 为 REJECTED

- [ ] **Step 4: 在 pipeline.py 添加幂等性检查**

在 `process_document` 开始时，检查是否已有成功处理的记录，如有则跳过或询问。

- [ ] **Step 5: 提交**

```bash
git add scripts/deduplicate_facts.py app/services/pipeline.py
git commit -m "fix: deduplicate facts and add pipeline idempotency check"
```

---

## 三、子问题 2: object_entity_id 链接缺失

### 问题描述

subject_entity_id 100% 已链接，但 object_entity_id 仅 32.9% 链接。

**根因：** entity_linker.py 仅处理 subject_text，未处理 object_text（如"船舶涂料"、"中国市场"等）。

**Files:**
- 修改: `app/services/entity_linker.py`

- [ ] **Step 1: 读取 entity_linker.py 分析当前逻辑**

```bash
cat app/services/entity_linker.py
```

- [ ] **Step 2: 检查 object_text 的内容类型**

```bash
python -c "
from app.models.db import get_connection
conn = get_connection()
samples = conn.execute('SELECT DISTINCT object_text FROM fact_atom WHERE object_text IS NOT NULL AND object_text != \"\" LIMIT 20').fetchall()
for s in samples: print(s[0])
"
```
Expected: object_text 包括产品类型、地区、货币等

- [ ] **Step 3: 扩展 entity_linker 支持 object_entity_id**

修改 `extract_and_link_entities` 函数，增加对 object_text 的实体识别和链接。

- [ ] **Step 4: 重新运行 entity linking**

对现有 73 条事实执行更新：
```python
# 重新链接所有 facts 的 object_entity_id
for fact in conn.execute('SELECT id, object_text FROM fact_atom WHERE object_text IS NOT NULL'):
    entity_id = lookup_entity(fact['object_text'])
    if entity_id:
        conn.execute('UPDATE fact_atom SET object_entity_id=? WHERE id=?', (entity_id, fact['id']))
```

- [ ] **Step 5: 验证链接率提升**

```bash
python -c "
from app.models.db import get_connection
conn = get_connection()
r = conn.execute('SELECT SUM(CASE WHEN object_entity_id IS NOT NULL THEN 1 ELSE 0 END) as linked, COUNT(*) as total FROM fact_atom').fetchone()
print(f'object_entity_id: {r[0]}/{r[1]} = {r[0]/r[1]*100:.1f}%')
"
```
Expected: 从 32.9% 提升到 >80%

- [ ] **Step 6: 提交**

---

## 四、子问题 3: location_entity_id 未链接

### 问题描述

location_text 有值（如"全国"、"中国"），但 location_entity_id 100% 为 NULL。

**Files:**
- 修改: `app/services/entity_linker.py`
- 新增: `scripts/init_location_entities.py`

- [ ] **Step 1: 创建地点实体初始化脚本**

```python
# scripts/init_location_entities.py
"""
在 entity 表中预填充常用地点实体，
支持：中国、全国、华东、华南、华北、全球 等。
"""
location_entities = [
    {"name": "全国", "type": "REGION", "country": "中国"},
    {"name": "中国", "type": "COUNTRY", "country": "中国"},
    {"name": "全球", "type": "REGION", "country": None},
    {"name": "华东", "type": "REGION", "country": "中国"},
    {"name": "华南", "type": "REGION", "country": "中国"},
    {"name": "华北", "type": "REGION", "country": "中国"},
    # ... 更多
]
```

- [ ] **Step 2: 实现 location linking 逻辑**

在 entity_linker.py 中添加 `link_location_entities()` 函数：
```python
def link_location_entities():
    """将 fact_atom.location_text 与 entity 表关联"""
    conn = get_connection()
    locations = conn.execute(
        "SELECT id, location_text FROM fact_atom WHERE location_text IS NOT NULL AND location_text != '' AND location_entity_id IS NULL"
    ).fetchall()
    for fact_id, loc_text in locations:
        entity = conn.execute(
            "SELECT id FROM entity WHERE canonical_name LIKE ? OR normalized_name LIKE ?",
            (f"%{loc_text}%", f"%{loc_text}%")
        ).fetchone()
        if entity:
            conn.execute("UPDATE fact_atom SET location_entity_id=? WHERE id=?", (entity['id'], fact_id))
    conn.commit()
```

- [ ] **Step 3: 验证链接率**

Expected: 从 0% 提升到 >70%

---

## 五、子问题 4: time_expr / location_text / currency 空值率高

### 问题描述

- time_expr 57.5% 空 — 时间是重要分析维度
- location_text 75.3% 空 — 地理分析受限
- currency 60.3% 空 — 跨国比较困难

**根因分析：** fact_extractor 的 prompt 中未强调这些字段必须提取。

**Files:**
- 修改: `app/prompts/fact_type_rules/financial_metric.txt`
- 修改: `app/prompts/fact_type_rules/sales_volume.txt`
- 新增: `scripts/backfill_missing_fields.py`

- [ ] **Step 1: 审查 fact_extractor prompt**

检查 `app/prompts/fact_type_rules/` 下各文件是否明确要求提取 time_expr、location_text、currency。

- [ ] **Step 2: 更新 prompt 强调必填字段**

在 financial_metric.txt 等文件的提取规则中添加：
```
【必须提取】
- time_expr: 明确的时间表达式，如"2024年"、"截至12月底"
- currency: 货币单位（CNY/HKD/JPY/USD），从原文推断
- location_text: 地理范围（全国、华东、中国等）
```

- [ ] **Step 3: 编写 backfill 脚本**

对于已有事实，尝试从 evidence_text 和 qualifier_json 中反推缺失字段：
```python
# scripts/backfill_missing_fields.py
"""
从现有数据中反推缺失字段：
1. time_expr: 从 evidence_text 中正则匹配年份/日期
2. location_text: 从 qualifier_json.market_scope 中获取
3. currency: 从 unit 字段推断（亿元→CNY，亿港元→HKD，亿日元→JPY，亿美元→USD）
"""
```

- [ ] **Step 4: 验证空值率下降**

```bash
python -c "
from app.models.db import get_connection
conn = get_connection()
stats = conn.execute('''
    SELECT
        SUM(CASE WHEN time_expr IS NULL OR time_expr=\"\" THEN 1 ELSE 0 END) as null_time,
        SUM(CASE WHEN location_text IS NULL OR location_text=\"\" THEN 1 ELSE 0 END) as null_loc,
        SUM(CASE WHEN currency IS NULL OR currency=\"\" THEN 1 ELSE 0 END) as null_cur,
        COUNT(*) as total
    FROM fact_atom
''').fetchone()
t = stats
print(f'time_expr: {t[0]}/{t[3]} = {t[0]/t[3]*100:.1f}% null')
print(f'location_text: {t[1]}/{t[3]} = {t[1]/t[3]*100:.1f}% null')
print(f'currency: {t[2]}/{t[3]} = {t[2]/t[3]*100:.1f}% null')
"
```

---

## 六、子问题 5: qualifier 结构不统一

### 问题描述

同类信息分散在 value_text、qualifier_json、unit 等多个字段中：
- 变化量(yoy) 有时在 qualifier_json.yoy，有时在 qualifier_json.change_amount
- 单位有时在 unit，有时在 value_text（如"98.12亿元"）
- is_approximate 只在部分 fact 中出现

**使用痛点：** 查询"所有含同比增长率的财务指标"需要同时查 yoy、change_amount、growth_description 等多种 qualifier 字段。

**Files:**
- 修改: `config.yaml` — 统一 qualifier 白名单
- 新增: `scripts/normalize_qualifiers.py`

- [ ] **Step 1: 统一 qualifier 白名单**

在 `config.yaml` 中为每种 fact_type 固定 qualifier 字段集：
```yaml
qualifier_whitelist:
  FINANCIAL_METRIC:
    - metric_name       # 指标名称
    - yoy              # 同比（统一用 yoy）
    - qoq              # 环比
    - change_amount    # 变化量（绝对值）
    - change_pct      # 变化百分比
    - is_approximate   # 是否近似值
    - is_forecast      # 是否预测值
    - currency         # 货币（在 qualifier 层统一）
    - time_range       # 时间范围
```

- [ ] **Step 2: 编写规范化脚本**

```python
# scripts/normalize_qualifiers.py
"""
将散乱的 qualifier 字段统一：
- change_amount + change_amount_unit → change_amount (合并)
- growth_description → yoy (转换)
- change_pct (百分点格式) → 归一化为百分比
"""
```

---

## 七、子问题 6: 单位格式不统一

### 问题描述

15 种不同单位格式，unit 字段和 value_text 中的单位表示不一致：
- `"亿元"` vs `"亿"` (currency context needed)
- `"万载重吨"` vs `"万吨"` vs `"万吨/年"`

**Files:**
- 新增: `scripts/normalize_units.py`

- [ ] **Step 1: 定义单位标准化映射**

```python
UNIT_NORMALIZATION = {
    "亿": "亿元",      # 金额默认人民币
    "亿美元": "亿美元",
    "亿日元": "亿日元",
    "亿港元": "亿港元",
    "万吨/年": "万吨/年",
    "万载重吨": "万载重吨",
    # ...
}
```

- [ ] **Step 2: 执行标准化**

对 unit 字段和 value_text 分别处理，确保一致性。

---

## 执行选项

**Plan complete. Three execution options:**

**1. Subagent-Driven (recommended)** — 每个子问题分配独立子代理，并行分析后顺序执行修复

**2. Inline Execution** — 在当前会话顺序执行，有检查点

**3. Priority-First** — 先执行 P0 优先级（重复事实 + entity linking），再并行其他

**Which approach?**
