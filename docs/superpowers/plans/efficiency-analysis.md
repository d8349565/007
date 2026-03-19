# 数据提取效率分析报告

> 分析日期：2026-03-19
> 基于：单文档测试数据（1篇文档，73条事实，127次API调用）

---

## 一、Pipeline 结构

```
Pipeline（顺序执行）:
  for each chunk (5个):
    └─ evidence_finder (1次/块)               → 5次调用
       └─ for each evidence (49个):
          └─ fact_extractor (1次/evidence)     → 49次调用
             └─ for each fact (73个):
                └─ reviewer (1次/fact)        → 73次调用

  总调用：127次
  总Token：138,079 (输入 117,629 + 输出 20,450)
  总费用：¥0.2966
```

### 各阶段调用统计

| 阶段 | 调用次数 | 输入Token/次 | 输出Token/次 | 总费用 | 费用占比 |
|------|---------|-------------|-------------|--------|---------|
| evidence_finder | 5 | 1,433 | 829 | ¥0.0268 | 9.0% |
| fact_extractor | 49 | 985 | 196 | ¥0.1253 | 42.2% |
| reviewer | 73 | 852 | 92 | ¥0.1446 | **48.7%** |
| **合计** | **127** | — | — | **¥0.2966** | 100% |

### Token 效率比

| 指标 | 数值 |
|------|------|
| 总处理 Token | 138,079 |
| 产出 facts | 73 |
| **Token/Fact 比** | **1,891** |

每条 fact 平均消耗约 1,891 tokens（含 evidence_finder、fact_extractor、reviewer 全链路开销）。

---

## 二、效率问题分析

### 问题 1: Reviewer 费用占比最高 (48.7%) — P0

**现象：** reviewer 阶段的费用占总费用的近一半，是最大的成本消耗。

**根因：**
- reviewer 采用 **逐条审核** 模式，每条 fact 单独调用一次 LLM
- 每次调用输入 852 tokens，但输出仅 92 tokens
- 输入/输出比高达 **9.3:1**，大量 token 浪费在 JSON 格式和模板文本上

**数据验证：**
- 73 条 facts → 73 次 reviewer 调用
- 每次 reviewer 调用平均费用：¥0.1446 / 73 ≈ ¥0.002

**改进方向：** 改为**批量审核**模式，将多条 facts + evidence 打包进一次 LLM 调用，可将调用次数减少 60-80%。

---

### 问题 2: Pipeline 完全串行 — P1

**现象：** 5 个 chunk、49 个 evidence、73 个 facts 全部顺序执行。

**根因：** `pipeline.py` 中三层嵌套 for 循环，无并行化：

```python
# pipeline.py 中的串行结构
for i, chunk_text in enumerate(chunks):           # 串行
    evidences = find_evidence(...)                  # 串行
    for ev in evidences:                           # 串行
        facts = extract_facts(...)                 # 串行
        for fact in facts:                        # 串行
            review_result = review_fact(...)      # 串行
```

**改进方向：** chunk 级别可并行处理（`ThreadPoolExecutor`），5 个 chunk 并行可将总处理时间缩短 60-80%。

---

### 问题 3: evidence_finder 输入 Token 偏高 — P2

**现象：** evidence_finder 每次调用输入 1,433 tokens，是三个阶段中最高的。

| 调用 | 输入 Token | 输出 Token |
|------|----------|----------|
| chunk_1 | 1,626 | 917 |
| chunk_2 | 1,548 | 984 |
| chunk_3 | 1,420 | 626 |
| chunk_4 | 1,405 | 663 |
| chunk_5 | 1,166 | 955 |

**根因：** chunk 包含完整段落文本作为上下文，evidence_finder 需要理解全段后才能判断证据边界。

**改进方向：** 适当调小 `chunk_max` 配置（如从 1200 降至 800），减少单次调用 token 量。

---

### 问题 4: fact_extractor 输出效率低 — P2

**现象：** fact_extractor 输入 985 tokens，输出仅 196 tokens，输入/输出比 5:1。

**分析：** fact_extractor 处理单个 evidence_span，输出主要是 JSON 格式的 fact 结构。单次调用通常只产生 1-2 个 fact，LLM 能力未充分利用。

**改进方向：** 可探索在单个 evidence 内抽取多个相关 facts，减少调用次数。

---

### 问题 5: 重跑幂等性风险

**现象：** 查询显示同一文档有重复调用记录（evidence_finder: 5次, fact_extractor: 49次, reviewer: 73次）。

**现状：** `pipeline.py:67` 有 `clear_document_results()` 保护，但若直接调用各阶段函数而不走 pipeline，则无幂等性保护。

---

## 三、效率优化优先级

| 优先级 | 问题 | 预期收益 | 改动范围 |
|--------|------|---------|---------|
| **P0** | Reviewer 批量审核 | 节省 40-50% 费用，减少 60-70% 调用次数 | 中（改 reviewer 接口） |
| **P1** | Chunk 并行处理 | 节省 60-80% 处理时间 | 小（加 ThreadPoolExecutor） |
| **P2** | evidence_finder 输入压缩 | 节省 10-20% token | 小（调 chunk_max 配置） |
| **P2** | 重跑幂等性强化 | 防止重复数据 | 小（已在 pipeline 有保护） |

---

## 四、现有架构优点

1. **幂等性保护 ✅** — `clear_document_results()` 确保重跑不累积重复数据
2. **evidence_finder 效率正常** — 5 chunks → 49 evidence，比例 9.8 evidence/chunk，符合预期
3. **fact_extractor 无冗余** — 49 calls / 49 evidence = 1:1，每 evidence 仅处理一次
4. **Token 监控完善 ✅** — `extraction_task` 表完整记录每次调用，支持精细化分析
5. **费用计算已集成 ✅** — 已实现按文档、按阶段的费用统计

---

## 五、优化收益预估

若实施 P0 + P1 优化：

| 指标 | 优化前 | 优化后（预估） | 提升 |
|------|--------|---------------|------|
| Reviewer 调用次数 | 73 | 10-20 | ↓70-86% |
| 总调用次数 | 127 | 60-80 | ↓37-53% |
| 总费用 | ¥0.2966 | ¥0.15-0.20 | ↓30-50% |
| 处理时间（并行） | T | T × 0.3-0.5 | ↓50-70% |
