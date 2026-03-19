# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

资讯颗粒化收集系统：从行业资讯文章中自动提取结构化"事实原子"的 MVP 系统。采用 3-Agent LLM 链路（Evidence Finder → Fact Extractor → Reviewer），使用 DeepSeek API（OpenAI 兼容），SQLite 存储，Flask 审核界面。

## 常用命令

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

## 项目架构

### 3-Agent LLM 链路

1. **Evidence Finder** - 从文本块中识别可抽取的证据片段
2. **Fact Extractor** - 从证据中抽取结构化事实记录
3. **Reviewer** - 审核验证抽取结果的准确性

### 处理流程 (pipeline.py)

```
文档导入 → 文本清洗 → 全文抽取(Agent1+2合并) → 审核(Agent3) → 实体链接
```

当前使用**全文抽取模式**：将清洗后的完整文章一次性交给 LLM 分析，不再切分 chunk，保留完整上下文。

### 核心数据模型

- `source_document` - 原始文档表
- `document_chunk` - 文本块表（全文模式下为单块）
- `evidence_span` - 证据片段表（Agent1 输出）
- `fact_atom` - 事实原子表（Agent2 输出 + Agent3 审核结果）
- `entity` / `entity_alias` - 标准实体表
- `extraction_task` - LLM 调用记录表（token 统计）

### 目录结构

```
app/
  models/
    db.py          # SQLite 连接和初始化
    schema.sql     # 数据库 schema
  services/
    pipeline.py         # 主处理链路编排
    full_extractor.py  # 全文抽取（Agent1+2）
    reviewer.py         # 审核（Agent3）
    entity_linker.py   # 实体链接
    cleaner.py         # 文本清洗
    llm_client.py      # DeepSeek API 封装
  prompts/
    evidence_finder.txt       # Agent1 prompt
    fact_extractor_full.txt   # Agent2 prompt（全文模式）
    reviewer.txt              # Agent3 prompt
    fact_type_rules/          # 各 fact_type 的抽取规则
  web/
    review_app.py    # Flask 审核界面
config.yaml           # 主配置文件
.env                  # API Key 等机密配置
```

## 配置管理

- **config.yaml** - 事实类型白名单、predicate 白名单、qualifier 白名单、LLM 参数、切分阈值等
- **.env** - `DEEPSEEK_API_KEY` 等机密配置
- Prompt 文件中**不内联配置**，配置统一走 config.yaml

## 支持的 9 类事实类型

| fact_type | 说明 |
|-----------|------|
| FINANCIAL_METRIC | 财务指标 |
| SALES_VOLUME | 产量/销量 |
| CAPACITY | 产能 |
| INVESTMENT | 投资 |
| EXPANSION | 扩产/扩建 |
| MARKET_SHARE | 市场份额 |
| COMPETITIVE_RANKING | 竞争排名 |
| COOPERATION | 合作 |
| PRICE_CHANGE | 价格变动 |

## 修改规则

- 做能解决问题的最小变更
- 保持现有架构、命名和文件结构
- 不引入未被要求的新依赖
- 修改抽取逻辑时，验证下游 schema 兼容性
- 修改 Prompt 时，验证输出 JSON 的字段仍与 fact_atom 表对应
- 使用 `app/logger.py` 的 `get_logger()` 而非 `print`
- API Key、路径、机密使用 `.env` 而非硬编码

## 验证要求

每次非平凡改动后：
1. 检查语法正确性
2. 检查 import 和类型一致性
3. 运行相关 pytest 测试
4. 检查边界和失败路径

## 虚拟环境

Windows 环境下使用 PowerShell：
```powershell
# 检测虚拟环境是否存在
Test-Path .venv

# 激活虚拟环境
.\.venv\Scripts\Activate.ps1
```

## 响应格式

提出或实施任何修改时，始终说明：
1. 改了什么
2. 为什么改
3. 潜在风险
4. 如何验证
