# 资讯颗粒化项目 MVP 技术规格说明书（Spec）

---

## 一、项目概述

### 1.1 项目名称
资讯颗粒化收集系统 —— 最小 MVP

### 1.2 项目目标
从行业资讯文本中自动抽取结构化"事实原子"（fact_atom），并提供审核与查询能力。

### 1.3 核心链路
```
source_document → document_chunk / document_sentence → evidence_span → fact_atom → review → query
```

### 1.4 不做的事
- Zep 图谱抽取 / Neo4j / GraphRAG
- 自动 ontology 生成
- 多跳关系推理
- 复杂前端 / 多用户权限
- 全量网页爬虫
- 高级实体消歧引擎
- 自动化定时任务编排

---

## 二、功能规格

### 2.1 输入规格

| 项目 | 规格 |
|------|------|
| 输入方式 | 手工粘贴全文 / 导入 txt / md 文件 |
| 元数据 | 标题（必填）、来源（可选）、发布时间（可选）、URL（可选） |
| 文本语言 | 中文为主，允许中英文混合 |
| 文档去重 | 基于 `content_hash`（SHA-256） |

### 2.2 文本切分规格

| 项目 | 规格 |
|------|------|
| 切分层级 | 四级：文档级 → 章节级 → 段落级 → 句群级 |
| chunk 大小 | 理想 500~900 中文字，上限 1200~1500 |
| 最小块 | 300 字（低于 200 字不单独送 LLM） |
| overlap | 50~120 中文字，不超过整块 20% |
| 短文直通 | ≤1200 字整篇送抽取，不切分 |
| 断句标点 | 优先：`。；：！？`；次优：`，`（仅极长句） |
| chunk 元数据 | doc_id, chunk_id, title, section, position, text |

### 2.3 三 Agent 处理链路

#### Agent 1：Evidence Finder
| 项目 | 规格 |
|------|------|
| 输入 | chunk_text + 文档标题/来源/发布时间 |
| 输出 | `{ has_fact, candidates: [{ fact_type, evidence_text, reason, priority }] }` |
| 模型要求 | 便宜、稳定即可 |
| 核心约束 | 只用 chunk 内明确信息，禁止标题补脑，裁最小 evidence_span |

#### Agent 2：Fact Extractor
| 项目 | 规格 |
|------|------|
| 输入 | evidence_text + 候选 fact_type + 文档元数据 |
| 输出 | JSON 数组，每条含 fact_type/subject/predicate/object/value_num/value_text/unit/currency/time_expr/location/qualifiers/confidence |
| 模型要求 | 质量优先 |
| 核心约束 | 一句多事实必须拆分，禁止补脑，qualifiers 必须为 JSON 对象，未明确字段设 null |

#### Agent 3：Reviewer / Validator
| 项目 | 规格 |
|------|------|
| 输入 | evidence_text + fact_record |
| 输出 | `{ verdict: PASS/REJECT/UNCERTAIN, score, issues: [{ field, issue }], review_note }` |
| 模型要求 | 与 Extractor 同级或略低 |
| 核心约束 | 只做校验不重写，检查主体/时间/数值/单位/scope 一致性 |

### 2.4 实体标准化规格

| 项目 | 规格 |
|------|------|
| 匹配策略 | 精确匹配 canonical_name → 精确匹配 alias_name → 未命中保留原文 |
| 消歧能力 | MVP 不做复杂消歧，只做最常见公司名/品牌名统一 |
| 处理时机 | 在 Fact Extractor 之后，不在抽取阶段做标准化 |

### 2.5 审核规格

#### 自动通过条件（全部满足）
- Reviewer verdict = PASS
- fact_type 在白名单中
- subject 非空
- predicate 非空
- evidence_span 非空
- 数值类事实 value_text 非空

#### 强制人工审核
- verdict = UNCERTAIN
- 金额类事实
- MARKET_SHARE 类事实
- COMPETITIVE_RANKING 类事实
- 同一句多事实
- 缺少时间口径
- 主体明显歧义

