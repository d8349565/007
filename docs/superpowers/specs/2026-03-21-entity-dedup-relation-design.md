# 实体去重与关系处理重构设计方案

**日期：** 2026-03-21
**目标：** 提升 AI 建议质量，优先解决代码"乱"的问题
**状态：** 评审通过，待用户确认

---

## 1. 背景与问题

### 1.1 现有代码的"乱"象

**重复逻辑（严重）：**
- 规范化函数至少 3 处独立实现：`_normalize_name()` / `_normalize()` / `_strip_legal()` 逻辑完全相同
- 包含度评分函数 2 处重复实现：公式相同但未共享
- 常量词表分散：`_GEO_QUALIFIERS` 在 merger、`_SKIP_NAMES` 在 merger、`_LOCATION_KEYWORDS` 在 linker

**职责边界模糊：**
- `entity_linker.py` 与 `entity_analyzer.py` 在合并建议和关系建议上功能重叠
- `entity_merger.py` 与 `entity_analyzer.py` 在候选生成上重复
- `review_app.py` 包含 100+ 行应属于 Service 层的业务逻辑

**演进中代码：**
- `entity_analyzer.py` 和 `web_searcher.py` 是新增未提交文件，系统架构尚未稳定

### 1.2 业务痛点

用户反馈 `/manage` 页面四大痛点全占：
1. 人工操作繁琐 — 缺少批量智能辅助
2. AI 建议不可信 — 准确率不足
3. 信息看不清 — 难以做决策
4. 流程不闭环 — 建议→审核→执行有断点

**本次重构聚焦：提升 AI 建议质量**

---

## 2. 核心设计原则

### 2.1 三条铁律

1. **消歧优先于合并** — 名称只是线索，不是结论。先判断"这是哪个佐敦"，再决定"要不要合并"
2. **证据分级，置信度驱动** — 低置信度不合并，只建议；高置信度才自动执行
3. **文章上下文是元数据** — 实体不孤立存在，它依附于文章来源，合并建议必须携带上下文解释

### 2.2 关键洞察：同名称实体消歧

> 例如"佐敦"在不同的文章中可能指代完全不同的实体：
> - 文章 A 的"佐敦" = 挪威 Jotun AS（全球涂料品牌，外资）
> - 文章 B 的"佐敦" = 中远佐敦船舶涂料（合资公司）
>
> 如果用统一名称合并，会把两个不同实体错误合并，或把同一实体当作两个

**设计影响：** 合并建议必须携带来源上下文 + 消歧信号，不能仅靠名称相似度。

---

## 3. 实体分类体系

### 3.1 多标签 + 主类型

每个实体可以同时拥有多个类型标签，并有一个优先级最高的**主类型**。

| 优先级 | 标签 | 说明 |
|--------|------|------|
| 1 | GROUP / 集团 | 包含多家子公司的大型企业集合 |
| 2 | COMPANY / 公司 | 独立法人企业 |
| 3 | BRAND / 品牌 | 商业品牌标识（如"比亚迪汽车"） |
| 4 | INDIVIDUAL / 个人 | 自然人 |
| 5 | PROJECT / 项目 | 非永久性项目标的 |
| 6 | INDUSTRY / 行业 | 业务领域（"新能源汽车"） |
| 7 | SEGMENT / 细分行业 | 细分赛道（"动力电池"） |
| 8 | MARKET / 赛道 | 行业赛道（"动力电池赛道"） |
| 9 | REGION / 区域 | 地理区域（"华东"、"挪威"） |

**多标签示例：**
- "比亚迪集团" = 主类型:集团 + 标签:公司,品牌,赛道
- "比亚迪股份有限公司" = 主类型:公司 + 标签:集团成员
- "Jotun AS" = 主类型:公司 + 标签:品牌,行业
- "中远佐敦" = 主类型:公司 + 标签:品牌,合资

**重要区分：集团层级关系 vs 多标签**

> "比亚迪集团"和"比亚迪股份有限公司"是**两个不同实体**，它们的关系应通过 `entity_relation(from=比亚迪集团, to=比亚迪股份, type=SUBSIDIARY)` 建模，而不是用多标签合并到一个实体上。

