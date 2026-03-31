# Project Guidelines

## Overview

资讯颗粒化收集系统 — 从行业资讯中提取结构化"事实原子"。3-Agent LLM 链路：Evidence Finder → Fact Extractor → Reviewer，外加 Entity Linking 和 Review UI。

## Code Style

- **语言**: Python 3.12+，中文注释和提示词
- **服务层**: 纯函数式，无状态，无类。每个服务文件导出独立函数
- **命名**: 函数 `snake_case`，常量 `UPPER_SNAKE_CASE`，模块级 logger
- **类型**: 函数返回 `dict` 或 `list[dict]`，不使用 dataclass/Pydantic
- **import**: 标准库 → 第三方 → 本地（`from app.models.db import get_connection`）

## Architecture

```
app/
├── main.py            # CLI 入口 (argparse)
├── config.py          # config.yaml + .env 单例加载
├── logger.py          # RotatingFileHandler 统一日志
├── models/
│   ├── db.py          # SQLite 连接（WAL 模式、Row factory）
│   └── schema.sql     # 全部建表语句
├── prompts/           # LLM 提示词（.txt 纯文本，中文）
├── services/          # 业务逻辑（纯函数，无状态）
│   ├── pipeline.py    # 编排: import → clean → extract → review → link
│   ├── llm_client.py  # OpenAI 兼容 API 封装 (DeepSeek/Kimi/MiniMax)
│   └── ...            # 各服务模块
└── web/               # Flask 审核界面 + API
    ├── review_app.py  # 路由与模板
    └── api_tasks.py   # 任务状态 Blueprint
```

### 关键约定

- **DB 连接**: 每个函数内 `conn = get_connection()` → `try/finally conn.close()`，无连接池
- **LLM 调用**: `client.chat_json(system_prompt, user_input)` 返回解析后的 JSON，自动重试 3 次
- **Prompt 加载**: `Path(__file__).parent.parent / "prompts" / "xxx.txt"` 读取纯文本
- **错误处理**: 服务函数内部 catch 所有异常，`logger.error()` 记录后返回空 list/dict，不向上抛
- **任务追踪**: `extraction_task` 表记录每步状态（running → success/failed）+ token 计数

## Build and Test

```bash
# 安装依赖
pip install -r requirements.txt

# 初始化数据库
python -m app.main init

# 运行测试（测试自动使用临时 DB，通过 DATABASE_PATH_OVERRIDE）
pytest tests/

# 启动 Web 审核界面
python -m app.main web

# Docker
docker build -t fact-extractor .
docker run -p 5000:5000 -v ./data:/app/data --env-file .env fact-extractor
```

## Conventions

- **新服务函数**模式: 加载 prompt → 调用 LLM → 验证结果 → 写入 DB → 返回结果 dict
- **数据库操作**: 直接 `conn.execute(SQL)` 原生 SQL，不使用 ORM。用 `?` 占位符防注入
- **ID 生成**: `str(uuid.uuid4())` 作为主键
- **配置**: 所有可配置项在 `config.yaml`，敏感信息在 `.env`（API Key 等）
- **迁移脚本**: 放 `scripts/` 目录，用 `--apply` flag 区分 dry-run / 实际执行
- **Web 模板**: Jinja2 + Bootstrap，模板在 `app/web/templates/`
- **事实类型**: 9 种固定类型（见 README），每种有对应 `prompts/fact_type_rules/*.txt`

## Documentation

设计文档和计划在 `docs/superpowers/` 下，按日期命名：

- `specs/` — 功能设计文档
- `plans/` — 实施计划


# 修改前必做

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
