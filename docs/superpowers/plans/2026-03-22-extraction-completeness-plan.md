# 事实抽取完整度提升实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提升事实抽取完整度，目标单文档 20-30 条重要事实（当前 3-5 条）

**Architecture:** 扩展 predicate 白名单 + 增强 extraction prompt 数量引导 + 调整审核分流阈值

**Tech Stack:** YAML 配置、Prompt 文本修改

---

## 文件修改清单

| 文件 | 改动类型 |
|------|---------|
| `config.yaml` | 修改：predicate 白名单扩展 + 审核配置 |
| `app/prompts/fact_extractor_full.txt` | 修改：增加 quantity expectations |

---

## Task 1: 扩展 config.yaml Predicate 白名单

**Files:**
- Modify: `config.yaml:79-175`（predicate_whitelist 部分）
- Modify: `config.yaml:265-277`（review 配置部分）

- [ ] **Step 1: 读取当前 config.yaml predicate_whitelist 结构**

确认 MARKET_SHARE、FINANCIAL_METRIC、CAPACITY、COOPERATION、SALES_VOLUME 各节位置

- [ ] **Step 2: 扩展 MARKET_SHARE predicate 白名单**

在 `config.yaml` 的 `predicate_whitelist.MARKET_SHARE` 列表中新增：
```yaml
- 市占率达到
- 市场份额达
```

- [ ] **Step 3: 扩展 FINANCIAL_METRIC predicate 白名单**

在 `predicate_whitelist.FINANCIAL_METRIC` 列表中新增：
```yaml
- 实现
- 综合销售额
```

- [ ] **Step 4: 扩展 CAPACITY predicate 白名单**

在 `predicate_whitelist.CAPACITY` 列表中新增：
```yaml
- 产能为
- 设计产能为
```

- [ ] **Step 5: 扩展 COOPERATION predicate 白名单**

在 `predicate_whitelist.COOPERATION` 列表中新增：
```yaml
- 击败
- 提供涂料
- 应用于
- 供货于
```

- [ ] **Step 6: 扩展 SALES_VOLUME predicate 白名单**

在 `predicate_whitelist.SALES_VOLUME` 列表中新增：
```yaml
- 消耗
```

- [ ] **Step 7: 调整审核配置**

修改 `config.yaml` 的 `review` 部分：
```yaml
review:
  auto_pass_confidence: 0.85
  force_human_review_types: []
```

- [ ] **Step 8: 验证 config.yaml YAML 语法**

Run: `python -c "import yaml; yaml.safe_load(open('config.yaml'))"`
Expected: 无报错

- [ ] **Step 9: 提交**

```bash
git add config.yaml
git commit -m "feat(config): 扩展 predicate 白名单并调整审核阈值"
```

---

## Task 2: 修改 Extraction Prompt 增加数量引导

**Files:**
- Modify: `app/prompts/fact_extractor_full.txt:7-18`（What to extract 之后）

- [ ] **Step 1: 读取当前 fact_extractor_full.txt 前 20 行**

确认 "What to extract" 部分的位置

- [ ] **Step 2: 在 "What to extract" 之后插入 quantity expectations**

在 `## What to extract` 部分之后、`## Output format` 之前插入：
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

- [ ] **Step 3: 提交**

```bash
git add app/prompts/fact_extractor_full.txt
git commit -m "feat(prompt): 增加 fact_type 数量引导规则"
```

---

## Task 3: 验证改进效果

**Files:**
- Test: `data/mvp.db`（现有数据库）
- Test: `app/services/pipeline.py`

- [ ] **Step 1: 建立基准 — 记录当前每文档事实数**

选取 3 篇文档作为测试集：
```bash
# 文档1: 35344020-b2ec-426c-aa3c-629241667cb6 (佐敦EOLMED项目)
# 文档2: 203bc029-e29d-42d5-b2c7-9abfa010cb5d (佐敦项目入选省重大项目)
# 文档3: 557aa9ca-7322-4be2-b57b-8baf893afce9 (佐敦全球涂料单项冠军)

sqlite3 data/mvp.db "
  SELECT document_id, COUNT(*) as fact_count
  FROM fact_atom
  WHERE document_id IN (
    '35344020-b2ec-426c-aa3c-629241667cb6',
    '203bc029-e29d-42d5-b2c7-9abfa010cb5d',
    '557aa9ca-7322-4be2-b57b-8baf893afce9'
  )
  GROUP BY document_id;
"
```
记录输出结果作为基准

- [ ] **Step 2: 清除测试文档旧结果**

```bash
python -c "
from app.models.db import get_connection
conn = get_connection()
docs = [
  '35344020-b2ec-426c-aa3c-629241667cb6',
  '203bc029-e29d-42d5-b2c7-9abfa010cb5d',
  '557aa9ca-7322-4be2-b57b-8baf893afce9'
]
for doc_id in docs:
  conn.execute('DELETE FROM fact_atom WHERE document_id = ?', (doc_id,))
  conn.execute('DELETE FROM evidence_span WHERE document_id = ?', (doc_id,))
  conn.execute('UPDATE source_document SET status = \"pending\" WHERE id = ?', (doc_id,))
conn.commit()
conn.close()
print('已清除测试文档旧结果')
"
```

- [ ] **Step 3: 重新处理测试文档**

```bash
python -c "
from app.services.pipeline import process_document
docs = [
  '35344020-b2ec-426c-aa3c-629241667cb6',
  '203bc029-e29d-42d5-b2c7-9abfa010cb5d',
  '557aa9ca-7322-4be2-b57b-8baf893afce9'
]
for doc_id in docs:
  result = process_document(doc_id)
  print(f'{doc_id[:8]}: facts={result[\"facts\"]}, passed={result[\"passed\"]}, uncertain={result[\"uncertain\"]}')
"
```

- [ ] **Step 4: 对比验证**

```bash
sqlite3 data/mvp.db "
  SELECT document_id, COUNT(*) as fact_count
  FROM fact_atom
  WHERE document_id IN (
    '35344020-b2ec-426c-aa3c-629241667cb6',
    '203bc029-e29d-42d5-b2c7-9abfa010cb5d',
    '557aa9ca-7322-4be2-b57b-8baf893afce9'
  )
  GROUP BY document_id;
"
```

- [ ] **Step 5: 检查人工审核比例**

```bash
sqlite3 data/mvp.db "
  SELECT review_status, COUNT(*) as cnt
  FROM fact_atom
  WHERE document_id IN (
    '35344020-b2ec-426c-aa3c-629241667cb6',
    '203bc029-e29d-42d5-b2c7-9abfa010cb5d',
    '557aa9ca-7322-4be2-b57b-8baf893afce9'
  )
  GROUP BY review_status;
"
```

- [ ] **Step 6: 评估结果**

对比基准与改进后的数据：
- 目标：单文档事实数 3-5 条 → 20-30 条
- 目标：HUMAN_REVIEW_REQUIRED 比例 26% → 10-15%

---

## 预期结果示例

| 文档 | 改进前事实数 | 改进后目标事实数 |
|------|------------|----------------|
| 佐敦EOLMED项目 | 4 | 15-20 |
| 佐敦项目入选省重大项目 | 6 | 18-25 |
| 佐敦全球涂料单项冠军 | 11 | 20-30 |