- **多标签**：描述单个实体的多重属性（如"比亚迪集团"既是集团又是品牌）
- **entity_relation**：描述多个实体之间的层级/业务关系

---

## 4. 整体架构

### 4.1 目标架构

```
app/services/
├── entity_utils.py          # 新增：统一基础工具层
│
├── entity_linker.py          # 实体 CRUD + 消歧
│
├── entity_merger.py          # 合并任务 + 执行
│
└── entity_analyzer.py        # 唯一 AI 建议入口（重构）
```

### 4.2 模块职责

| 模块 | 职责 | 对外 API |
|------|------|---------|
| `entity_utils.py` | 规范化、包含度评分、类型常量、词表 | 纯函数，无状态 |
| `entity_linker.py` | 实体 CRUD、别名管理、链接事实原子、**名称消歧** | `link_entity`, `get_or_create_entity`, `disambiguate` |
| `entity_merger.py` | 合并任务管理、合并执行（携带来源上下文） | `create_merge_task`, `approve_merge`, `execute_merge` |
| `entity_analyzer.py` | 唯一 AI 建议入口：合并建议 + 关系建议，统一三重证据收集 | `analyze_merge`, `analyze_relation` |

### 4.3 现有函数替代关系

| 现有函数 | 状态 | 替代/补充方案 |
|----------|------|--------------|
| `entity_linker.ai_suggest_relations()` | 废弃 | `analyzer.analyze_relation()` 批量分析模式 |
| `entity_merger.get_merge_suggestions()` | 迁移至 analyzer | `analyzer.analyze_merge()` |
| `entity_analyzer.analyze_entity()` | 重构 | 三重证据 + 消歧版 |

### 4.4 与现有架构对比

```
现有（乱）                              目标（清晰）
───────────────────────────────────────────────────────────────
entity_linker.py     ┐                entity_utils.py ← 唯一基础层
  _normalize_name()  ├─ 重复代码       ┗ normalize(), contain_score(),
entity_merger.py     │                 LEGAL_SUFFIXES, GEO_QUALIFIERS,
  _normalize()       ┤                 SKIP_NAMES, RELATION_TYPES
entity_analyzer.py   │
  _strip_legal()     ┘                  entity_linker.py ← 消歧优先
                                        entity_merger.py  ← 合并执行
                                        entity_analyzer.py ← 唯一AI入口
```

---

## 5. entity_utils.py：统一基础层

### 5.1 核心函数

```python
# 唯一规范化函数
def normalize(name: str) -> str:
    """去括号 + 去法人后缀 + 去首尾空格"""
    s = _PAREN_RE.sub('', name).strip()
    for suffix in LEGAL_SUFFIXES:
        if s.endswith(suffix):
            s = s[:-len(suffix)].strip()
    return s

# 唯一包含度评分
def contain_score(a: str, b: str) -> float:
    """返回 [0,1] 相似度：相等=1.0，包含=0.5~0.95"""
    ...

# 指纹：用于快速去重检测
def fingerprint(name: str) -> str:
    """返回 normalize 后的字符串，用于唯一性判断"""
    return normalize(name)

# 实体类型推断：返回 (primary_type, tags) 元组
def infer_entity_type(name: str, fact_type: str = "") -> tuple[str, list[str]]:
    """
    根据名称和 fact_type 推断实体的主类型和多标签。
    例如：输入"比亚迪集团"，返回 ("GROUP", ["company", "brand", "market"])
    """
    ...
```

### 5.2 常量词表

| 常量 | 用途 | 来源 |
|------|------|------|
| `LEGAL_SUFFIXES` | 法人后缀词表（有限公司、Inc. 等） | 合并 linker + merger 中的版本 |
| `GEO_QUALIFIERS` | 地理修饰词（华东、华南、挪威等） | 迁自 merger |
| `SKIP_NAMES` | 跳过词（"公司"、"集团"等泛指词） | 迁自 merger |
| `RELATION_TYPES` | 关系类型常量 | 合并 linker + analyzer 中的分散定义 |
| `ENTITY_PRIMARY_TYPES` | 主类型优先级映射 | 新增 |