#### 驳回处理
- REJECT 记录不入正式库，保留日志用于调 Prompt

### 2.6 查询规格

| 查询类型 | 说明 |
|----------|------|
| 按 subject 查事实 | 输入主体名称，返回所有关联 fact_atom |
| 按 fact_type 查事实 | 输入事实类型，返回对应事实列表 |
| 按时间范围查事实 | 输入起止时间，返回范围内事实 |
| 按文档查事实 | 输入文档 ID，返回该文档所有抽取结果 |
| 查看 evidence | 输入 fact_atom ID，返回原始 evidence_span |

---

## 三、数据模型规格

### 3.1 表清单

| 表名 | 说明 | MVP 必做 |
|------|------|----------|
| `source_document` | 原始文档表 | ✅ |
| `document_chunk` | 块级切分表 | ✅ |
| `document_sentence` | 句级切分表 | ✅ |
| `evidence_span` | 证据片段表 | ✅ |
| `fact_atom` | 事实原子表（核心） | ✅ |
| `entity` | 标准实体表 | ✅ |
| `entity_alias` | 实体别名表 | ✅ |
| `review_log` | 审核日志表 | ✅ |
| `extraction_task` | 抽取任务表 | ⚠️ 简化版 |
| `relation` | 关系表 | ❌ 暂不做 |
| `conflict_case` | 冲突记录表 | ❌ 暂不做 |

### 3.2 fact_atom 核心字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | TEXT PK | ✅ | UUID |
| document_id | TEXT FK | ✅ | 来源文档 |
| evidence_span_id | TEXT FK | ✅ | 证据片段 |
| fact_type | TEXT | ✅ | 事实类型 |
| subject_entity_id | TEXT FK | ⚠️ | 主体实体（可后填） |
| predicate | TEXT | ✅ | 谓词 |
| object_entity_id | TEXT FK | ⚠️ | 对象实体 |
| object_text | TEXT | ⚠️ | 对象原始文本 |
| value_num | REAL | ⚠️ | 数值 |
| value_text | TEXT | ⚠️ | 原始数值文本 |
| unit | TEXT | ⚠️ | 单位 |
| currency | TEXT | ⚠️ | 币种 |
| time_expr | TEXT | ⚠️ | 原始时间表达 |
| time_start | TEXT | ⚠️ | 标准化开始时间 |
| time_end | TEXT | ⚠️ | 标准化结束时间 |
| location_entity_id | TEXT FK | ⚠️ | 地点实体 |
| qualifier_json | TEXT | ⚠️ | JSON 附加限定 |
| confidence_score | REAL | ⚠️ | 置信度 0~1 |
| extraction_model | TEXT | ⚠️ | 抽取模型名 |
| review_status | TEXT | ✅ | PENDING/AUTO_PASS/HUMAN_PASS/REJECTED/UNCERTAIN |
| created_at | TIMESTAMP | ✅ | 创建时间 |

### 3.3 数据库选型

| 阶段 | 选型 | 理由 |
|------|------|------|
| MVP | SQLite | 轻量、部署简单、便于调试 |
| 后续 | PostgreSQL | 生产级切换 |

---

## 四、MVP fact_type 范围

### 第一批必做（7 类）

| fact_type | 中文 | 代表 predicate |
|-----------|------|----------------|
| FINANCIAL_METRIC | 财务指标 | revenue_in_period, profit_in_period, gross_margin_in_period |
| SALES_VOLUME | 产量/销量 | output_in_period, sales_in_period, consumption_in_period |
| CAPACITY | 产能 | capacity_in_period, new_capacity_in_period |
| INVESTMENT | 投资 | invested_in, investment_in_period |
| EXPANSION | 扩产/扩建 | expanded_in, built_new_plant, launched_project |
| MARKET_SHARE | 市场份额 | market_share_in_period, cr10_in_period |
| COMPETITIVE_RANKING | 竞争排名 | ranked_in, listed_in |

