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
