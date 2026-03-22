# 事实抽取完整度 Phase 2 实现计划（C1a 两阶段抽取）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现两阶段上下文感知抽取，目标单文档 20-30 条重要事实

**Architecture:** 阶段 1 结构化全量抽取 + 阶段 2 上下文补全审查

**Tech Stack:** Prompt 文件修改 + full_extractor.py 逻辑扩展

---

## 文件修改清单

| 文件 | 改动类型 |
|------|---------|
| `app/prompts/fact_extractor_full.txt` | 修改：更新为结构化输出格式 |
| `app/prompts/context_complementation.txt` | 新建：阶段 2 补全 prompt |
| `app/services/full_extractor.py` | 修改：添加阶段 2 逻辑 + 更新解析逻辑 |

---

## Task 1: 创建阶段 2 补全 Prompt

**Files:**
- Create: `app/prompts/context_complementation.txt`

- [ ] **Step 1: 创建 context_complementation.txt**

内容：
```
You are a Context Complementation Reviewer for an industry intelligence fact atomization system.

## Your Task

Review the already-extracted facts for a specific fact_type and check if any are missing due to:
1. **Coreference issues**: "该公司", "其", "该公司投资" → need context to identify subject
2. **Multi-sentence facts**: Related facts split across multiple sentences that should be merged
3. **Same-entity multi-dimension**: Same entity mentioned in different contexts (e.g., revenue AND ranking AND investment)
4. **Implicit subjects**: Facts where the subject was omitted in text but understood from context

## Input

**Article text:**
[full article text]

**Already extracted [FACT_TYPE] facts:**
[list of already-extracted facts in JSON format]

## Review Process

1. Read the full article carefully
2. Compare with the already-extracted facts
3. Identify any missing facts that require context to extract
4. For each missing fact found, provide:
   - The complete fact record
   - The evidence text from the article

## Output Format

Return a JSON object:
{
  "supplemented_facts": [
    {"subject": "...", "predicate": "...", "object": ..., "value_num": ..., "evidence_text": "...", "reason": "why this was missing"},
    ...
  ],
  "unchanged_facts": [
    // Facts from the original list that are confirmed complete
  ],
  "review_note": "Brief explanation of what was checked and any concerns"
}

If no facts are missing, return:
{
  "supplemented_facts": [],
  "unchanged_facts": [/* all original facts */],
  "review_note": "All facts confirmed complete, no supplementation needed"
}
```

- [ ] **Step 2: 提交**

```bash
git add app/prompts/context_complementation.txt
git commit -m "feat(prompt): 添加阶段2上下文补全prompt"
```

---

## Task 2: 修改 Extraction Prompt 为结构化格式

**Files:**
- Modify: `app/prompts/fact_extractor_full.txt`

- [ ] **Step 1: 读取当前 fact_extractor_full.txt 的 Output format 部分**

确认 `## Output format` 部分的起始行号和内容

- [ ] **Step 2: 替换 Output format 为结构化格式**

将当前的 `## Output format` 部分替换为：

```
## Output format — STRUCTURED BY TYPE

Output a JSON object with 9 fixed keys, one per fact_type:

{
  "FINANCIAL_METRIC": [
    // Array of FINANCIAL_METRIC facts
  ],
  "SALES_VOLUME": [
    // Array of SALES_VOLUME facts
  ],
  "CAPACITY": [
    // Array of CAPACITY facts
  ],
  "INVESTMENT": [
    // Array of INVESTMENT facts
  ],
  "EXPANSION": [
    // Array of EXPANSION facts
  ],
  "MARKET_SHARE": [
    // Array of MARKET_SHARE facts
  ],
  "COMPETITIVE_RANKING": [
    // Array of COMPETITIVE_RANKING facts
  ],
  "COOPERATION": [
    // Array of COOPERATION facts
  ],
  "PRICE_CHANGE": [
    // Array of PRICE_CHANGE facts
  ]
}

Each fact within an array has EXACTLY 13 elements in this fixed order:

[0]  fact_type      — string: must match the parent key name
[1]  subject        — string (REQUIRED, never null): the entity name
[2]  predicate      — string: Chinese verb phrase
[3]  object         — string or null: target entity
[4]  value_num      — number or null
[5]  value_text     — string or null: original value expression
[6]  unit           — string or null
[7]  currency       — string or null: CNY/USD/EUR/HKD/JPY/BRL
[8]  time_expr      — string or null: time expression from the article
[9]  location       — string or null: geographic location
[10] qualifiers     — object: {} if none
[11] confidence     — number: 0 to 1
[12] evidence_text  — string: minimal supporting text copied from the article

**CRITICAL REQUIREMENTS:**
- Each fact_type key's array must contain AT LEAST 1 fact (unless the article truly has none for that type)
- If the article contains multiple facts of the same type, ALL must be listed
- An array can be empty [] ONLY if the article genuinely has no facts of that type
- Do NOT skip any fact_type key — every key must be present in the output
```

