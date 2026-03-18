# GitHub Copilot 项目指令

## 项目概述

**资讯颗粒化收集系统**：从行业资讯文章中自动提取结构化"事实原子"的 MVP 系统。采用 3-Agent LLM 链路（Evidence Finder → Fact Extractor → Reviewer），使用 DeepSeek API（OpenAI 兼容），SQLite 存储，Flask 审核界面。

## 角色

你是这个项目的谨慎编码助手。首要目标是做出最小化、正确、可验证的改动。优先精确而非速度。不重构不相关的代码，不改变无需改变的现有行为。

---

## 快速参考

### 常用命令（PowerShell / Windows）

```powershell
# 初始化数据库（首次必须）
python -m app.main init

# 导入并处理文件
python -m app.main import-file <path> --process

# 处理所有待处理文档
python -m app.main process --all

# 查看统计
python -m app.main stats

# 启动审核 Web 界面
python -m app.main web

# 运行所有测试
pytest tests/

# 单模块测试
pytest tests/test_pipeline.py
pytest tests/test_cleaner.py
pytest tests/test_splitter.py
```

### 虚拟环境

```powershell
# 检测虚拟环境是否存在
Test-Path .venv

# 激活
.\.venv\Scripts\Activate.ps1
```

---

## 架构

### 3-Agent 流水线（`app/services/pipeline.py`）

```
原始文章
  → clean_text()       [cleaner.py]     清洗 HTML/广告/免责
  → split_text()       [text_splitter.py] 四级切分，自适应中文字数
  → find_evidence()    [evidence_finder.py]  Agent 1：识别可抽取证据片段 → evidence_span
  → extract_facts()    [fact_extractor.py]   Agent 2：抽取结构化 fact_atom
  → review_fact()      [reviewer.py]         Agent 3：校验 PASS/REJECT/UNCERTAIN
  → batch_link_fact_atoms() [entity_linker.py] 后置：标准化实体
```

### 服务层职责（一句话）

| 文件 | 职责 |
|------|------|
| `services/pipeline.py` | 编排整个文档处理流程 |
| `services/evidence_finder.py` | Agent 1：从 chunk 中识别证据片段 |
| `services/fact_extractor.py` | Agent 2：分层加载 prompt，抽取  JSON 结构 |
| `services/reviewer.py` | Agent 3：校验每条 fact 是否被证据明确支持 |
| `services/cleaner.py` | HTML 去除、广告/导航/免责过滤、空白规范化 |
| `services/text_splitter.py` | 四级切分（章节→段落→句群），短文直通 |
| `services/entity_linker.py` | 精确/别名匹配标准化实体，未命中保留原文 |
| `services/importer.py` | 导入 txt/md/URL，content_hash 去重 |
| `services/llm_client.py` | DeepSeek API 封装，自动重试+Token 计数 |
| `models/db.py` | SQLite 连接管理，WAL 模式，外键约束 |
| `config.py` | 单例 `get_config()`，加载 config.yaml + .env |

### Prompt 管理

- 路径：`app/prompts/`
- 加载：每个服务模块用 `Path(__file__).resolve().parent.parent / "prompts"` 定位
- **分层**：`fact_extractor_common.txt`（通用规则）+ `fact_type_rules/{fact_type}.txt`（类型规则）拼接
- **不能**在代码里硬编码 prompt 文本——必须放在 .txt 文件

---

## 数据库核心表（SQLite）

| 表 | 关键字段 |
|----|---------|
| `source_document` | id, content_hash（去重键）, status, raw_text |
| `document_chunk` | id, document_id, chunk_index, chunk_text |
| `evidence_span` | id, chunk_id, fact_type, evidence_text |
| `fact_atom` | id, evidence_span_id, fact_type, subject_text, predicate, object_text, value_num, value_text, unit, currency, time_expr, qualifier_json, confidence_score, **review_status** |
| `entity` / `entity_alias` | 标准实体库+别名 |
| `extraction_task` | agent_type, status, error_message（调试用） |
| `review_log` | 审核操作日志 |

