---
name: fact-fidelity-audit
description: '审核事实原子的内容保真度。Use when: 检查事实原子是否与原文匹配、年份数值是否正确、事实是否能还原原始事件。适用于对已通过结构审核的事实原子做深层内容校验。'
argument-hint: '可选：文档ID 或 fact_type 过滤条件，如 "document_id=xxx" 或 "检查所有 FINANCIAL_METRIC"'
---

# 事实原子内容保真度审核

## 定位

本技能是**结构审核之后**的第二道质量关卡。系统内置的 `reviewer`（见 `app/prompts/reviewer.txt`）只检查字段格式和类型是否正确，**不校验内容是否与原文匹配**。本技能由 Agent 直接阅读证据原文与事实原子逐条比对，**不额外调用 LLM**，目的是评估整体提取质量，找到系统性问题并给出优化方案。

## 何时使用

- 批量导入处理后，评估提取质量
- 用户对事实原子准确性有疑问时
- 新调整 prompt 后验证输出效果
- 定期质量巡检

## 审核流程

### 第 1 步：获取待审核数据

运行审核脚本取出事实原子及证据原文的对照数据。**审核范围包含所有状态**（自动通过 + 待人工审核 + 待处理等），用于全面评估提取质量：

```bash
# 取某个文档的全部事实（推荐）
python .github/skills/fact-fidelity-audit/scripts/audit_facts.py --doc-id <DOC_ID> --status ""

# 取某种类型的最近 30 条
python .github/skills/fact-fidelity-audit/scripts/audit_facts.py --fact-type FINANCIAL_METRIC --status "" --limit 30

# JSON 格式输出（便于程序处理）
python .github/skills/fact-fidelity-audit/scripts/audit_facts.py --doc-id <DOC_ID> --status "" --json
```

参数说明：
- `--status ""`：空字符串表示不过滤状态，取所有状态的事实
- `--status 自动通过`：仅取自动通过的
- `--limit`：默认 50 条，按需调整

### 第 2 步：逐条比对（核心检查清单）

对每条事实原子，**阅读 evidence_text（证据原文）**，按以下 7 项逐一比对。Agent 直接做判断，不调用额外 LLM。

#### ① 时间保真

| 检查点 | 说明 |
|--------|------|
| `time_expr` 与原文一致 | 原文写"2025年"，`time_expr` 不能是"2024年" |
| 时间归属正确 | "预计2026年投产" vs "2024年开工"，不得混淆 |
| 时间粒度合理 | 原文"2025年上半年" → 不应简化为"2025" |

#### ② 数值保真

| 检查点 | 说明 |
|--------|------|
| `value_num` 与原文数字一致 | 原文"约3.3亿美元" → `value_num=3.3`，不能是 33 |
| `value_text` 忠实还原原文表述 | 保留"约""超过""近"等修饰词 |
| `unit` 与原文单位匹配 | "万吨" vs "吨" vs "万元" 不能混 |
| `currency` 正确 | "美元" vs "人民币" vs "港元" |
| 数值来源唯一 | 一个 value_num 只对应证据中的一个数字，不能把两个数字合并 |

#### ③ 主体保真

| 检查点 | 说明 |
|--------|------|
| `subject_text` 在原文中有依据 | 不能凭推理补全原文未提及的主体 |
| 指代消解准确 | 原文"该公司"若上文明确指 A公司，subject 应为"A公司" |
| 简称/全称对应 | subject 的简称需与原文上下文的指代一致 |

#### ④ 客体/目标保真

| 检查点 | 说明 |
|--------|------|
| `object_text` 在原文有依据 | 合作方、收购标的、投资项目名应与原文一致 |
| 主客体方向正确 | A 收购 B → subject=A, object=B，不得颠倒 |

#### ⑤ 谓词保真

| 检查点 | 说明 |
|--------|------|
| `predicate` 反映原文的动作/状态 | 原文"计划投资" vs "已投资"，时态/意图不同 |
| 不夸大不缩小 | "签署战略合作协议" ≠ "合作"，精度要匹配 |

#### ⑥ 限定词(qualifier)保真

| 检查点 | 说明 |
|--------|------|
| 关键限定信息未丢失 | 原文有"中国市场" → qualifier 应有 market_scope |
| 限定值与原文一致 | ranking_scope="全球" 不能写成"中国" |
| 阶段(phase)匹配 | 原文"在建" → phase 不能是 "completed" |

#### ⑦ 事件还原度

用以下公式拼读事实原子，检验是否能还原原文事件：

> **[subject] [predicate] [object] [value_text + unit] [time_expr]**

- ✅ "佐敦集团 投资建设 液体涂料研发中心 — —" → 原文描述匹配
- ❌ "佐敦集团 投资 — 1300万元 2024年" → 原文说的是2025年，且丢失了投资对象

### 第 3 步：输出审核报告

汇总为结构化报告，包含三部分：**统计概览 → 问题明细 → 优化方案**。

#### 报告格式

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 事实原子内容保真度审核报告
 审核范围：文档 [title] / [fact_type] / 共 [N] 条
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

