# 三 Agent 抽取 Prompt 规范

## 一、文档定位

本文档用于定义“资讯颗粒化项目”的三 Agent 抽取 Prompt 规范，作为后续抽取链路的统一执行标准。

三 Agent 分工如下：

- `Evidence Finder`：负责发现候选证据
- `Fact Extractor`：负责将证据片段抽取为结构化事实
- `Reviewer / Validator`：负责对抽取结果进行证据一致性校验

本文档重点解决以下问题：

- 每个 Agent 到底负责什么
- 每个 Agent 的输入和输出是什么
- Prompt 应该如何写，才能尽量降低漂移
- 哪些约束必须写死
- 如何与 `fact_type 字典与抽取字段规范` 对齐
- 如何减少幻觉、补脑、字段乱飞、跨句串错

说明：

- 所有英文名称、字段名、状态名保持不变
- 在英文术语上补充中文注释
- 本规范优先服务于 MVP 阶段
- 本规范默认抽取对象为行业资讯、公告、研报、公众号、新闻稿等文本内容

---

## 二、总体设计原则

结论：

**不要用一个万能 Prompt 处理全文。**
必须拆成三个阶段，否则抽取质量会明显失控。

原因：

1. `Evidence Finder` 负责“找值得抽的内容”
2. `Fact Extractor` 负责“按 schema 抽结构”
3. `Reviewer / Validator` 负责“检查是否真的被 evidence 支持”

如果三件事放在一个 Prompt 里，会出现以下问题：

- evidence 识别不准
- schema 输出不稳定
- 模型自己给自己背书
- 容易补脑
- 一句话多事实时漏抽
- 数值、时间、单位错配

---

## 三、三 Agent 职责边界

## 3.1 `Evidence Finder`

中文说明：证据发现 Agent。

职责：

- 判断给定文本片段中是否包含“可结构化事实”
- 识别候选 `fact_type`
- 提取最小 `evidence_span`
- 初步判断是否值得进入抽取链路

不负责：

- 不负责最终字段抽取
- 不负责实体标准化
- 不负责关系生成
- 不负责最终审核通过

输出目标：

- 给下游 `Fact Extractor` 提供尽可能短、尽可能干净的 evidence

---

## 3.2 `Fact Extractor`

中文说明：事实抽取 Agent。

职责：

- 读取 `evidence_span`
- 根据候选 `fact_type` 和字段规范输出结构化 JSON
- 一句多事实时拆分输出多条记录
- 保留原文口径

不负责：

- 不负责把 `subject` 映射到标准 `entity`
- 不负责最终判断事实是否通过审核
- 不负责冲突裁决

输出目标：

- 生成稳定、字段完整、可入库的中间结构

---

## 3.3 `Reviewer / Validator`

中文说明：校验审核 Agent。

职责：

- 检查 `Fact Extractor` 的输出是否被 evidence 明确支持
- 检查是否存在主体错配、时间错配、数值错配、单位错配
- 检查是否有明显过度推断
- 输出 `PASS / REJECT / UNCERTAIN`

不负责：

- 不负责重写抽取结果
- 不负责 entity linking
- 不负责 relation build

输出目标：

- 决定该条事实是否可进入 `AUTO_PASS` 或人工审核池

---

## 四、统一 Prompt 设计原则

### 4.1 只做单一任务

每个 Prompt 只做一种任务，不要混合“找证据 + 抽结构 + 审核”。

### 4.2 明确禁止补脑

Prompt 中必须明确写：

- 只抽 evidence 明确支持的信息
- 不允许根据行业常识补全
- 不允许根据标题推断正文未写出的事实
- 不允许假设时间、单位、排序

### 4.3 输出必须是固定 schema

Prompt 中必须限制：

- 输出 JSON
- 不允许输出解释性长文
- 不允许输出 schema 外字段
- `qualifiers` 必须为 JSON 对象

### 4.4 一句多事实必须拆分

Prompt 中必须提醒：

- evidence 可能包含多个事实
- 每个事实单独输出一条记录
- 不允许把多个数值混成一条含糊记录

### 4.5 原文优先，标准化后置

Prompt 中应明确：

