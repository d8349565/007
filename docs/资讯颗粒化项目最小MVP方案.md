# 资讯颗粒化项目最小 MVP 方案

## 一、文档定位

本文档基于以下三份已有文档整理而成，目标是给出一套**可直接开工、范围收敛、实现成本可控**的最小 MVP 方案：

- `资讯颗粒化项目开发目标与实施方案.md`
- `fact_type 字典与抽取字段规范.md`
- `三 Agent 抽取 Prompt 规范.md`

本文档只回答一个问题：

**第一版最小 MVP 到底做什么，不做什么，怎么做，做到什么程度算完成。**

---

## 二、MVP 结论

结论很明确：

**最小 MVP 不做重型知识图谱，不做 Zep 关系抽取，不做 GraphRAG，不做复杂前端。**

最小 MVP 只做这条主链路：

`source_document → document_chunk / document_sentence → evidence_span → fact_atom → review → query`

也就是说，第一版的核心目标只有三个：

1. **把文章中的高价值证据句筛出来**
2. **把证据句抽成结构化 `fact_atom`**
3. **让这些事实可以被审核、查询、回溯**

只要这三件事跑通，这个项目就已经成立。

---

## 三、MVP 的目标边界

## 3.1 业务目标

最小 MVP 要解决的不是“生成摘要”，而是“沉淀事实原子”。

第一版应该支持：

- 从一篇行业资讯中抽出高价值事实
- 保留原始 evidence
- 保存事实类型、主体、谓词、对象、数值、单位、时间、地点、附加限定信息
- 对抽取结果做基础校验
- 在数据库中查询某主体、某时间、某指标的历史事实

---

## 3.2 技术目标

最小 MVP 技术上只需要证明以下几点：

- 三 Agent 链路能跑通
- `fact_type` 模板稳定可用
- `fact_atom` 可入库
- evidence 可回溯
- 审核闭环能挡住明显脏数据

---

## 3.3 暂时不做的内容

以下内容明确排除出 MVP 范围：

- Zep 图谱抽取
- 自动 ontology 生成
- Neo4j / 图数据库接入
- GraphRAG
- 多跳关系推理
- 自动关系网络分析
- 复杂前端系统
- 多用户权限系统
- 自动化定时任务编排
- 全量网页爬虫平台
- 高级实体消歧引擎

说明：

这些内容不是没价值，而是**现在做会分散精力、放大不确定性**。

---

## 四、MVP 设计原则

## 4.1 `fact_atom` 是核心资产

最小 MVP 的主资产不是图，不是 embedding，不是关系边，而是 `fact_atom`。

原因：

- 查询靠它
- 时间轴靠它
- 审核靠它
- 后续关系层也从它长出来

---

## 4.2 evidence 必须是一等公民

每条事实都必须能回到原始证据：

- 哪篇文档
- 哪个 chunk
- 哪个 sentence
- 哪个 evidence_span

没有 evidence 的事实，默认不可信。

---

## 4.3 先结构化，后关系化

最小 MVP 只做结构化事实，不做复杂关系推理。

如果后面需要关系，先从 `fact_atom` 派生，而不是反过来直接从全文建图。

---

## 4.4 优先做高价值场景

第一版不需要覆盖所有行业资讯。

优先做最容易抽、最有价值的事实：

- 财务指标
- 产量 / 销量
- 产能
- 市占率
- 投资
- 扩产
- 榜单入选

---

## 五、MVP 功能范围

## 5.1 输入范围

第一版建议只支持**手工导入文章文本**，不要一开始接复杂爬虫。

支持输入方式：

- 手工粘贴文章全文
- 导入 txt / md
- 可选：保存 URL、标题、来源、发布时间

这样就够验证系统链路。

---

## 5.2 输出范围

第一版输出只需要三类结果：

### A. 文档级结果
- 文档已入库
- 切分完成
- 有多少候选 evidence

### B. 事实级结果
- 抽出多少条 `fact_atom`
- 每条事实的结构化字段
- 每条事实的 evidence

### C. 审核级结果
- 哪些记录 `PASS`
- 哪些记录 `REJECT`
- 哪些记录 `UNCERTAIN`

---

## 5.3 查询范围

第一版不需要复杂 BI，只要支持以下基础查询：

- 按 `subject` 查询事实
- 按 `fact_type` 查询事实
- 按时间范围查询事实
- 查看某条事实对应 evidence
- 查看某篇文章抽出了哪些事实

这已经足够验证业务价值。

---

## 六、MVP 推荐 `fact_type` 范围

虽然完整字典里有 12+2 类，但最小 MVP 建议只先落 7 类。

