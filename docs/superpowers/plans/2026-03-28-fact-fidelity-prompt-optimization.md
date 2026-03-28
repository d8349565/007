# 事实原子内容保真度 Prompt 优化计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 根据保真度审核报告（52 条事实原子，18 条问题），优化提取/补全/类型规则 prompt，提升事实原子内容保真度

**来源:** 审核发现 6 类根因问题，按优先级排列如下

**影响面统计:**
- ❌ 严重问题：3 条 (5.8%)
- ⚠️ 一般问题：15 条 (28.8%)
- ✅ 准确：34 条 (65.4%)

---

## 文件修改清单

| 文件 | 改动类型 | 对应根因 |
|------|---------|---------|
| `app/prompts/fact_extractor_common.txt` | 修改：新增 3 条规则(21/22/23) | 根因 1+5 |
| `app/prompts/fact_extractor_common.txt` | 修改：扩展第 16 条 | 根因 2 |
| `app/prompts/context_complementation.txt` | 修改：新增补全红线约束 | 根因 3 |
| `app/prompts/fact_type_rules/competitive_ranking.txt` | 修改：新增 value_text 和 ranking_scope 规则 | 根因 4 |
| `app/prompts/fact_type_rules/cooperation.txt` | 修改：强化 predicate 完整性约束 | 根因 6 |

---

## Task 1: Predicate 规范规则 [P0] — 影响 8 条

**问题描述：**
- predicate 包含 object 信息（如 "船舶涂料改造项目（一期）通过验收"）
- predicate 过于简单（如仅为 "与"）
- predicate 夸大/扭曲语义（如 "参与建设" 实为供应涂料）

**Files:**
- Modify: `app/prompts/fact_extractor_common.txt`

- [x] **Step 1: 在第 20 条规则之后新增第 21、22 条**

在 `fact_extractor_common.txt` 的 `20. value_num scale preservation` 规则之后，`Use this fixed schema` 行之前，添加：

```
21. predicate MUST be a PURE verb phrase (动词/动词短语). Do NOT include the target entity or object name inside predicate.
    - ✅ predicate="通过验收", object="船舶涂料改造项目（一期）"
    - ❌ predicate="船舶涂料改造项目（一期）通过验收", object=null
    - ✅ predicate="签约落地", object="高性能涂料新工厂项目"
    - ❌ predicate="签约落地高性能涂料新工厂及液体涂料研发中心项目", object=null
22. predicate MUST preserve the EXACT tense/intent from the evidence:
    - "计划投资" vs "已投资" vs "签约投资" are DIFFERENT — use the one matching the evidence
    - "合作使用" ≠ "合作供应" — do not rephrase the action
    - NEVER use predicate="与" or "和" alone — always include the full action verb (e.g. "共同投资设立", "合作成立")
    - Do NOT exaggerate: if a company is a supplier, use "供应涂料" not "参与建设"
```

---

## Task 2: 货币识别扩展 [P2] — 影响 2 条

**问题描述：**
unit 含"挪威克朗"但 currency=null，因 prompt 只列了 4 种货币

**Files:**
- Modify: `app/prompts/fact_extractor_common.txt`

- [x] **Step 1: 扩展第 16 条规则的货币映射列表**

将第 16 条从：
```
16. currency: For monetary values, always specify the currency code. Infer from context: "亿元" / "万元" / "元" → CNY; "亿日元" → JPY; "亿美元" → USD; "亿港元" → HKD. Only leave null if the value is non-monetary.
```

修改为：
```
16. currency: For monetary values, always specify the currency code. Infer from context: "亿元"/"万元"/"元" → CNY; "亿日元" → JPY; "亿美元" → USD; "亿港元" → HKD; "亿欧元"/"欧元" → EUR; "亿挪威克朗"/"挪威克朗" → NOK; "亿韩元" → KRW; "亿英镑" → GBP; "亿卢布" → RUB; "亿瑞士法郎" → CHF. Only leave null if the value is non-monetary. If the unit string contains a currency name (e.g. unit="亿挪威克朗"), currency MUST be set correspondingly (NOK).
```

---

## Task 3: 补全阶段约束 [P1] — 影响 2 条（严重）

**问题描述：**
补全阶段从全文其他段落引入 time_expr="2009年"、qualifier.phase="一期" 等虚假信息，evidence 中无依据

**Files:**
- Modify: `app/prompts/context_complementation.txt`

- [x] **Step 1: 在 Output Format 之前插入补全红线规则**

在 `## Review Process` 和 `## Output Format` 之间插入新的约束段落：