`review_status` 取值：`PENDING` / `AUTO_PASS` / `HUMAN_PASS` / `REJECTED` / `UNCERTAIN`

---

## 支持的 fact_type（9 类，来自 config.yaml 白名单）

`FINANCIAL_METRIC` · `SALES_VOLUME` · `CAPACITY` · `INVESTMENT` · `EXPANSION` · `MARKET_SHARE` · `COMPETITIVE_RANKING` · `COOPERATION` · `PRICE_CHANGE`

---

## 关键约束与陷阱

### 抽取规则（LLM 行为约束）
- **禁止补脑**：只抽 evidence 明确写出的内容，不推断、不补标题、不用常识填空。
- **一事一条**：一句话含多个事实时，必须拆成多条 fact_atom，不能合并。
- **Reviewer 只判断，不修改**：PASS/REJECT/UNCERTAIN，不允许改写 subject 或数值。
- **Entity Linking 后置**：Extractor 输出原文 subject_text；实体标准化在 pipeline 最后一步做。
- `qualifier_json` 必须是严格 JSON 对象，不能是字符串。
- 未知值填 `null`，不填 `None`（Python 序列化差异）。

### 数据层约束
- `content_hash` 去重是**覆盖式**：同文章重复导入返回同一 document_id。
- 清洗必须在切分之前，否则广告/导航会进 LLM 浪费 Token。
- `short_text_threshold: 1200`：≤1200 字的文章不切分，整篇作为一个 chunk，但仍走完整三 Agent 链路。
- 时间有三种语义，不能混淆：`publish_time`（文章发布时间）/ `time_expr`（原文时间表达）/ `time_start`·`time_end`（标准化范围）。

### 审核自动通过条件（全部满足）
- Reviewer verdict = `PASS`
- fact_type 在白名单中
- subject_text、predicate、evidence_span 均非空
- 数值类事实 value_text 非空

### 强制人工审核
- verdict = `UNCERTAIN`
- 金额 / 财务数据
- `MARKET_SHARE` / `COMPETITIVE_RANKING` 类型

---

## 修改前必做

- 确认相关代码路径、输入输出和副作用
- 阅读周围代码，理解调用契约
- 检查相关 schema、config.yaml 白名单、数据库字段
- 保持公共函数签名不变（除非明确要求）

## 修改规则

- 做能解决问题的最小变更
- 保持现有架构、命名和文件结构
- 不引入未被要求的新依赖
- 修改抽取逻辑时，验证下游 schema 兼容性
- 修改 Prompt 时，验证输出 JSON 的字段仍与 fact_atom 表对应

## 验证要求

每次非平凡改动后：
- 检查语法正确性
- 检查 import 和类型一致性
- 检查受影响的调用路径
- 检查边界和失败路径
- 运行相关 pytest 测试（若存在）
- 若无测试，增加最小验证路径

---

## 项目特定禁止事项

- 不静默吞掉异常（除非明确要求 fallback）
- 不用 `print` 替换结构化日志（`app/logger.py`）
- 不硬编码 API Key、路径或机密（使用 `.env`）
- 不在全局作用域产生副作用
- 不跳过清洗直接切分
- 不在 `extraction_task` 失败时丢弃数据（应置 UNCERTAIN）
- 不在 Prompt 文件中内联配置（配置走 config.yaml）

## 终端和环境规则

- Windows 优先使用 PowerShell
- 运行任何 Python 命令前，先确认虚拟环境是否存在（`Test-Path .venv`）
- 若存在项目虚拟环境，优先使用该环境
- 不向全局 Python 环境安装包（除非明确要求）
- 使用 pip 前，确认它属于正确环境

## 响应格式

提出或实施任何修改时，始终说明：
1. 改了什么
2. 为什么改
3. 潜在风险
4. 如何验证