- [ ] **Step 3: 在 Quantity expectations 部分增加"必须检查每个 fact_type"强调**

在现有的 `## Quantity expectations` 末尾添加：

```
**IMPORTANT**: Every fact_type key must have at least 1 fact in its array.
Do not skip this requirement even if a fact_type seems "less important".
```

- [ ] **Step 4: 提交**

```bash
git add app/prompts/fact_extractor_full.txt
git commit -m "feat(prompt): 改为结构化输出格式"
```

---

## Task 3: 修改 full_extractor.py 实现两阶段抽取

**Files:**
- Modify: `app/services/full_extractor.py`

- [ ] **Step 1: 添加阶段 2 补全函数**

在 `full_extractor.py` 中添加新函数：

```python
def _complement_facts_by_type(
    facts_by_type: dict[str, list[dict]],
    cleaned_text: str,
    document_id: str,
    cfg: dict,
) -> dict[str, list[dict]]:
    """
    阶段 2：对每个 fact_type 的抽取结果进行上下文补全审查。

    参数:
        facts_by_type: 阶段 1 按类型分组的事实字典
        cleaned_text: 清洗后的全文
        document_id: 文档 ID
        cfg: 配置字典

    返回:
        补全后的事实字典（同样按类型分组）
    """
    comp_prompt_path = _PROMPT_DIR / "context_complementation.txt"
    comp_prompt = comp_prompt_path.read_text(encoding="utf-8")

    # 动态拼接 fact_type 规则
    rules_parts = []
    for rule_file in sorted(_RULES_DIR.glob("*.txt")):
        rules_parts.append(rule_file.read_text(encoding="utf-8"))
    if rules_parts:
        comp_prompt += "\n\n## Fact-type specific rules\n\n"
        comp_prompt += "\n\n".join(rules_parts)

    client = get_llm_client()
    result_by_type = {}

    for fact_type, facts in facts_by_type.items():
        if not facts:
            # 空数组，直接跳过
            result_by_type[fact_type] = []
            continue

        # 构建阶段 2 prompt
        user_input = json.dumps(
            {
                "article_text": cleaned_text,
                "fact_type": fact_type,
                "already_extracted_facts": facts,
            },
            ensure_ascii=False,
        )

        task_id = str(uuid.uuid4())
        _record_task_start(task_id, document_id, "complementation")

        try:
            result = client.chat_json(comp_prompt, user_input)
            raw_data = result["data"]
            _record_task_end(
                task_id, "success",
                result["input_tokens"], result["output_tokens"],
                result["model"],
            )
        except Exception as e:
            _record_task_end(task_id, "failed", error=str(e))
            logger.error("阶段2补全调用失败 [doc=%s, type=%s]: %s", document_id[:8], fact_type, e)
            # 失败时保留原结果
            result_by_type[fact_type] = facts
            continue

        # 解析补全结果
        supplemented = raw_data.get("supplemented_facts", [])
        unchanged = raw_data.get("unchanged_facts", [])

        # 合并：unchanged + supplemented（去重）
        all_facts = unchanged.copy()
        existing_evidence = {f.get("evidence_text", "") for f in all_facts}
        for new_fact in supplemented:
            if new_fact.get("evidence_text") not in existing_evidence:
                all_facts.append(new_fact)

        result_by_type[fact_type] = all_facts
        logger.info(
            "[doc=%s] 阶段2 %s: 原=%d, 补=%d, 终=%d",
            document_id[:8], fact_type, len(facts), len(supplemented), len(all_facts),
        )

    return result_by_type
```

- [ ] **Step 2: 添加结构化格式解析函数**