- `subject`、`object`、`location` 先保留原始文本
- 不要在抽取阶段做过度标准化
- `entity linking` 在下游单独做

---

## 五、输入输出标准

## 5.1 `Evidence Finder` 输入

建议输入结构：

```json
{
  "document_title": "文章标题",
  "document_source": "来源",
  "document_publish_time": "发布时间",
  "chunk_text": "待分析文本块"
}
```

说明：

- `chunk_text` 是主分析对象
- 标题和时间仅作为辅助上下文
- 不允许根据标题补全 `chunk_text` 没写出的信息

---

## 5.2 `Evidence Finder` 输出

建议输出结构：

```json
{
  "has_fact": true,
  "candidates": [
    {
      "fact_type": "FINANCIAL_METRIC",
      "evidence_text": "2023年主营业务收入同比下降4.5%至4044.8亿元。",
      "reason": "包含明确主体、时间、数值、单位和指标",
      "priority": "high"
    }
  ]
}
```

字段说明：

- `has_fact`：是否存在可抽取事实
- `candidates`：候选 evidence 列表
- `fact_type`：候选事实类型
- `evidence_text`：最小证据文本
- `reason`：命中原因
- `priority`：优先级，如 `high / medium / low`

---

## 5.3 `Fact Extractor` 输入

建议输入结构：

```json
{
  "document_title": "文章标题",
  "document_source": "来源",
  "document_publish_time": "发布时间",
  "fact_type": "FINANCIAL_METRIC",
  "evidence_text": "2023年主营业务收入同比下降4.5%至4044.8亿元。"
}
```

说明：

- `fact_type` 由上游候选结果给定
- `evidence_text` 为最小证据单元
- 模型可以判断 evidence 中包含多条同类型事实，并拆分输出

---

## 5.4 `Fact Extractor` 输出

统一输出数组：

```json
[
  {
    "fact_type": "FINANCIAL_METRIC",
    "subject": "中国涂料工业",
    "predicate": "revenue_in_period",
    "object": null,
    "value_num": 4044.8,
    "value_text": "4044.8亿元",
    "unit": "亿元",
    "currency": "CNY",
    "time_expr": "2023年",
    "location": null,
    "qualifiers": {
      "metric_name": "主营业务收入",
      "yoy": -4.5
    },
    "confidence": 0.96
  }
]
```

---

## 5.5 `Reviewer / Validator` 输入

建议输入结构：

```json
{
  "fact_type": "FINANCIAL_METRIC",
  "evidence_text": "2023年主营业务收入同比下降4.5%至4044.8亿元。",
  "fact_record": {
    "fact_type": "FINANCIAL_METRIC",
    "subject": "中国涂料工业",
    "predicate": "revenue_in_period",
    "object": null,
    "value_num": 4044.8,
    "value_text": "4044.8亿元",
    "unit": "亿元",
    "currency": "CNY",
    "time_expr": "2023年",
    "location": null,
    "qualifiers": {
      "metric_name": "主营业务收入",
      "yoy": -4.5
    },
    "confidence": 0.96
  }
}
```

---

## 5.6 `Reviewer / Validator` 输出

建议输出结构：

```json
{
  "verdict": "PASS",
  "score": 0.95,
  "issues": [],
  "review_note": "字段与 evidence 一致，未发现明显补充推断。"
}
```

可选 `verdict`：

- `PASS`
- `REJECT`
- `UNCERTAIN`

---

## 六、`Evidence Finder` Prompt 规范

## 6.1 目标

从文本块中识别“值得抽取的最小证据片段”。

关键要求：

- 优先找有明确数值、单位、时间、主体的句子
- 允许识别榜单块、名单块
- 不要把整段长文全部丢给下游
- 最好裁出最小 `evidence_text`

---

## 6.2 Prompt 模板

