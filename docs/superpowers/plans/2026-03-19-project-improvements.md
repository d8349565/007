# 资讯颗粒化项目改进实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Entity 抽取为空问题、优化审核阈值配置、修正提取规则缺陷、建立 MCP 浏览器测试能力

**Architecture:** 四项改进，其中子任务 1-2-3 相互独立可并行开发，子任务 4（MCP 集成）需独立配置。

**Tech Stack:** Python/SQLite, Flask, Playwright MCP, LLM API

---

## 一、数据库现状分析

### 数据统计（单文档测试样本）

| 指标 | 数值 | 评估 |
|------|------|------|
| 文档总数 | 1 | 测试数据 |
| 事实总数 | 43 | 正常 |
| 待审核 (HUMAN_REVIEW_REQUIRED) | 23 (53%) | ⚠️ 偏高 |
| 自动通过 (AUTO_PASS) | 10 (23%) | ✓ |
| 拒绝 (REJECTED) | 9 (21%) | ⚠️ 需分析 |
| **实体数 (entity)** | **0** | ❌ 严重 |
| 证据片段 (evidence_span) | 22 | ✓ |

### 审核状态按置信度分布

| 置信度区间 | AUTO_PASS | HUMAN_REVIEW | PENDING | REJECTED |
|-----------|-----------|--------------|---------|----------|
| 0.95+ | 8 | **19** | 1 | 0 |
| 0.90-0.95 | 2 | **4** | 0 | 0 |
| <0.50 | 0 | 0 | 0 | **9** |

---

## 二、提取规则缺陷分析

### 根因发现：config.yaml 审核配置过严

```yaml
review:
  auto_pass_confidence: 0.90
  force_human_review_types:
    - MARKET_SHARE        # ← 所有 MARKET_SHARE 都强制人工审核
    - COMPETITIVE_RANKING # ← 所有 COMPETITIVE_RANKING 都强制人工审核
  force_human_review_predicates:
    - yoy                  # ← 包含 yoy 的都强制人工审核
    - qoq
```

这解释了为什么 0.95+ 置信度的事实仍有 19 条处于 `HUMAN_REVIEW_REQUIRED`。

---

### 缺陷 1: COMPETITIVE_RANKING 范围排名 vs 具体排名未区分

| 证据文本 | 提取结果 | 问题 |
|---------|---------|------|
| "立邦、中涂化工、PPG、金刚化工...分别位居第 4-10 位" | `predicate: "位居第"` + `val: None` + `qual: {rank_range: "4-10"}` | **谓词错误**："位居第"暗示具体排名，但实际是排名范围 |

**规则缺陷：** 缺少 `rank_range` 专用 predicate（如"排名在范围内"），当证据只给出范围时应使用范围谓词。

**影响：** 4 条 REJECTED（conf:0.15），涉及立邦、中涂化工、PPG、鱼童。

---

### 缺陷 2: MARKET_SHARE 变化值误作主值

| 证据文本 | 提取结果 | 问题 |
|---------|---------|------|
| "市场份额85.32%，较上一年减少了2.42个百分点" | `fact_type: MARKET_SHARE` + `predicate: "市场份额减少"` + `val: 2.42` | **主值错误**：MARKET_SHARE 应记录当前值85.32，而非变化量2.42 |

**规则缺陷：** `MARKET_SHARE` 类型的 fact 期望主值为当前市场份额，变化量应作为 qualifier（如 `change_pct: -2.42`）。

**影响：** 2 条 REJECTED（conf:0.30）。

---

### 缺陷 3: FINANCIAL_METRIC 增量 vs 累计值未区分

| 证据文本 | 提取结果 | 问题 |
|---------|---------|------|
| "累计销售收入106.51亿元，较上一年增加16.63亿元" | `predicate: "累计销售收入增加"` + `val: 16.63` | **结构错误**：106.51是主值，16.63是增量 |

**规则缺陷：** 谓词 `新增销售收入` 和 `累计销售收入` 混用，导致 LLM 选择了增量作为主值。

**影响：** 1 条 REJECTED。

---

### 缺陷 4: approx/约 标记未捕获

| 证据文本 | 提取结果 | 问题 |
|---------|---------|------|
| "约合 107.669 亿美元" | `val: 107.669` + `unit: "107.669 亿美元"` | **近似信息丢失**：缺少 `is_approximate: true` qualifier |

**规则缺陷：** `qualifier_whitelist` 中缺少 `is_approximate` 字段。

---

### 缺陷 5: 复合句切分导致信息丢失

证据：`"上榜的外资品牌总销售收入高达98.12亿元，较上一年新增15.64亿元；占全国市场份额的85.32%..."`

拆分成多个 evidence_span 时，`market_scope: "全国"` 被单独抽取但未关联到对应的 MARKET_SHARE 事实。

---

### 改进建议优先级

