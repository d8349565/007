# 数据库审查报告

**审查日期**：2026-03-18
**数据库**：F:/Python/007/data/mvp.db

---

## 一、数据概况

| 表名 | 记录数 | 状态 |
|------|--------|------|
| source_document | 1 | 正常 |
| document_chunk | 5 | 正常 |
| evidence_span | 30 | 正常 |
| fact_atom | 56 | **需关注** |
| entity | 0 | **未使用** |
| entity_alias | 0 | **未使用** |
| review_log | 54 | 正常 |
| extraction_task | 84 | 正常 |

### fact_atom 审核状态分布

| 状态 | 数量 | 占比 |
|------|------|------|
| HUMAN_REVIEW_REQUIRED | 34 | 60.7% |
| AUTO_PASS | 10 | 17.9% |
| REJECTED | 10 | 17.9% |
| UNCERTAIN | 2 | 3.5% |

---

## 二、问题汇总

### 问题 1：Predicate 白名单与实际提取结果不匹配

**严重程度**：高

**问题描述**：

config.yaml 中定义的 predicate 白名单采用"词根"形式，而 LLM 实际提取的是"完整短语"，两者语义未对齐。

**详细对比**：

| fact_type | 白名单配置 | LLM 实际输出 | 是否匹配 |
|-----------|-----------|-------------|---------|
| COMPETITIVE_RANKING | `排名第一、位居第、跻身前十、连续排名第一、入选排行榜` | `ranked_in_top10` | ❌ 英文，旧格式 |
| FINANCIAL_METRIC | `实现、销售收入、新增销售收入、累计销售收入、收入为` | `销售收入为`、`销售额为`、`综合销售额为`、`营业收入为` | ❌ 不在白名单 |
| FINANCIAL_METRIC | `新增销售收入` | `销售收入新增` | ❌ 词序相反 |
| MARKET_SHARE | `占全国市场份额` | `占全球市场份额` | ❌ 白名单无"全球" |
| MARKET_SHARE | `市场份额减少` | `市场份额较上一年减少` | ❌ 多了修饰语 |
| SALES_VOLUME | `销售量为、产量为` | `为` | ❌ 太笼统 |
| CAPACITY | `规划、新增年产、产能为` | `规划产能为、新增年产能` | ❌ 短语组合不在白名单 |

**根本原因**：

白名单设计为"动词词根"（如 `销售收入`），期望 LLM 输出的 predicate 也直接使用这些词根。但实际提取时 LLM 将词根与主语/宾语组合成完整短语（如 `销售收入为`），导致不匹配。

---

### 问题 2：Subject 质量问题

**严重程度**：高

**问题描述**：

部分 fact_atom 的 subject 不是具体实体名称，而是度量名称、业务描述或过于宽泛的类别。

**问题示例**：

| subject_text | 实际应为 |
|-------------|---------|
| `全国造船完工量` | 度量，非实体 |
| `手持订单量` | 度量，非实体 |
| `新接订单量` | 度量，非实体 |
| `中国船舶涂料市场` | 市场区域，非具体实体 |
| `船舶涂料品牌` | 类别名称，太宽泛 |
| `公司船舶涂料业务` | 业务描述，非实体 |
| `其他涂料业务（含船舶涂料、汽车修补漆等）` | 业务范围描述 |

**不符合项目目标**：

项目目标是提取关于**具体企业/实体**的事实，而非度量或业务分类。

---

### 问题 3：Entity 实体解析未实现

**严重程度**：中

**问题描述**：

`entity` 表和 `entity_alias` 表为空（0 条记录），`fact_atom` 表的 `subject_entity_id` 和 `object_entity_id` 字段永远为 NULL。

**影响**：

- 无法实现实体标准化（"立邦"和"立邦涂料"应识别为同一实体）
- 无法实现跨文档实体关联查询
- 实体消歧功能缺失

---

### 问题 4：Schema 缺少唯一约束

**严重程度**：中

**问题描述**：

`evidence_span` 表没有对 `(document_id, fact_type, evidence_text)` 建立 UNIQUE 约束。

**影响**：

- 代码层去重逻辑存在并发竞态条件（`SELECT ... LIMIT 1` 后 `INSERT` 非原子操作）
- 数据库层面无法防止重复插入

---

### 问题 5：Schema 字段不一致

**严重程度**：低

**问题描述**：

`evidence_span` 表缺少 `created_at` 字段，与 `fact_atom`、`source_document` 等表不一致。

---

## 三、建议

### 建议 1：扩展 predicate 白名单为完整短语

**方案 A**：将白名单从词根改为完整短语（推荐）

```yaml
predicate_whitelist:
  FINANCIAL_METRIC:
    - 销售收入为
    - 销售额为
    - 综合销售额为
    - 营业收入为
    - 销售收入新增
    - 销售收入增长
    - 销售收入增加
    # ... 扩展所有实际出现的变体
```

**方案 B**：修改 LLM 提示词，约束输出为词根形式

在 prompt 中明确要求 predicate 只能是白名单中的词根，不得添加后缀。

---

### 建议 2：加强 subject 校验逻辑

在 `fact_extractor.py` 中增加 subject 合理性校验：

- subject 长度应在合理范围内（如 2-50 字符）
- subject 不应为纯度量词（如"销售量"、"产量"、"订单量"）
- subject 应能在证据文本中找到对应的实体指代

---

### 建议 3：实现 entity 实体解析

两条路径：

**路径 A（简单）**：启用后置实体标准化
- 在 fact_atom 写入后，根据 subject_text 查 entity 表
- 如存在匹配则回填 entity_id
- 需预先维护 entity 基础数据

**路径 B（完整）**：实现端到端实体 pipeline
- evidence_finder 阶段输出时做实体标注
- fact_extractor 阶段利用实体信息填充 entity_id
- entity_alias 表建立同义词映射

---

### 建议 4：添加数据库唯一约束

```sql
CREATE UNIQUE INDEX idx_evidence_span_dedup
    ON evidence_span(document_id, fact_type, evidence_text);
```

或在 `evidence_span` 表添加复合唯一键。

---

### 建议 5：统一表结构

为 `evidence_span` 表添加 `created_at` 字段：

```sql
ALTER TABLE evidence_span
    ADD COLUMN created_at TEXT NOT NULL DEFAULT (datetime('now'));
```

---

## 四、设计亮点

以下设计是合理的，应保持：

1. **一个 evidence_span 对应多条 fact_atom**：符合"一个原子事实 = 一条记录"原则，如"立邦、中涂化工...位居第4-10位"拆分为7条记录是正确的。

2. **review_status 状态机**：状态流转清晰（AUTO_PASS、HUMAN_REVIEW_REQUIRED、HUMAN_PASS、REJECTED）。

3. **extraction_task 记录模型**：任务级追踪（evidence_finder → fact_extractor → reviewer）完整。

4. **review_log 审计表**：所有状态变更均有日志，可追溯。

---

## 五、优先级排序

| 优先级 | 问题 | 建议 |
|--------|------|------|
| P0 | predicate 白名单不匹配 | 扩展白名单或调整 prompt |
| P0 | subject 质量差 | 增加校验，拒绝度量词作为 subject |
| P1 | entity 表为空 | 规划实体解析方案并实施 |
| P1 | evidence_span 无唯一约束 | 添加 UNIQUE 索引 |
| P2 | evidence_span 缺 created_at | ALTER TABLE 添加字段 |