---

## 6. 实体消歧设计

### 6.1 消歧 API

```python
def disambiguate(name: str, context: DisambiguateContext = None) -> DisambiguationResult:
    """
    返回消歧结果：

    有上下文时（context 非空）：
      - candidates: 多个候选实体（按置信度排序）
      - ambiguity_note: 消歧解释（如"需注意避免与中远佐敦混淆"）
      - fallback_action: "create_new" | "require_manual" | "match_best"

    无上下文时（核心边界情况）：
      1. 搜索所有同名实体，返回候选列表
      2. ambiguity_note 明确标注"缺少消歧上下文，需人工确认"
      3. fallback_action = "require_manual"（默认不自动创建同名新实体）
      4. 如果已有同名实体，倾向匹配（避免重复创建），但注明风险
    """
```

### 6.2 消歧信号

| 信号来源 | 说明 |
|----------|------|
| `fact_atom.object_qualifier` | 公司性质词（外资/合资/民营） |
| 关联实体共现 | 谁与它同时出现在一个 fact 中 |
| 来源文章上下文 | `source_document` 的标题/摘要 |
| 网络搜索证据 | `web_searcher.py` 的外部知识 |

### 6.3 消歧时机

- **实体入库时** — `get_or_create_entity` 调用消歧，检查是否存在同名歧义实体
- **合并建议前** — 在判断"是否合并"之前，先输出"这是哪个实体"的候选列表
- **关系建议时** — 确保关系两端实体都被正确消歧

### 6.4 消歧缓存管理

消歧结果依赖 qualifier 和关联实体共现（动态数据），当 `fact_atom` 更新时缓存可能过期：

| 缓存策略 | 说明 |
|----------|------|
| TTL | 消歧结果缓存有效期 7 天 |
| 失效触发 | 当 `fact_atom.qualifier_json` 或关联实体变化时，清除相关消歧缓存 |
| 失效接口 | `cache.invalidate(entity_name)` — 按名称清除指定缓存 |
| 批量失效 | 实体合并执行后，清除被合并实体的所有缓存 |

### 6.5 同名实体合并触发条件

同名实体的合并建议在以下条件满足时触发：

| 触发条件 | 说明 |
|----------|------|
| 关联实体共现 | 两个候选实体在同一个 `fact_atom` 中同时作为 subject/object/object_qualifier 出现 |
| predicate 一致 | 共现时 predicate 相同（说明业务场景一致） |
| 消歧后指向同一实体 | 经过 `disambiguate()` 消歧后，两个候选指向同一个 canonical_name |
| 人工标记 | 用户在界面上标记"这两个是同一实体" |

---

## 7. 证据增强的合并建议

### 7.1 三重证据

```
┌─────────────────────────────────────────────────────────┐
│              entity_analyzer.py (证据中枢)                │
│                                                         │
│  1. 名称证据 (w=0.3)   2. 事实证据 (w=0.4)  3. 网络证据 (w=0.3) │
│  ┌───────────┐      ┌─────────────┐    ┌───────────┐   │
│  │名称相似度  │      │同文章共现   │    │DuckDuckGo │   │
│  │包含度评分  │      │predicate   │    │搜索+LLM解读│   │
│  │规范化指纹  │      │一致        │    │          │   │
│  └───────────┘      └─────────────┘    └───────────┘   │
│                                                         │
│  消歧上下文: 来源文章 + qualifier + 关联企业              │
│                   │                                    │
│         ┌─────────▼──────────┐                        │
│         │  证据聚合 + LLM 综合  │                        │
│         └─────────┬──────────┘                        │
│                   ▼                                    │
│         confidence + suggestion                         │
└─────────────────────────────────────────────────────────┘
```

### 7.2 合并判断示例