```text
You are an Evidence Finder for an industry intelligence atomization system.

Your task is to read the given text chunk and identify whether it contains extractable structured facts.

A structured fact means the text explicitly states at least one of the following:
- measurable metrics
- output / sales / capacity
- market share / concentration
- investment / expansion
- cooperation / M&A
- policy release / certification
- competitive ranking
- segment trend

Rules:
1. Only use information explicitly stated in the text chunk.
2. Do not infer facts from the title if the chunk itself does not state them.
3. Prefer the smallest evidence span that fully supports a fact.
4. If one sentence contains multiple facts, it can still be one candidate evidence if all facts are tightly coupled.
5. Ignore pure rhetoric, marketing language, and vague commentary unless it clearly expresses a segment trend.
6. Output JSON only.

Return schema:
{
  "has_fact": true or false,
  "candidates": [
    {
      "fact_type": "one candidate type",
      "evidence_text": "minimal supporting text",
      "reason": "why this is extractable",
      "priority": "high|medium|low"
    }
  ]
}
```

---

## 6.3 中文补充说明

Prompt 中的几个关键限制不能删：

- `Only use information explicitly stated`
- `Do not infer facts`
- `Prefer the smallest evidence span`
- `Output JSON only`

这四句本质上决定了后面脏数据率。

---

## 6.4 推荐附加规则

可以在系统层增加一段动态指令：

```text
Known fact_type candidates:
FINANCIAL_METRIC, SALES_VOLUME, CAPACITY, PRICE_CHANGE, INVESTMENT, EXPANSION, NEW_PRODUCT, COOPERATION, MNA, POLICY_RELEASE, CERTIFICATION, MARKET_SHARE, COMPETITIVE_RANKING, SEGMENT_TREND
```

作用：

- 约束模型只在白名单中选类型
- 减少自创类型

---

## 七、`Fact Extractor` Prompt 规范

## 7.1 目标

对单个 `evidence_text` 进行结构化抽取，输出符合 schema 的 JSON 数组。

关键要求：

- 不补脑
- 不标准化实体
- 一句多事实时拆分
- 严格输出字段

---

## 7.2 通用 Prompt 模板

```text
You are a Fact Extractor for an industry intelligence atomization system.

Your task is to extract structured fact records from the given evidence text.

You must follow these rules:
1. Extract only information explicitly supported by the evidence text.
2. Do not infer missing subject, time, unit, scope, ranking, or causal relation.
3. Keep subject, object, and location as raw text from the evidence whenever possible.
4. If the evidence contains multiple independent facts, output multiple records.
5. qualifiers must be a JSON object.
6. If a field is not explicitly supported, set it to null.
7. Output JSON array only. No explanation.

Use this fixed schema for each record:
{
  "fact_type": "string",
  "subject": "string or null",
  "predicate": "string",
  "object": "string or null",
  "value_num": "number or null",
  "value_text": "string or null",
  "unit": "string or null",
  "currency": "string or null",
  "time_expr": "string or null",
  "location": "string or null",
  "qualifiers": {},
  "confidence": "number between 0 and 1"
}
```

---

## 7.3 按 `fact_type` 注入局部指令

通用 Prompt 不够，还需要根据 `fact_type` 注入局部规则。

例如 `MARKET_SHARE`：

```text
Additional rules for MARKET_SHARE:
- market share must not be extracted without a clear market scope.
- CR3/CR5/CR10 should be represented in predicate, such as cr10_in_period.
- top10 combined share and single-company share are different facts.
- put market scope into qualifiers.market_scope if explicitly stated.
```

例如 `COMPETITIVE_RANKING`：

```text
Additional rules for COMPETITIVE_RANKING:
- If the text only says a company is in TOP 10, do not invent exact rank.
- ranking_name, segment, ranking_scope, and ranking_year should go into qualifiers when explicitly stated.
- If no exact rank is given, qualifiers.rank must be null or absent.
```

例如 `SEGMENT_TREND`：

```text
Additional rules for SEGMENT_TREND:
- This is qualitative, not quantitative.
- Preserve the original wording in object or value_text.
- Do not convert vague trend statements into numeric facts.
```

---

## 7.4 中文补充说明

`Fact Extractor` 最容易犯的错有四个：

1. 把标题里的主体硬补进 evidence
2. 把一个句子里的多个事实混在一起
3. 把同比值当主值
4. 把 scope 没写明的市占率硬抽出来

所以这些都要在局部 Prompt 里单独强调。

---

## 八、`Reviewer / Validator` Prompt 规范