```
## 补全红线（必须遵守）

1. **直接文字依据原则**：补全的每个字段值必须在原文中有明确的文字依据。不得凭推理或常识补充原文未提及的信息。
2. **语义关联原则**：不得将原文A段落的年份/数值/阶段补到B段落描述的事实上，除非原文明确建立了两者的关联（如"上述项目于2009年..."）。
3. **reason 字段必填**：补全时 reason 必须引用原文原句，说明信息来源于哪句话。如果找不到直接文字依据，该字段应保持 null。
4. **禁止猜测填充**：以下场景必须保持 null 而非猜测：
   - 原文未提及时间 → time_expr 保持 null
   - 原文未提及阶段 → qualifier.phase 保持 null
   - 原文未提及地点 → location 保持 null
5. **交叉段落验证**：如果补全的信息来自文章其他段落，必须验证该段落描述的确实是同一个实体/事件，而非名称相似的不同实体/事件。
```

---

## Task 4: COMPETITIVE_RANKING value_text 规则 [P2] — 影响 3 条

**问题描述：**
排名类 fact 的 value_text 系统性缺失（value_num=9 但 value_text=null），ranking_scope 使用英文

**Files:**
- Modify: `app/prompts/fact_type_rules/competitive_ranking.txt`

- [x] **Step 1: 在现有规则末尾追加两条新规则**

在 `competitive_ranking.txt` 末尾追加：

```
- value_text MUST contain the original ranking expression from the evidence (e.g. "第9位", "第一名", "全球第1", "前十强"). Do NOT leave value_text=null when value_num has a ranking number.
- ranking_scope in qualifiers MUST be Chinese text ("全球"/"中国"/"亚洲"/"欧洲"). NEVER use English values ("global"/"china"/"asia").
```

---

## Task 5: 主体归属规则 [P1] — 影响 2 条（1 条严重）

**问题描述：**
- 代理商中标但 subject 写成供应商
- 原文"佐敦张家港生产基地"被泛化为"佐敦中国"

**Files:**
- Modify: `app/prompts/fact_extractor_common.txt`

- [x] **Step 1: 在第 22 条（Task 1 新增）之后新增第 23 条**

```
23. subject precision rules:
    a. When a sentence mentions both a principal and an agent/dealer performing an action (e.g. "A的代理商B成功中标"), the subject MUST be the entity performing the action (B), NOT the upstream principal (A).
    b. Do NOT replace a specific entity name with a broader/generalized name. "佐敦张家港生产基地" must NOT be replaced with "佐敦中国" unless the evidence text itself uses the broader name.
    c. When in doubt, use the EXACT entity name as it appears in the evidence text.
```

---

## Task 6: COOPERATION 语义规则 [P3] — 影响 3 条

**问题描述：**
- cooperation_type 与原文动作不匹配（"合作使用"写成 supply_agreement）
- predicate="与" 过于简单

**Files:**
- Modify: `app/prompts/fact_type_rules/cooperation.txt`

- [x] **Step 1: 在现有示例之后追加约束规则**

在 `cooperation.txt` 的 CORRECT example 之后追加：

```

## Predicate 完整性约束
- predicate must be a COMPLETE action phrase, NEVER just "与" or "和".
  ✅ "共同投资设立" / "合作使用" / "代理销售" / "完成股权合作"
  ❌ "与" / "和" / "合作" (too vague)
- If the evidence says "A与B合作成立C", predicate should be "合作成立", NOT "与".
- cooperation_type in qualifiers must EXACTLY match the cooperation nature described in the evidence:
  - "合作使用和应用" → "technology_cooperation", NOT "supply_agreement"
  - "共同投资设立" → "equity_cooperation"
  - "代理销售/授权销售" → "distribution_agreement"
  - "供应涂料/提供产品" → "supply_agreement"
  - Do NOT use "supply_agreement" when the evidence describes collaborative usage rather than supply.
```

---

## Task 7: 运行测试验证

- [x] **Step 1: 运行现有测试确认修改不破坏功能**

```bash
pytest tests/ -x
```

---

## 预期效果

| 根因 | 当前问题条数 | 修复后预期 |
|------|------------|-----------|
| Predicate 规范缺失 | 8 条 | 0-1 条 |
| 补全引入虚假信息 | 2 条（严重） | 0 条 |
| 主体归属推断过度 | 2 条（1严重） | 0 条 |
| Currency 识别不全 | 2 条 | 0 条 |
| RANKING value_text 缺失 | 3 条 | 0 条 |
| COOPERATION 语义偏差 | 3 条 | 0-1 条 |
| **总计** | **18 条问题 / 3 条严重** | **预计降至 0-2 条一般问题** |
