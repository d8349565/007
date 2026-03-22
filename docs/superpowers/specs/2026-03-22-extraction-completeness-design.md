# 事实抽取完整度提升方案

## 目标

提升资讯文章的事实抽取完整度，目标：**每篇 1000-2000 字文章抽取 20-30 条重要事实**（当前约 3-5 条，严重不足）。

## 问题诊断

### 核心问题

1. **Predicate 白名单覆盖不足** — 大量重要事实的谓词词汇未在白名单中，导致 LLM 抽取后被过滤或评分降低
2. **Extraction Prompt 缺乏数量引导** — 只说"Extract ALL"但没有结构化要求，LLM 倾向于只抽"最明显"的事实
3. **审核分流机制过于保守** — COMPETITIVE_RANKING 100% 强制人工审核，导致大量 Ranking 事实积压
4. **单次抽取遗漏上下文关联事实** — 很多事实需要联系上下文（如"该公司"指代）才能完整抽取

### 当前数据

- 待人工审核（HUMAN_REVIEW_REQUIRED）：57 条 / 219 总数 = 26%
- COMPETITIVE_RANKING 类型积压最多：17 条
- 单文档平均抽取：~3-5 条（目标 20-30 条）

## 已实施改进（Phase 1）

### 1. Predicate 白名单扩展 ✅

**文件：** `config.yaml`

| fact_type | 新增 predicate 词根 |
|-----------|-------------------|
| MARKET_SHARE | `市占率达到`、`市场份额达` |
| FINANCIAL_METRIC | `实现`、`综合销售额` |
| CAPACITY | `产能为`、`设计产能为` |
| COOPERATION | `击败`、`提供涂料`、`应用于`、`供货于` |
| SALES_VOLUME | `消耗` |

### 2. Extraction Prompt 修改 ✅

在 `app/prompts/fact_extractor_full.txt` 增加了 quantity expectations 引导规则。

### 3. 审核分流机制调整 ✅

```yaml
review:
  auto_pass_confidence: 0.85
  force_human_review_types: []
```

### Phase 1 验证结果

- 事实数：32 → 38（+18.75%）
- **未达目标**（目标 20-30 条/文档，实际平均 12.7 条）
- 需要 Phase 2 改进

---

## Phase 2 改进方案：C1a 两阶段上下文感知抽取

### 核心思路

**两阶段强制全面抽取，同时保留完整上下文：**

**阶段 1（全量初抽取）：**
- 使用全文模式，让 LLM 按 9 类 fact_type 强制输出
- 每个 fact_type 至少 1 条，没有就留空数组
- 输出格式改为按 fact_type 分组的结构化 JSON

**阶段 2（上下文补全）：**
- 对每个 fact_type，将已抽取的事实列表 + 全文一起发给 LLM
- 要求 LLM 检查"这些事实是否完整？有无遗漏？特别是需要联系上下文才能确认主体的事实"
- **特别提示**：coreference 解析（"该公司"、"其"等指代）

### 新输出格式（结构化）

```
## Output format — STRUCTURED BY TYPE

输出格式为 JSON，包含 9 个固定 key，每个 key 对应一类事实类型：

{
  "FINANCIAL_METRIC": [
    {"subject": "...", "predicate": "...", "value_num": ..., ...},
    ...
  ],
  "SALES_VOLUME": [...],
  "CAPACITY": [...],
  "INVESTMENT": [...],
  "EXPANSION": [...],
  "MARKET_SHARE": [...],
  "COMPETITIVE_RANKING": [...],
  "COOPERATION": [...],
  "PRICE_CHANGE": [...]
}

**强制要求：**
- 每个 key 的数组**至少包含 1 条**事实（除非文章确实没有该类型的事实）
- 如果某个 fact_type 在文章中有多个事实，**必须全部列出**
- 数组可以为空 []（仅当文章确实不包含该类型时）
- 每个 fact_type 在阶段 2 必须做上下文补全检查
```

### 阶段 2 Prompt 模板

```
## Context Complementation Review

**Article:** [完整文章文本]

**Already extracted [FACT_TYPE] facts:**
[已抽取的事实列表]

**Task:** 检查上述列表是否完整：

1. **同一实体的多维度事实**
   - 例如佐敦：销售额 342 亿、船舶涂料第 1、全球第 9，是否全部抽取？

2. **需要联系上下文才能确认主体的事实**
   - "该公司投资 21 亿" → 主体需要联系前文确认为"佐敦"
   - "其销售额位列全球第 9" → 需要确认"其"指代

3. **被分散在多个句子中的相关事实**
   - 段落 1 说"佐敦在张家港建厂"
   - 段落 2 说"该项目投资 21 亿元"
   - → 需要合并为一条完整事实

4. **同一公司的并列表述**
   - "立邦、佐敦、华辉涂料均入选" → 应拆分为 3 条独立事实

如有补充或修正，在下方列表中追加或修改。
```

## 实施文件清单

1. `app/prompts/fact_extractor_full.txt` — 修改为结构化输出格式 + 阶段 1 prompt
2. `app/prompts/context_complementation.txt` — 阶段 2 补全 prompt（新建）
3. `app/services/full_extractor.py` — 修改解析逻辑适配结构化输出 + 实现两阶段抽取

## 风险评估

- **中等风险**：修改了 full_extractor.py 的核心抽取逻辑
- **可能的副作用**：
  - 阶段 2 会增加 LLM 调用次数（每个 fact_type 一次）
  - 需要确保去重逻辑正确
  - 解析逻辑变更可能引入新 bug

## 验证方法

同 Phase 1：3 篇测试文档，对比改进前后 fact_atom 数量