| 优先级 | 问题 | 解决方案 |
|-------|------|---------|
| P0 | `force_human_review_types` 过严 | 移除 `MARKET_SHARE`，保留 `COMPETITIVE_RANKING` |
| P0 | `yoy` 强制人工审核 | 移除 `yoy`，或提高 `auto_pass_confidence` 到 0.95 |
| P1 | 缺少范围排名 predicate | 新增 `排名在范围内` predicate |
| P1 | MARKET_SHARE 变化量误作主值 | 规则明确：主值必须是当前份额，变化作 qualifier |
| P2 | 增量/累计值混淆 | 分离谓词：`累计销售收入为` vs `销售收入增加` |
| P2 | 缺少 is_approximate | 加入 `qualifier_whitelist` |

---

## 三、变更文件清单

- 修改: `app/services/entity_linker.py` — 修复实体抽取逻辑
- 修改: `app/services/reviewer.py` — 调整审核阈值/规则
- 修改: `config.yaml` — 更新审核配置和规则白名单
- 新增: `.claude/mcp.json` — MCP Playwright 配置
- 新增: `tests/test_entity_linker.py` — 实体抽取测试
- 新增: `tests/test_review_threshold.py` — 审核阈值测试
- 修改: `docs/testing-guide.md` — MCP 浏览器测试指南

---

## 四、子任务 1: 修复 Entity Linker（实体抽取）

### 问题根因

`entity` 表为空 (0 条记录)，`entity_linker` 服务未正常工作。

**Files:**

- 修改: `app/services/entity_linker.py:1-200`
- 新增: `tests/test_entity_linker.py`

- [ ] **Step 1: 读取 entity_linker.py 分析问题**

```bash
cat app/services/entity_linker.py
```

- [ ] **Step 2: 检查 pipeline.py 中 entity_linker 的调用时机**

```bash
grep -n "entity_linker" app/services/pipeline.py
```

- [ ] **Step 3: 编写失败的测试用例**

```python
# tests/test_entity_linker.py
def test_entity_extraction_from_fact():
    from app.services.entity_linker import extract_entities_from_facts
    facts = [{"subject_text": "立邦涂料", "object_text": "船舶涂料"}]
    entities = extract_entities_from_facts(facts)
    assert len(entities) >= 1
    assert any(e["canonical_name"] == "立邦涂料" for e in entities)
```

- [ ] **Step 4: 运行测试验证失败**

```bash
pytest tests/test_entity_linker.py -v
```

Expected: FAIL — 函数未实现或返回空

- [ ] **Step 5: 检查 config.yaml 中 entity_linker 配置**

```bash
grep -A5 "entity_linker" config.yaml
```

- [ ] **Step 6: 实现 minimal entity_linker**

在 `entity_linker.py` 中实现从 fact_atom 抽取唯一主体/客体文本并存储到 entity 表的逻辑。

- [ ] **Step 7: 运行测试验证通过**

```bash
pytest tests/test_entity_linker.py -v
```

Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add app/services/entity_linker.py tests/test_entity_linker.py
git commit -m "fix: implement entity extraction from fact atoms"
```

---

## 五、子任务 2: 优化审核阈值和配置

### 问题分析

53% 事实需人工审核，REJECTED 21%。根因：
1. `force_human_review_types` 包含 `MARKET_SHARE` 和 `COMPETITIVE_RANKING`
2. `force_human_review_predicates` 包含 `yoy` 和 `qoq`
3. `auto_pass_confidence: 0.90` 但高置信度事实仍被强制人工审核

**Files:**

- 修改: `app/services/reviewer.py`
- 修改: `config.yaml`
- 新增: `tests/test_review_threshold.py`

- [ ] **Step 1: 读取 reviewer.py 分析审核逻辑**

```bash
cat app/services/reviewer.py
```

- [ ] **Step 2: 编写测试用例验证阈值行为**

```python
# tests/test_review_threshold.py
def test_auto_pass_threshold():
    from app.services.reviewer import _map_verdict_to_status
    cfg = {"review": {"auto_pass_confidence": 0.90, "force_human_review_types": ["MARKET_SHARE"], "force_human_review_predicates": ["yoy"]}}
    # MARKET_SHARE with PASS and high score should still be HUMAN_REVIEW (force_human)
    result = _map_verdict_to_status("PASS", 0.98, {"fact_type": "MARKET_SHARE", "qualifiers": {}}, cfg)
    assert result == "HUMAN_REVIEW_REQUIRED"
    # Without force_human, should AUTO_PASS
    cfg2 = {"review": {"auto_pass_confidence": 0.90, "force_human_review_types": [], "force_human_review_predicates": []}}
    result2 = _map_verdict_to_status("PASS", 0.98, {"fact_type": "MARKET_SHARE", "qualifiers": {}}, cfg2)
    assert result2 == "AUTO_PASS"
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_review_threshold.py -v
```

- [ ] **Step 4: 调整 config.yaml 审核配置**

修改 `config.yaml`:

```yaml
review:
  auto_pass_confidence: 0.90
  force_human_review_types:
    - COMPETITIVE_RANKING  # 移除 MARKET_SHARE
  force_human_review_predicates: []  # 移除 yoy, qoq
