# 数据库事实原子结果质量分析报告

> **分析日期:** 2026-03-19
> **数据库:** `data/mvp.db`
> **分析目的:** 从数据提取和使用角度，发现当前数据结果存在的问题

---

## 一、数据概览

### 1.1 基本统计

| 指标 | 数量 |
|------|------|
| 文档 (source_document) | 2 |
| 文本块 (document_chunk) | 9 |
| 证据片段 (evidence_span) | 61 |
| 事实原子 (fact_atom) | 90 |

### 1.2 审核状态分布

| 状态 | 数量 | 占比 | 说明 |
|------|------|------|------|
| AUTO_PASS | 73 | 81.1% | 自动通过 |
| REJECTED | 9 | 10.0% | 已拒绝 |
| HUMAN_REVIEW_REQUIRED | 7 | 7.8% | 需人工审核 |
| HUMAN_PASS | 1 | 1.1% | 人工通过 |

### 1.3 事实类型分布

| 类型 | 数量 | 占比 |
|------|------|------|
| FINANCIAL_METRIC (财务指标) | 26 | 28.9% |
| CAPACITY (产能) | 17 | 18.9% |
| INVESTMENT (投资) | 17 | 18.9% |
| MARKET_SHARE (市场份额) | 9 | 10.0% |
| COMPETITIVE_RANKING (竞争排名) | 7 | 7.8% |
| SALES_VOLUME (销售量) | 7 | 7.8% |
| EXPANSION (扩建) | 5 | 5.6% |
| COOPERATION (合作) | 1 | 1.1% |
| PRICE_CHANGE (价格变动) | 1 | 1.1% |

---

## 二、质量问题详析

### 2.1 字段空值率统计

| 字段 | 空值数 | 总数 | 空值率 | 严重程度 |
|------|--------|------|--------|----------|
| object_text | 61 | 90 | 67.8% | 🔴 高 |
| location_text | 61 | 90 | 67.8% | 🔴 高 |
| time_expr | 53 | 90 | 58.9% | 🟡 中 |
| currency | 48 | 90 | 53.3% | 🟡 中 |
| unit | 16 | 90 | 17.8% | 🟢 低 |
| qualifier_json | 0 | 90 | 0.0% | 🟢 正常 |

### 2.2 实体链接率统计

| 字段 | 已链接数 | 总数 | 链接率 | 严重程度 |
|------|---------|------|--------|----------|
| subject_entity_id | 90 | 90 | 100.0% | 🟢 正常 |
| object_entity_id | 29 | 90 | 32.2% | 🔴 高 |
| location_entity_id | 20 | 90 | 22.2% | 🔴 高 |

---

## 三、具体问题列表

### 问题 1: 重复事实 (🔴 高优先级)

**现状:** 发现 4 组重复事实

| 事实类型 | subject_text | predicate | object_text | 重复次数 |
|----------|-------------|-----------|-------------|----------|
| FINANCIAL_METRIC | 上榜品牌船舶涂料业务 | 累计销售收入为 | None | 2 |
| FINANCIAL_METRIC | 船舶涂料 | 销售收入为 | None | 2 |
| INVESTMENT | 佐敦船舶涂料丹麦公司 | 扩建股权 | The Universal Hardwa... | 2 |
| SALES_VOLUME | 中远佐敦船舶涂料 | 销售量为 | None | 3 |

**根因分析:**
- `pipeline.py` 重跑时未完全清除旧数据
- `evidence_finder` 对同一 evidence_span 可能多次触发 fact_extractor
- 缺少按 (fact_type + subject + predicate + object) 的去重机制

**影响:**
- 同一事实被多次计数，导致统计分析结果偏大
- 需要人工审核去重，增加工作量

---

### 问题 2: object_text 大面积空值 (🔴 高优先级)

**现状:** 67.8% (61/90) 的 fact_atom 的 object_text 为空

**示例:**
```
[af8afa90] FINANCIAL_METRIC: 船舶涂料 | 销售收入增加 | obj=None
[84b64273] FINANCIAL_METRIC: 佐敦船舶涂料 | 销售收入为 | obj=None
[cc042d99] FINANCIAL_METRIC: 佐敦船舶涂料 | 累计销售收入为 | obj=None
```

**根因分析:**
- fact_extractor 的 prompt 未明确要求必须提取 object_text
- 某些事实类型（如 FINANCIAL_METRIC）可能确实没有明确的 object，但系统应区分"无object"和"未提取"

**影响:**
- 无法建立实体间关系图谱
- 上下游产业分析受限

---

### 问题 3: object_entity_id 链接率低 (🔴 高优先级)

**现状:** object_entity_id 仅 32.2% (29/90) 有值

**示例 object_text 内容:**
```
[8aa1d0e1] INVESTMENT: 船舶涂料项目
[16e41a92] INVESTMENT: 新建2000吨防腐涂料项目
[8c96eb3a] INVESTMENT: 新建 2.304 亿美元船舶涂料
[dc8436ca] CAPACITY: 船舶涂料产能
[cf72a054] INVESTMENT: 新建2000吨防腐涂料及船舶涂料
```

**根因分析:**
- entity_linker.py 主要处理 subject_text 的实体链接
- object_text 多为项目名、产品名等复合实体，识别难度高
- entity 表中可能缺少这些实体记录

**影响:**
- 无法将事实归因到具体实体
- 跨文档的实体关系分析受阻

---

### 问题 4: location_entity_id 链接率低 (🟡 中优先级)

**现状:** location_entity_id 仅 22.2% (20/90) 有值