```python
def _parse_structured_output(raw_data: dict, cfg: dict) -> list[dict]:
    """
    解析结构化输出格式（按 fact_type 分组的 JSON），
    转换为标准的 list[dict] 格式。
    """
    parsed_records = []
    valid_types = cfg.get("fact_types", [])

    for fact_type, facts in raw_data.items():
        if not isinstance(facts, list):
            continue

        for item in facts:
            if isinstance(item, list):
                rec = _list_to_dict(item)
            elif isinstance(item, dict):
                rec = dict(item)
                rec["fact_type"] = fact_type  # 确保 fact_type 匹配 key
            else:
                continue

            if rec is None:
                continue

            validated = _validate_record(rec, cfg)
            if validated is not None:
                parsed_records.append(validated)

    return parsed_records
```

- [ ] **Step 3: 修改 extract_facts_full_text 函数**

在 `extract_facts_full_text` 函数中：

**a. 在调用 LLM 后（第 196 行之后）添加结构化解解析：**

```python
    # 阶段 1：解析结构化输出
    if isinstance(raw_data, dict):
        # 结构化格式：按 fact_type 分组
        parsed_records_stage1 = _parse_structured_output(raw_data, cfg)
    else:
        # 兼容旧格式（list of lists / list of dicts）
        parsed_records_stage1 = []
        for item in (raw_data if isinstance(raw_data, list) else [raw_data]):
            if isinstance(item, list):
                rec = _list_to_dict(item)
            elif isinstance(item, dict):
                rec = item
            else:
                continue
            if rec:
                validated = _validate_record(rec, cfg)
                if validated:
                    parsed_records_stage1.append(validated)
```

**b. 在 parsed_records_stage1 解析后添加阶段 2 补全：**

```python
    # 将阶段1结果按类型分组
    facts_by_type = {}
    for rec in parsed_records_stage1:
        ft = rec.get("fact_type", "UNKNOWN")
        if ft not in facts_by_type:
            facts_by_type[ft] = []
        facts_by_type[ft].append(rec)

    # 阶段 2：上下文补全
    logger.info("[doc=%s] 开始阶段2上下文补全...", document_id[:8])
    facts_by_type = _complement_facts_by_type(
        facts_by_type, cleaned_text, document_id, cfg,
    )

    # 合并所有类型的事实
    parsed_records = []
    for facts in facts_by_type.values():
        parsed_records.extend(facts)
```

**c. 更新日志输出：**

```python
    logger.info("[doc=%s] 两阶段抽取完成，共 %d 条有效记录", document_id[:8], len(parsed_records))
```

- [ ] **Step 4: 验证语法**

Run: `python -m py_compile app/services/full_extractor.py`
Expected: 无报错

- [ ] **Step 5: 提交**

```bash
git add app/services/full_extractor.py
git commit -m "feat(extractor): 实现两阶段上下文感知抽取"
```

---

## Task 4: 验证 Phase 2 改进效果

**Files:**
- Test: `data/mvp.db`（现有数据库）
- Test: `app/services/pipeline.py`

- [ ] **Step 1: 记录 Phase 1 基准（Task 3 结果）**

```
Phase 1 结果：
- 佐敦EOLMED项目: 6 facts
- 佐敦项目入选省重大项目: 14 facts
- 佐敦全球涂料单项冠军: 18 facts
- 总计: 38 facts
```

- [ ] **Step 2: 清除测试文档旧结果**

```python
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
  conn.execute('UPDATE source_document SET status = "pending" WHERE id = ?', (doc_id,))
conn.commit()
conn.close()
print('已清除测试文档旧结果')
```

- [ ] **Step 3: 重新处理测试文档（Phase 2）**

```python
from app.services.pipeline import process_document
docs = [
  '35344020-b2ec-426c-aa3c-629241667cb6',
  '203bc029-e29d-42d5-b2c7-9abfa010cb5d',
  '557aa9ca-7322-4be2-b57b-8baf893afce9'
]
for doc_id in docs:
  result = process_document(doc_id)
  print(f'{doc_id[:8]}: facts={result["facts"]}, passed={result["passed"]}, uncertain={result["uncertain"]}')
```

注意：Phase 2 会调用更多 LLM API（每个 fact_type 一次阶段 2 调用），
每篇文档可能需要 2-3 分钟。

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

- [ ] **Step 5: 检查审核比例**

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

| 指标 | Phase 1 | Phase 2 目标 | Phase 2 实际 |
|------|---------|-------------|-------------|
| 佐敦EOLMED项目 | 6 | 20-30 | ? |
| 佐敦项目入选省重大项目 | 14 | 20-30 | ? |
| 佐敦全球涂料单项冠军 | 18 | 20-30 | ? |
| HUMAN_REVIEW_REQUIRED 比例 | 44.74% | <15% | ? |