### 第二批延后（7 类）
PRICE_CHANGE, COOPERATION, MNA, POLICY_RELEASE, CERTIFICATION, SEGMENT_TREND, NEW_PRODUCT

---

## 五、Prompt 分层架构

```
system_prompt（角色定义 + 通用规则）
  + shared_rules（禁止补脑、JSON only、一句多事实拆分）
  + fact_type_specific_rules（按类型注入局部规则）
  + user_input（实际输入数据）
```

每个 Agent 2~4 个 few-shot 示例。

---

## 六、状态流转

```
Evidence Finder → RAW_CANDIDATE
Fact Extractor  → EXTRACTED
Reviewer        → VALIDATED_PASS / VALIDATED_REJECT / VALIDATED_UNCERTAIN
系统规则判定     → AUTO_PASS / HUMAN_REVIEW_REQUIRED
人工审核         → HUMAN_PASS / REJECTED
```

---

## 七、技术实现规格

| 项目 | 规格 |
|------|------|
| 语言 | Python |
| 后端形态 | 第一版脚本型 MVP，后续可选包 FastAPI |
| 数据库 | SQLite（mvp.db） |
| 模型调用 | Evidence Finder 用便宜模型，Fact Extractor 用高质量模型，Reviewer 同级或略低 |
| 输出格式 | 所有 Agent 输出纯 JSON，不允许解释性文本 |

---

## 八、项目目录结构

```
project_root/
├─ app/
│  ├─ models/          # ORM / 数据模型
│  │  ├─ db.py
│  │  ├─ source_document.py
│  │  ├─ evidence.py
│  │  ├─ fact_atom.py
│  │  └─ entity.py
│  ├─ services/        # 业务逻辑
│  │  ├─ text_splitter.py
│  │  ├─ evidence_finder.py
│  │  ├─ fact_extractor.py
│  │  ├─ reviewer.py
│  │  ├─ entity_linker.py
│  │  └─ repository.py
│  ├─ prompts/         # Prompt 模板
│  │  ├─ evidence_finder.txt
│  │  ├─ fact_extractor_common.txt
│  │  ├─ reviewer.txt
│  │  └─ fact_type_rules/
│  │     ├─ financial_metric.txt
│  │     ├─ sales_volume.txt
│  │     ├─ capacity.txt
│  │     ├─ investment.txt
│  │     ├─ expansion.txt
│  │     ├─ market_share.txt
│  │     └─ competitive_ranking.txt
│  └─ main.py
├─ data/
│  └─ mvp.db
├─ docs/
│  └─ 方案文档
└─ tests/
```

---

## 九、里程碑与验收标准

### Milestone 1：单篇打通
- 手工输入 1 篇文章 → 切分 → evidence → 5~20 条 fact_atom
- 验收：≥70% 高价值事实被抽出，数值/单位/时间无明显错配

### Milestone 2：10 篇小样本
- 10 篇不同类型文章，覆盖 7 个 fact_type
- 验收：Evidence Finder 不漏高价值句，Extractor schema 稳定，Reviewer 挡住明显错误

### Milestone 3：可查询原型
- 支持按 subject / fact_type / 时间 / 文档查询，evidence 回溯
- 验收：人工验证结果可用，数据具备基本分析价值

### 最终验收 6 条标准
1. 能稳定导入文档
2. 能稳定切分 chunk / sentence
3. 能抽出候选 evidence
4. 能按 schema 生成 fact_atom
5. 能对结果做基础审核
6. 能查询并回看 evidence

---

## 十、开发顺序

| 步骤 | 内容 |
|------|------|
| 1 | 建 SQLite 表（9 张） |
| 2 | 实现文本切分器 |
| 3 | 实现 Evidence Finder |
| 4 | 实现 Fact Extractor |
| 5 | 实现 Reviewer / Validator |
| 6 | 实现最轻量 entity 匹配 |
| 7 | 实现查询导出 |