## 8.1 目标

对抽取后的结构结果做“证据一致性复核”。

这一步不是摘要，也不是重抽，而是校验。

---

## 8.2 通用 Prompt 模板

```text
You are a Reviewer / Validator for an industry intelligence atomization system.

Your task is to verify whether the extracted fact record is explicitly supported by the evidence text.

You must check:
1. subject consistency
2. predicate appropriateness
3. object consistency
4. value_num and value_text consistency
5. unit consistency
6. time_expr consistency
7. location consistency
8. qualifier validity
9. whether any unsupported inference was added

Rules:
- If the record is clearly supported, return PASS.
- If the record contains unsupported or incorrect fields, return REJECT.
- If the evidence is ambiguous or partially supports the record, return UNCERTAIN.
- Output JSON only.

Return schema:
{
  "verdict": "PASS|REJECT|UNCERTAIN",
  "score": 0.0,
  "issues": [
    {
      "field": "field name",
      "issue": "problem description"
    }
  ],
  "review_note": "short explanation"
}
```

---

## 8.3 推荐追加校验点

对于不同行业资讯，建议重点检查以下问题：

### 金额类
- 是否抽到了单位
- 是否误把同比抽成金额

### 市占率类
- 是否缺少 `market_scope`
- 是否把 top10 share 当成单一企业 share

### 榜单类
- 是否凭空填了 `rank`
- 是否把“入选榜单”说成“排名第一”

### 趋势类
- 是否把定性结论伪装成定量结论

### 时间类
- 是否混淆文章发布时间、榜单年份、经营数据期间

---

## 八、Few-shot 示例建议

结论：

**三 Agent 都建议加少量 few-shot，但不要太多。**
每个 Agent 2~4 个高质量样例足够。

---

## 8.1 `Evidence Finder` 示例

### 正例

输入 chunk：

“2023年我国涂料工业总产量3577.2万吨，同比增长4.5%；表观消费量3566.3万吨，同比增长4.2%。”

输出：

```json
{
  "has_fact": true,
  "candidates": [
    {
      "fact_type": "SALES_VOLUME",
      "evidence_text": "2023年我国涂料工业总产量3577.2万吨，同比增长4.5%；表观消费量3566.3万吨，同比增长4.2%。",
      "reason": "包含明确时间、主体、数值、单位和同比信息",
      "priority": "high"
    }
  ]
}
```

### 反例

输入 chunk：

“不同细分市场的竞争格局呈现明显差异化特征。”

输出：

```json
{
  "has_fact": false,
  "candidates": []
}
```

---

## 8.2 `Fact Extractor` 示例

输入：

- `fact_type`: `SALES_VOLUME`
- `evidence_text`: “2023年我国涂料工业总产量3577.2万吨，同比增长4.5%；表观消费量3566.3万吨，同比增长4.2%。”

输出：

```json
[
  {
    "fact_type": "SALES_VOLUME",
    "subject": "我国涂料工业",
    "predicate": "output_in_period",
    "object": null,
    "value_num": 3577.2,
    "value_text": "3577.2万吨",
    "unit": "万吨",
    "currency": null,
    "time_expr": "2023年",
    "location": "我国",
    "qualifiers": {
      "metric_name": "总产量",
      "yoy": 4.5
    },
    "confidence": 0.96
  },
  {
    "fact_type": "SALES_VOLUME",
    "subject": "我国涂料工业",
    "predicate": "consumption_in_period",
    "object": null,
    "value_num": 3566.3,
    "value_text": "3566.3万吨",
    "unit": "万吨",
    "currency": null,
    "time_expr": "2023年",
    "location": "我国",
    "qualifiers": {
      "metric_name": "表观消费量",
      "yoy": 4.2
    },
    "confidence": 0.95
  }
]
```

---

## 8.3 `Reviewer / Validator` 示例

输入 evidence：

“前十强企业的市占率高达96%。”

输入 fact_record：

```json
{
  "fact_type": "MARKET_SHARE",
  "subject": "某单一企业",
  "predicate": "market_share_in_period",
  "object": null,
  "value_num": 96,
  "value_text": "96%",
  "unit": "%",
  "currency": null,
  "time_expr": null,
  "location": null,
  "qualifiers": {
    "market_scope": "船舶涂料"
  },
  "confidence": 0.9
}
```