```
输入：候选实体 A ("佐敦") 和候选实体 B ("Jotun AS")

证据收集：
├─ 名称证据 (0.7)     — 规范化后相似，但"佐敦"是简称，不足以确认
├─ 事实共现 (0.85)   — 两个实体在多篇文章中共同出现，predicate 一致
├─ 网络证据 (0.9)     — 搜索"佐敦 Jotun"返回明确关联
└─ 消歧上下文 (0.8)   — A 的 qualifier 含"挪威"，B 含"涂料品牌"，指向同一实体

LLM 综合判断：
→ "A 是 Jotun AS 在中国市场报道中的简称或简称组合"
→ confidence: 0.88
→ ambiguity_note: "需注意避免与中远佐敦混淆"
→ 决策：建议人工确认
```

---

## 8. 三层置信度决策

### 8.1 置信度来源

每条建议的置信度由 LLM 综合判断，参考三重证据输入：
- **名称证据** (w=0.3) — 规范化相似度、包含度
- **事实证据** (w=0.4) — 同文章共现、predicate 一致性
- **网络证据** (w=0.3) — DuckDuckGo 搜索 + LLM 解读

### 8.2 决策规则

```
自动执行条件（三者需同时满足）：
  1. 三重证据综合评分 ≥ 0.75
  2. 消歧无冲突（候选实体唯一或主候选置信度 > 0.9）
  3. 非 skip 类型建议（skip = AI 判断该实体无需任何操作）

人工审核范围：
  - 综合评分 0.4–0.75

丢弃/降权：
  - 综合评分 < 0.4

无上下文场景：
  - 当 DisambiguateContext 为空时，fallback_action = "require_manual"
  - 即使名称完全匹配，也不触发自动执行，需人工确认
  - （避免同名称不同实体的误合并）
```

| 置信度 | 动作 | 说明 |
|--------|------|------|
| ≥ 0.75 且无上下文冲突 | 自动执行 | 三重证据强 + 消歧无冲突 |
| 0.4–0.75 | 人工审核 | 提供完整证据摘要 |
| < 0.4 | 丢弃或降权 | 仅记录，不推送建议 |
| 无上下文（任何置信度） | 人工审核 | fallback_action = require_manual |

---

## 9. Web 层最小化改造

`review_app.py` 中的实体管理 API 改为调用服务层：

| 现有实现 | 目标实现 |
|----------|----------|
| `api_dedup_batch_rename()` — 122 行混杂逻辑 | 调用 `analyzer.analyze_merge()` + `merger.execute_merge()` |
| API 内含业务判断 | API 只做：参数校验 + 路由 + 响应格式化 |

Web 层保留：API 路由、参数校验、权限判断、响应格式化
迁出：合并/去重的业务判断逻辑

---

## 10. 数据库 Schema 调整

### 10.1 新增字段

```sql
ALTER TABLE entity_relation_suggestion
  ADD COLUMN source_document_id INTEGER,
  ADD COLUMN ambiguity_note TEXT;

ALTER TABLE entity
  ADD COLUMN primary_type TEXT,
  ADD COLUMN tags TEXT;  -- JSON 数组，如 '["brand", "industry"]'

ALTER TABLE entity_alias
  ADD COLUMN alias_type TEXT DEFAULT 'alias',
  -- 'primary' | 'alias' | 'acronym' | 'formal'

ALTER TABLE fact_atom
  ADD COLUMN source_document_id INTEGER REFERENCES source_document(id);
```

### 10.2 entity_type 迁移计划

现有 `entity.entity_type` 值：`COMPANY | GROUP | PROJECT | REGION | COUNTRY | UNKNOWN`

| 旧值 | 新 primary_type | 新增 tags |
|------|----------------|-----------|
| GROUP | GROUP | - |
| COMPANY | COMPANY | - |
| PROJECT | PROJECT | - |
| REGION | REGION | - |
| COUNTRY | REGION | ["country"] |
| UNKNOWN | 待推断 | 待推断 |

**迁移脚本** `scripts/migrate_entity_type.py`：

```python
# 1. 复制旧 entity_type → primary_type
# 2. 对 UNKNOWN 类型，调用 infer_entity_type() 批量重新推断
# 3. 保留旧 entity_type 字段一段时间（兼容），后续版本删除
```