```

- [ ] **Step 5: 验证调整后统计**

重新运行 `stats` 命令，检查 AUTO_PASS 比例是否上升。

预期：23 条 HUMAN_REVIEW_REQUIRED 应大幅减少。

- [ ] **Step 6: 提交**

```bash
git add app/services/reviewer.py config.yaml tests/test_review_threshold.py
git commit -m "tune: adjust review thresholds and force_human_review config"
```

---

## 六、子任务 3: 修正提取规则缺陷

### 问题分析

基于上述缺陷分析，需修改 `config.yaml` 中的 predicate 和 qualifier 白名单。

**Files:**

- 修改: `config.yaml`

- [ ] **Step 1: 新增范围排名 predicate**

在 `predicate_whitelist.COMPETITIVE_RANKING` 中新增:

```yaml
COMPETITIVE_RANKING:
  - 排名第
  - 位居第
  - 跻身前
  - 入选
  - 连续排名
  - 名列
  - 排名在范围内  # 新增：处理 "第4-10位" 类证据
```

- [ ] **Step 2: 新增 is_approximate qualifier**

在 `qualifier_whitelist.FINANCIAL_METRIC` 中新增:

```yaml
qualifier_whitelist:
  FINANCIAL_METRIC:
    - metric_name
    - segment
    - yoy
    - qoq
    - report_scope
    - is_forecast
    - change_amount
    - change_from_previous_year
    - equivalent_usd
    - equivalent_rmb
    - equivalent_weight
    - is_approximate  # 新增
```

- [ ] **Step 3: 在 prompt 文件中添加规则说明**

检查 `app/prompts/` 目录下的 fact_extractor 相关 prompt，确保包含：
- MARKET_SHARE 类型主值必须是当前市场份额，变化量放 qualifier
- FINANCIAL_METRIC 的累计值和增量需区分
- approx/约 标记需设置 is_approximate: true

- [ ] **Step 4: 用现有数据重新验证规则**

导入同一篇文档，观察 REJECTED 比例是否下降。

- [ ] **Step 5: 提交**

```bash
git add config.yaml app/prompts/
git commit -m "fix: add extraction rules for range ranking and approximate values"
```

---

## 七、子任务 4: MCP Playwright 浏览器测试集成

### 目标

配置 MCP Playwright 实现自动化浏览器测试，覆盖 http://127.0.0.1:5000/ 所有页面。

**Files:**

- 新增: `.claude/mcp.json`
- 新增: `tests/test_web_browser.py`
- 新增: `docs/testing-guide.md`

- [ ] **Step 1: 检查 Playwright MCP 插件路径**

```bash
ls "C:/Users/Lee/.claude/plugins/marketplaces/claude-plugins-official/external_plugins/playwright/"
```

- [ ] **Step 2: 创建 .claude/mcp.json**

```json
{
  "mcpServers": {
    "playwright": {
      "command": "node",
      "args": ["C:/Users/Lee/.claude/plugins/marketplaces/claude-plugins-official/external_plugins/playwright/dist/index.js"]
    }
  }
}
```

- [ ] **Step 3: 验证 MCP 连接**

在 Claude Code 中运行:
```
/mcp list
```
应显示 playwright server。

- [ ] **Step 4: 编写浏览器测试用例**

```python
# tests/test_web_browser.py
async def test_homepage_loads():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("http://127.0.0.1:5000/")
        assert "资讯颗粒化" in await page.title()
        assert await page.locator(".metric-val").count() >= 4
        await browser.close()
```

- [ ] **Step 5: 运行测试**

```bash
pytest tests/test_web_browser.py -v
```

- [ ] **Step 6: 编写完整页面测试**

覆盖: `/documents`, `/documents/<id>`, `/review`, `/passed`, `/stats`

- [ ] **Step 7: 编写测试指南文档**

在 `docs/testing-guide.md` 中编写以下内容：

```markdown
# 浏览器测试指南

### 启动 Web 服务
...（启动命令和步骤）

### 运行浏览器测试
...（测试命令和覆盖页面）
```

- [ ] **Step 8: 提交**

```bash
git add .claude/mcp.json tests/test_web_browser.py docs/testing-guide.md
git commit -m "feat: add MCP Playwright browser testing"
```

---

## 执行选项

**Plan complete and saved to `docs/superpowers/plans/2026-03-19-project-improvements.md`. Four execution options:**

**1. Subagent-Driven (recommended)** - 每个子任务分配独立子代理，并行开发，任务间有检查点

**2. Inline Execution** - 在当前会话中顺序执行，有检查点

**3. Priority-First** - 先执行 P0 优先级任务（审核配置调整），再并行执行其他任务

**Which approach?**