## 第一批必做

- `FINANCIAL_METRIC`
- `SALES_VOLUME`
- `CAPACITY`
- `INVESTMENT`
- `EXPANSION`
- `MARKET_SHARE`
- `COMPETITIVE_RANKING`

这 7 类覆盖了大多数行业资讯中最有价值的数据点。

## 第二批再做

- `PRICE_CHANGE`
- `COOPERATION`
- `MNA`
- `POLICY_RELEASE`
- `CERTIFICATION`
- `SEGMENT_TREND`
- `NEW_PRODUCT`

原因：

第一批更偏硬数据，适合先做稳定性验证。  
第二批歧义更多，适合后续扩展。

---

## 七、MVP 数据表范围

## 7.1 必做表

最小 MVP 必做以下 8 张表：

### `source_document`
原始文档表。

### `document_chunk`
块级切分表。

### `document_sentence`
句级切分表。

### `evidence_span`
证据片段表。

### `fact_atom`
事实原子表。

### `entity`
标准实体表。

### `entity_alias`
实体别名表。

### `review_log`
审核日志表。

---

## 7.2 建议保留但可简化

### `extraction_task`
建议保留，但字段可以先简化，只记录：

- `id`
- `document_id`
- `task_type`
- `model_name`
- `status`
- `started_at`
- `finished_at`
- `error_message`

这样后期方便排查。

---

## 7.3 暂时不做的表

以下表可以先不落：

### `relation`
先不做。需要时再由 `fact_atom` 派生。

### `conflict_case`
第一版可以先用简单 SQL 查重 / 查冲突，不一定单独建表。

---

## 八、MVP 三 Agent 处理链路

最小 MVP 按以下顺序执行。

## Step 1：文档入库

输入：

- 标题
- 来源
- 发布时间
- 原始正文

输出：

- `source_document`

说明：

第一版可以手工录入，不强求自动抓取。

---

## Step 2：文本切分

处理：

- 按段切 `document_chunk`
- 按句切 `document_sentence`

输出：

- `document_chunk`
- `document_sentence`

说明：

这一步只做基础切分，不追求特别复杂的 NLP 断句能力。

---

## Step 3：`Evidence Finder`

输入：

- `chunk_text`
- 文档标题、来源、发布时间

输出：

- 候选 `evidence_span`
- 候选 `fact_type`

目标：

- 只把最值得抽的 evidence 往下传
- 避免全文直接丢给 `Fact Extractor`

---

## Step 4：`Fact Extractor`

输入：

- `evidence_text`
- 候选 `fact_type`

输出：

- 一条或多条 `fact_atom` 中间结构 JSON

要求：

- 严格遵守 `fact_type` 字典
- 一句多事实必须拆分
- 不做 entity 标准化
- `qualifiers` 必须是 JSON 对象

---

## Step 5：`Reviewer / Validator`

输入：

- `evidence_text`
- `fact_record`

输出：

- `PASS`
- `REJECT`
- `UNCERTAIN`

处理逻辑：

- `PASS`：可自动入库
- `REJECT`：丢弃或人工查看
- `UNCERTAIN`：进入人工审核池

---

## Step 6：实体标准化

第一版建议做最轻量版本：

1. 精确匹配 `entity.canonical_name`
2. 精确匹配 `entity_alias.alias_name`
3. 未命中则先保留原始文本，不强行消歧

说明：

不要一开始做太复杂的 entity linking。  
最小 MVP 只要能把最常见公司名、品牌名统一起来就够了。

---

## Step 7：入库与查询

通过审核的事实写入：

- `fact_atom`

并支持最小查询：

- 按文档查事实
- 按主体查事实
- 按 `fact_type` 查事实
- 看 evidence 原文

---

## 九、MVP 审核机制

## 9.1 自动通过条件

第一版可以用最简单规则：

满足以下条件则自动通过：

- `Reviewer / Validator = PASS`
- `fact_type` 在白名单中
- `subject` 非空
- `predicate` 非空
- `evidence_span` 非空
- 对于数值类事实，`value_text` 非空

---

## 9.2 人工审核池

以下情况直接进入人工池：

- `UNCERTAIN`
- 金额类事实
- `MARKET_SHARE`
- `COMPETITIVE_RANKING`
- 同一句多事实
- 缺少时间口径
- 主体明显歧义

说明：

第一版人工审核可以很原始，甚至就是一个简单表格界面或命令行导出。

---

## 9.3 驳回机制

`REJECT` 的记录不入正式库，但建议保留日志，便于后面调 Prompt。

---

## 十、MVP 技术实现建议

## 10.1 数据库