### 10.3 说明

- `source_document_id` — fact_atom 知道自己来自哪篇文章，消歧和合并时提供上下文
- `ambiguity_note` — 当名称存在多义性时，记录消歧解释（如"需注意避免与中远佐敦混淆"）
- `primary_type` + `tags` — 实体多标签体系
- `alias_type` — 别名层级（primary=主名称、alias=普通别名、acronym=缩写、formal=法律全称），消歧时优先匹配 primary

---

## 11. 重构步骤（待实施计划细化）

### 阶段一：统一基础层（无依赖，可独立进行）
1. 新建 `entity_utils.py`，实现 `normalize()` / `contain_score()` / `fingerprint()` / `infer_entity_type()`
2. 迁移 `LEGAL_SUFFIXES` / `GEO_QUALIFIERS` / `SKIP_NAMES` / `RELATION_TYPES`
3. 更新 `entity_linker.py` / `entity_merger.py` / `entity_analyzer.py` 引用 `entity_utils`
4. **验证：** 运行全部 pytest，确保行为不变

### 阶段二：消歧优先（依赖阶段一）
5. 执行 Schema 迁移脚本（新增字段 + entity_type 迁移）
6. 在 `entity_linker.py` 中实现 `disambiguate()` 函数（含无上下文 fallback）
7. 修改 `get_or_create_entity()` 为带消歧的版本
8. **验证：** 抽样验证 10 个典型歧义名称的消歧结果

### 阶段三：AI 建议收敛（依赖阶段一 + 阶段二）
9. 废弃 `linker.ai_suggest_relations()`，确认由 `analyzer.analyze_relation()` 承接
10. 将 `entity_merger.py` 中的 `get_merge_suggestions()` 迁至 `entity_analyzer.py`
11. 在 `entity_analyzer.py` 中实现三重证据聚合 + 无上下文 fallback
12. 更新 `entity_merge.txt` 和 `entity_relation_analysis.txt` prompt
13. 实现三层置信度决策逻辑（阈值 0.75，无上下文场景需人工审核）
14. **验证：** 对比新旧 AI 建议结果，确认改进

### 阶段四：Web 层清理（依赖阶段三，可独立测试）
15. 迁出 `review_app.py` 中的业务逻辑到服务层
16. 简化 API 路由，专注于路由职责
17. **验证：** 手动测试 `/manage` 页面的所有 API

---

## 12. 风险与注意事项

| 风险 | 影响 | 缓解 |
|------|------|------|
| `entity_analyzer.py` 是未提交的新文件 | 可能与已有逻辑有未发现的耦合 | 重构前先完整测试现有流程 |
| `review_app.py` 改动面广 | Web 层测试需要同步更新 | 最小化改动，分阶段验证 |
| 消歧逻辑依赖 qualifier 质量 | 如果 qualifier 缺失，消歧效果下降 | 无上下文时 fallback_action = "require_manual"，不阻塞入库 |
| 三重证据中的网络搜索增加耗时 | 批量建议生成变慢 | 搜索结果可缓存（已有 web_searcher.py 的缓存机制） |
| Schema 迁移涉及 UNKNOWN 类型推断 | 可能推断不准确 | UNKNOWN 实体标记为待处理，人工审核确认 |
| 置信度阈值从 0.85 改为 0.75 | 自动合并范围扩大 | 先在实际数据上验证，再决定是否扩大自动执行范围 |

---

## 13. 验收标准

1. **代码去重** — 规范化函数只剩 1 个实现，评分函数只剩 1 个
2. **职责清晰** — 每个服务模块的职责在 3 行内可说清楚
3. **消歧可用** — `disambiguate("佐敦")` 能返回多个候选 + 消歧解释，无上下文时 fallback_action = "require_manual"
4. **AI 建议带证据** — 每条建议附带名称/事实/网络三重证据摘要
5. **置信度分级** — ≥0.75 + 无冲突的建议能自动执行，<0.4 的不干扰人工，无上下文场景需人工审核
6. **Web 层瘦身前** — `review_app.py` 中的业务逻辑全部迁到服务层