一、统计概览
 ✅ 内容准确：XX 条 (XX%)
 ⚠️ 一般问题：XX 条 (XX%)
 ❌ 严重问题：XX 条 (XX%)

 按问题类别分布：
   时间保真：X 条
   数值保真：X 条
   主体保真：X 条
   客体保真：X 条
   谓词保真：X 条
   限定词保真：X 条
   还原度不足：X 条

二、问题明细

 ❌ 严重问题

 #1 [fact_id] 时间保真
    证据原文："预计2025年底建成投产"
    事实原子：time_expr="2024年"
    → 年份错误，应为 "2025年底"

 #2 [fact_id] 数值保真
    证据原文："总投资约3.3亿美元"
    事实原子：value_num=33, unit="亿美元"
    → 数值偏差一个数量级，应为 value_num=3.3

 ⚠️ 一般问题

 #3 [fact_id] 限定词保真
    证据原文："中国船舶涂料市场前三强企业"
    事实原子：qualifier_json={}
    → 缺少 market_scope="中国船舶涂料市场"

三、优化方案

 按问题根因分组，给出具体的 prompt/代码/配置改进建议：

 1. [根因类别] — 影响 X 条
    现象：……
    根因分析：……
    建议改进：
      - prompt 修改：在 prompts/xxx.txt 中增加 ……
      - 代码修改：在 services/xxx.py 中调整 ……
      - 配置修改：在 config.yaml 中增加 ……
```

#### 严重程度定义

| 等级 | 定义 | 典型例子 |
|------|------|----------|
| **严重** | 事实原子表述与原文含义相反或数值错误 | 年份错、主客体颠倒、数量级错误 |
| **一般** | 信息丢失导致事实原子无法完整还原事件 | 缺少关键 qualifier、时间模糊化、object 丢失 |
| **轻微**（归入准确） | 表述精度略有偏差但不影响核心含义 | 修饰词"约"丢失、时间粒度降级但年份正确 |

### 第 4 步：优化方案设计

审核报告的第三部分是核心产出。对发现的问题进行**根因聚类**，然后给出可执行的优化建议：

#### 根因聚类维度

| 根因类别 | 对应位置 | 优化手段 |
|----------|----------|----------|
| LLM 提取 prompt 不足 | `app/prompts/fact_extractor_*.txt` | 增加约束规则、增加示例 |
| 补全 prompt 引入错误 | `app/prompts/context_complementation.txt` | 限制补全范围 |
| 证据切片过短/过长 | `app/services/text_splitter.py` | 调整分块参数 |
| Reviewer 结构审核遗漏 | `app/prompts/reviewer.txt` | 增加内容校验项 |
| fact_type 规则缺失 | `app/prompts/fact_type_rules/*.txt` | 补充特定类型的提取规则 |
| 指代消解失败 | `app/services/full_extractor.py` | 改进上下文传递方式 |
| 后处理逻辑 bug | `app/services/*.py` | 代码修复 |

#### 优化方案输出要求

每条建议须包含：
1. **影响面**：影响多少条事实原子
2. **具体改动位置**：文件路径 + 行号/函数名
3. **改动内容**：改什么、怎么改（给出 diff 或文字描述）
4. **预期效果**：改后能解决哪类问题

## 常见问题模式

基于项目实际数据，以下是高频出错模式（审核时优先检查）：

| 模式 | 频率 | 示例 | 优化方向 |
|------|------|------|----------|
| 年份偏移 | 高 | 原文2025→原子2024 | prompt 强调"严格使用原文年份" |
| 单位混淆 | 高 | 万元 vs 亿元，万吨 vs 吨 | prompt 增加单位校验规则 |
| "预计/规划"变"已完成" | 中 | "计划投资"→写成已投资 | prompt 强调保留时态词 |
| 竞争排名缺 scope | 中 | "中国前十"→ qualifier 缺 market_scope | fact_type_rules 补充 |
| 多主体合并 | 低 | A和B的数据合到一条 atom | 提取 prompt 增加拆分规则 |
| 百分比与绝对值混淆 | 低 | 32.45% → value_num=32.45 但 unit 丢失 | prompt 明确百分比写法 |

## 项目上下文

- 数据库：SQLite，路径由 `app/models/db.py` 的 `get_connection()` 管理
- 事实表：`fact_atom` JOIN `evidence_span`（证据原文）
- 现有结构审核：`app/prompts/reviewer.txt`（仅检查格式，不检查内容）
- 提取 prompt：`app/prompts/fact_extractor_common.txt` / `fact_extractor_full.txt`
- 补全 prompt：`app/prompts/context_complementation.txt`
- 类型规则：`app/prompts/fact_type_rules/*.txt`（9 种事实类型）
- 9 种事实类型：FINANCIAL_METRIC, SALES_VOLUME, MARKET_SHARE, CAPACITY, COMPETITIVE_RANKING, INVESTMENT, PRICE_CHANGE, EXPANSION, COOPERATION