**非空 location_text 示例:**
```
[af8afa90] FINANCIAL_METRIC: 全国
[84b64273] FINANCIAL_METRIC: 全国
[ca3a75b4] FINANCIAL_METRIC: 中国
[d10e3d6b] MARKET_SHARE: 全国
```

**根因分析:**
- entity_linker.py 未处理 location_text 的实体链接
- entity 表中可能缺少"全国"、"中国"等标准地点实体
- location_text 格式不统一（"全国"、"中国"、"全球"混用）

**影响:**
- 无法进行地理维度的聚合分析
- 跨地区比较受限

---

### 问题 5: time_expr 格式不统一 (🟡 中优先级)

**现状:** 58.9% 空值，且非空值的格式差异大

**非空 time_expr 示例:**
```
[af8afa90] FINANCIAL_METRIC: 去年
[ca3a75b4] FINANCIAL_METRIC: 2024 年
[38b00928] FINANCIAL_METRIC: 2024 年全国
[17b916fd] FINANCIAL_METRIC: 全年
[ece27913] CAPACITY: 目前
[9c2758f8] MARKET_SHARE: 2025年
[c11d78e5] SALES_VOLUME: 2024年
```

**根因分析:**
- fact_extractor prompt 未强调 time_expr 的标准格式
- 人类语言的时间表达（"去年"、"目前"、"全年"）未经标准化
- 混合了绝对时间（"2024年"）和相对时间（"去年"）

**影响:**
- 时间序列分析需要额外的时间标准化处理
- 无法直接进行跨文档的时间对齐

---

### 问题 6: currency 字段缺失 (🟡 中优先级)

**现状:** 53.3% (48/90) 的 fact_atom 的 currency 为空

**货币字段分布:**
| 货币 | 数量 |
|------|------|
| CNY (人民币) | 31 |
| JPY (日元) | 4 |
| USD (美元) | 3 |
| HKD (港元) | 3 |
| BRL (巴西雷亚尔) | 1 |

**根因分析:**
- fact_extractor prompt 未强制要求提取 currency
- 从"亿元"等中文单位可推断为 CNY，但未被利用

**影响:**
- 跨国比较无法直接换算
- 可能出现"亿元"和"亿日元"混用导致的数据歧义

---

### 问题 7: 单位格式不统一 (🟡 中优先级)

**现状:** 发现 15 种不同单位格式

**单位分布:**
| 单位 | 数量 |
|------|------|
| 亿元 | 17 |
| 万元 | 11 |
| % | 10 |
| 吨 | 7 |
| 艘/年 | 5 |
| 年 | 5 |
| 万吨 | 4 |
| 万载重吨 | 3 |
| 百万美元 | 3 |
| ... | ... |

**问题表现:**
- "亿元" vs "万元" 混用
- "%" 既在 unit 字段也在 value_text 中（如 "85.32%"）
- "万载重吨" vs "万吨" 混用

**根因分析:**
- fact_extractor 输出时未做单位标准化
- unit 和 value_text 职责不清（单位应在 unit，但有时在 value_text）

**影响:**
- 数值比较需要先统一单位
- 数据导出时可能产生歧义

---

### 问题 8: qualifier_json 结构混乱 (🟡 中优先级)

**现状:** qualifier_json 内部结构不统一，同样的信息有多种表示方式

**示例:**
```json
// 格式1: metric_name + growth_description
{"metric_name": "销售收入", "growth_description": "同比增长"}

// 格式2: metric_name + change_amount
{"metric_name": "销售收入", "change_amount": 15.64, "change_amount_unit": "亿元", "change_amount_currency": "CNY"}

// 格式3: 仅 yoy
{"yoy": "7.3%", "is_approximate": true}

// 格式4: change_percentage_points
{"change_percentage_points": -2.42}
```

**根因分析:**
- fact_extractor 对不同事实类型使用不同的 qualifier 字段
- 缺少统一的 qualifier 结构规范
- "同比增长" 和 "yoy" 和 "change_percentage_points" 表达同样概念

**影响:**
- 使用者需要理解多种 qualifier 字段含义
- 无法用统一规则查询"所有含同比增长率的事实"

---

## 四、根因总结

| 问题类别 | 直接原因 | 根本原因 |
|---------|---------|---------|
| 重复事实 | 幂等性保护不足 | pipeline 缺少去重机制 |
| object_text 空值 | prompt 未强制要求 | 抽取规则定义不完整 |
| object_entity_id 低链接 | entity_linker 未处理 object | 实体库覆盖不足 |
| location_entity_id 低链接 | entity_linker 未处理 location | 地点实体未标准化 |
| time_expr 格式乱 | 无标准化规范 | prompt 和存储规则缺失 |
| currency 缺失 | prompt 未强调 | 可从单位推断但未实现 |
| 单位不统一 | 无标准化规范 | 抽取和存储规则不明确 |
| qualifier 混乱 | 各 fact_type 自行其是 | 缺少统一结构定义 |

---

## 五、修复优先级建议

| 优先级 | 问题 | 预计影响 | 修复难度 |
|--------|------|---------|---------|
| P0 | 重复事实去重 | 数据准确性 | 低 |
| P0 | object_text 空值 | 数据完整性 | 中 |
| P1 | object_entity_id 链接 | 实体关联分析 | 中 |
| P1 | currency 字段补全 | 跨货币比较 | 低 |
| P2 | location_entity_id 链接 | 地理分析 | 中 |
| P2 | qualifier 结构统一 | 数据可用性 | 高 |
| P3 | time_expr 标准化 | 时间序列分析 | 中 |
| P3 | 单位格式统一 | 数值比较 | 中 |

---

## 六、相关文档

- 实施计划: `2026-03-19-atom-data-quality.md`
- 效率分析: `efficiency-analysis.md`