输出：

```json
{
  "verdict": "REJECT",
  "score": 0.12,
  "issues": [
    {
      "field": "subject",
      "issue": "evidence refers to top10 companies as a group, not a single company"
    },
    {
      "field": "predicate",
      "issue": "predicate should reflect combined share of top10 rather than single-company market share"
    }
  ],
  "review_note": "主体和谓词都与 evidence 不一致。"
}
```

---

## 九、系统实现建议

## 9.1 Prompt 分层

建议每个 Agent 的 Prompt 由三部分拼接：

1. `system_prompt`
2. `task_prompt`
3. `fact_type_specific_prompt`

结构如下：

```text
system_prompt
+ shared_rules
+ fact_type_specific_rules
+ user_input
```

这样做的好处：

- 共性规则只写一次
- 不同 `fact_type` 的局部差异可单独维护
- 后续改规则不必重写整个 Prompt

---

## 9.2 `qualifiers` 白名单

建议为各 `fact_type` 建立 `qualifiers` 白名单。

例如：

### `FINANCIAL_METRIC`
- `metric_name`
- `segment`
- `yoy`
- `qoq`
- `report_scope`
- `is_forecast`

### `MARKET_SHARE`
- `market_scope`
- `segment`
- `ranking_scope`
- `source_scope`

### `COMPETITIVE_RANKING`
- `ranking_name`
- `ranking_year`
- `segment`
- `rank`
- `ranking_scope`

作用：

- 防止字段乱飞
- 防止同一概念换名字
- 方便后续写入数据库和做统计

---

## 9.3 推荐执行顺序

建议链路如下：

1. 文档切 chunk
2. `Evidence Finder`
3. evidence 去重 / 合并
4. `Fact Extractor`
5. schema 校验
6. `Reviewer / Validator`
7. PASS -> `AUTO_PASS` 或人工池
8. entity linking
9. relation build

注意：

`entity linking` 不应在 `Fact Extractor` 之前做。  
否则容易放大抽取阶段错误。

---

## 十、建议的状态流转

建议使用以下内部状态：

- `RAW_CANDIDATE`
- `EXTRACTED`
- `VALIDATED_PASS`
- `VALIDATED_REJECT`
- `VALIDATED_UNCERTAIN`
- `AUTO_PASS`
- `HUMAN_REVIEW_REQUIRED`

说明：

- `Evidence Finder` 产生 `RAW_CANDIDATE`
- `Fact Extractor` 产生 `EXTRACTED`
- `Reviewer / Validator` 产生 `VALIDATED_*`
- 系统再决定是否自动通过或进入人工审核

---

## 十一、最常见错误与规避策略

### 错误 1：标题补脑
规避：
在 Prompt 明确禁止用标题补正文未写出的信息。

### 错误 2：一句只抽一条
规避：
在 Prompt 明确要求多事实拆分。

### 错误 3：同比值和主值混淆
规避：
要求把同比放进 `qualifiers.yoy`，不要覆盖 `value_num`。

### 错误 4：市占率无 scope
规避：
`MARKET_SHARE` 的局部 Prompt 强制要求 scope。

### 错误 5：榜单类伪造位次
规避：
`COMPETITIVE_RANKING` 局部 Prompt 明确禁止无依据填 rank。

### 错误 6：把定性趋势当定量事实
规避：
将 `SEGMENT_TREND` 单独处理。

---

## 十二、最终建议

当前阶段最合理的做法是：

**先把三 Agent Prompt 固化成模板文件，再拿 20~30 篇典型文章做小样本调试。**

不要一上来追求“全自动正确”，而要先验证三件事：

1. `Evidence Finder` 能否把高价值句子筛出来
2. `Fact Extractor` 能否稳定按 schema 输出
3. `Reviewer / Validator` 能否把明显脏数据挡住

这三关打通后，后面的 ORM、接口、审核页面、时间轴分析才有价值。

建议本文档文件名为：

`三 Agent 抽取 Prompt 规范.md`
