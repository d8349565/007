# GitHub Copilot 项目指令

所有对话包括思考都使用中文

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

## 项目架构

### 处理流程（当前：全文抽取模式）

```
文档导入 → 文本清洗 → 全文抽取(full_extractor) → 审核(reviewer) → 实体链接(entity_linker)
```

- **不再切分 chunk**，完整文章一次性交给 LLM，保留完整上下文
- `pipeline.py` 是主编排入口

### 核心数据模型

| 表 | 说明 |
|---|---|
| `source_document` | 原始文档 |
| `document_chunk` | 文本块（全文模式下为单块） |
| `evidence_span` | 证据片段（Agent1 输出） |
| `fact_atom` | 事实原子（Agent2 输出 + Agent3 审核） |
| `entity` / `entity_alias` | 标准实体表 |
| `extraction_task` | LLM 调用记录（token 统计） |
| `entity_merge_task` | 实体合并任务（规则+LLM+人工三层审核） |

### 支持的 9 类事实类型

`FINANCIAL_METRIC` / `SALES_VOLUME` / `CAPACITY` / `INVESTMENT` / `EXPANSION` / `MARKET_SHARE` / `COMPETITIVE_RANKING` / `COOPERATION` / `PRICE_CHANGE`

### 实体类型推断优先级（`entity_linker._infer_entity_type`）

1. 以"项目/工程/专项/计划"结尾 → `PROJECT`
2. 包含"新工厂/生产基地/研发基地…"且无法人后缀 → `PROJECT`
3. 集合主体关键词 → `GROUP`
4. 含公司法人后缀 → `COMPANY`
5. COOPERATION fact_type → `PROJECT`
6. 其他 → `UNKNOWN`

### 实体合并工作流（三层架构）

规则层（相似度) → LLM 分析（DeepSeek）→ 人工审核（`/manage` 页面）

关键规则：
- **括号内地区词不同**（如"（香港）" vs "（青岛）"）→ 不同注册主体，规则层直接屏蔽，不生成任务
- **工厂/基地后缀**（不含法人后缀）→ 应合并到主体品牌
- Prompt 在 `app/prompts/entity_merge.txt`，包含三条优先级规则

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
- 修改 Prompt 时，验证输出 JSON 的字段仍与 `fact_atom` 表对应
- 使用 `app/logger.py` 的 `get_logger()` 而非 `print`
- API Key、路径、机密使用 `.env` 而非硬编码

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
