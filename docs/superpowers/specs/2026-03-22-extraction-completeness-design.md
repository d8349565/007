# 事实抽取完整度提升方案

## 目标

提升资讯文章的事实抽取完整度，目标：**每篇 1000-2000 字文章抽取 20-30 条重要事实**（当前约 3-5 条，严重不足）。

## 问题诊断

### 核心问题

1. **Predicate 白名单覆盖不足** — 大量重要事实的谓词词汇未在白名单中，导致 LLM 抽取后被过滤或评分降低
2. **Extraction Prompt 缺乏数量引导** — 只说"Extract ALL"但没有结构化要求，LLM 倾向于只抽"最明显"的事实
3. **审核分流机制过于保守** — COMPETITIVE_RANKING 100% 强制人工审核，导致大量 Ranking 事实积压

### 当前数据

- 待人工审核（HUMAN_REVIEW_REQUIRED）：57 条 / 219 总数 = 26%
- COMPETITIVE_RANKING 类型积压最多：17 条
- 单文档平均抽取：~3-5 条（目标 20-30 条）

## 改进方案

### 1. Predicate 白名单扩展

**文件：** `config.yaml`

**新增词根：**

| fact_type | 新增 predicate 词根 |
|-----------|-------------------|
| MARKET_SHARE | `市占率达到`、`市场份额达` |
| FINANCIAL_METRIC | `实现`、`综合销售额` |
| CAPACITY | `产能为`、`设计产能为` |
| COOPERATION | `击败`、`提供涂料`、`应用于`、`供货于` |
| SALES_VOLUME | `消耗` |

### 2. Extraction Prompt 修改

**文件：** `app/prompts/fact_extractor_full.txt`

**在 "What to extract" 之后新增：**

```
## Quantity expectations

For each fact_type category below, extract AT LEAST the number of facts indicated.
If the article contains multiple examples, extract ALL of them:
- FINANCIAL_METRIC: 至少 2-3 条
- SALES_VOLUME: 至少 1-2 条
- CAPACITY: 至少 1-2 条
- INVESTMENT: 至少 1-2 条
- EXPANSION: 至少 1-2 条
- MARKET_SHARE: 至少 1-2 条
- COMPETITIVE_RANKING: 至少 2-3 条
- COOPERATION: 至少 1-2 条
- PRICE_CHANGE: 若有则抽

如果某段落提到多个公司/品牌的同类事实，应为每个公司/品牌单独创建一条记录。
```

### 3. 审核分流机制调整

**文件：** `config.yaml`

**修改内容：**

```yaml
review:
  auto_pass_confidence: 0.85  # 从 0.90 降低至 0.85
  force_human_review_types: []  # 移除 COMPETITIVE_RANKING
```

**预期效果：**
- HUMAN_REVIEW_REQUIRED 比例：26% → 10-15%
- 高质量 COMPETITIVE_RANKING 事实自动通过

## 验证方法

1. 选取 3 篇代表性文档，记录当前事实抽取数量（基准）
2. 应用上述改进
3. 重新处理相同文档，对比 fact_atom 数量

**目标指标：**
- 单文档事实数：3-5 条 → 20-30 条
- HUMAN_REVIEW_REQUIRED 比例：26% → 10-15%

## 实施文件清单

1. `config.yaml` — predicate 白名单扩展 + 审核配置调整
2. `app/prompts/fact_extractor_full.txt` — quantity expectations 引导规则

## 风险评估

- **低风险**：仅修改配置和 prompt，不涉及核心逻辑
- **可能的副作用**：白名单扩展后，可能有少量低质量事实进入抽取，需要观察审核分流效果