首选：

- `SQLite`

原因：

- 足够轻
- 部署简单
- 便于调试
- 非常适合最小原型

后续再切：

- `PostgreSQL`

---

## 10.2 后端形态

最小 MVP 推荐两种实现方式之一：

### 方案 A：脚本型 MVP
用 Python 脚本直接跑：

- 文档导入
- 切分
- 三 Agent 调用
- 入库
- 查询导出

优点：

- 最快
- 最适合验证链路

### 方案 B：轻 API MVP
用 `FastAPI` 或 `Flask` 做几个接口：

- 导入文档
- 执行抽取
- 查看结果
- 查询事实

优点：

- 更像产品
- 后续方便接前端

结论：

**第一版建议先做脚本型 MVP，再视情况包一层轻 API。**

---

## 10.3 模型调用策略

建议：

- `Evidence Finder`：用便宜但稳定的模型
- `Fact Extractor`：用质量更高的模型
- `Reviewer / Validator`：可与 `Fact Extractor` 同级或略低一级

原因：

- `Fact Extractor` 决定结构质量
- `Reviewer / Validator` 决定脏数据率
- `Evidence Finder` 成本最低即可

---

## 十一、MVP 文件与模块结构建议

推荐项目目录：

```text
project_root/
├─ app/
│  ├─ models/
│  │  ├─ db.py
│  │  ├─ source_document.py
│  │  ├─ evidence.py
│  │  ├─ fact_atom.py
│  │  └─ entity.py
│  ├─ services/
│  │  ├─ text_splitter.py
│  │  ├─ evidence_finder.py
│  │  ├─ fact_extractor.py
│  │  ├─ reviewer.py
│  │  ├─ entity_linker.py
│  │  └─ repository.py
│  ├─ prompts/
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

## 十二、MVP 里程碑

## Milestone 1：单篇文章打通链路

目标：

- 手工输入 1 篇文章
- 成功切分
- 成功找出 evidence
- 成功抽出 5~20 条 `fact_atom`
- 能看 evidence 和抽取结果

验收标准：

- 至少 70% 的高价值事实被抽出
- 主要数值、单位、时间没有明显错配

---

## Milestone 2：10 篇文章小样本验证

目标：

- 用 10 篇不同类型文章测试
- 覆盖 7 个 `fact_type`
- 观察错误模式

验收标准：

- `Evidence Finder` 不漏掉大部分高价值句
- `Fact Extractor` 输出 schema 基本稳定
- `Reviewer / Validator` 能挡掉明显错误

---

## Milestone 3：可查询原型

目标：

- 查询某主体的所有事实
- 查询某篇文档抽出的所有事实
- 查询某个 `fact_type` 的历史事实

验收标准：

- 可以人工验证结果可用
- evidence 回溯顺畅
- 数据具备基本分析价值

---

## 十三、MVP 验收标准

最小 MVP 是否成立，不看界面炫不炫，只看这 6 条：

1. 能稳定导入文档
2. 能稳定切分 chunk / sentence
3. 能抽出候选 evidence
4. 能按 schema 生成 `fact_atom`
5. 能对结果做基础审核
6. 能查询并回看 evidence

如果这 6 条成立，MVP 就算成功。

---

## 十四、最小 MVP 开发顺序

建议严格按这个顺序，不要跳。

### 第 1 步
建 SQLite 表：

- `source_document`
- `document_chunk`
- `document_sentence`
- `evidence_span`
- `fact_atom`
- `entity`
- `entity_alias`
- `review_log`
- 可选：`extraction_task`

### 第 2 步
实现文本切分器。

### 第 3 步
实现 `Evidence Finder`。

### 第 4 步
实现 `Fact Extractor`。

### 第 5 步
实现 `Reviewer / Validator`。

### 第 6 步
实现最轻量 entity 匹配。

### 第 7 步
实现查询导出。

---

## 十五、我对这个 MVP 的明确建议

最小 MVP 的正确姿势不是“做大而全”，而是：

**拿 10 篇典型行业文章，沉淀出一批干净、可审、可查的 `fact_atom`。**

只要这一点做出来，你后面就可以继续长：

- ORM
- API
- 审核页
- 时间轴
- 关系层
- 图谱层

但在 MVP 阶段，**别提前做关系网络，不要碰 Zep，不要碰图数据库。**
先把事实底座做稳。

---

## 十六、下一步建议

在这份 MVP 方案之后，最适合继续补的是两份实现文档：

1. `Python ORM 模型设计.md`
2. `抽取链路伪代码与任务流.md`

这两份一出来，你基本就可以正式开写代码了。
