# 资讯颗粒化收集系统

从行业资讯文章中自动提取结构化"事实原子"的 MVP 系统。

## 架构

采用 **3-Agent LLM 链路**：

1. **Evidence Finder** — 从文本块中识别可抽取的证据片段
2. **Fact Extractor** — 从证据中抽取结构化事实记录
3. **Reviewer** — 审核验证抽取结果的准确性

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env 填入 DeepSeek API Key

# 3. 初始化数据库
python -m app.main init

# 4. 导入文档并处理
python -m app.main import-file path/to/article.txt --process

# 5. 查看统计
python -m app.main stats

# 6. 启动审核界面
python -m app.main web
```

## 命令行用法

| 命令 | 说明 |
|------|------|
| `init` | 初始化数据库 |
| `import-file <path> [--process]` | 导入单个文件 |
| `import-url <url> [--process]` | 从 URL 导入 |
| `import-batch <dir> [--process]` | 批量导入目录 |
| `process --all` | 处理所有待处理文档 |
| `stats` | 查看统计概览 |
| `web` | 启动审核 Web 界面 |

## 支持的事实类型 (9 类)

| fact_type | 中文 |
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

## 技术栈

- Python 3.12+
- SQLite (直接 SQL)
- DeepSeek API (OpenAI 兼容)
- Flask (审核界面)
- Jupyter Notebook (查询分析)

## Docker

```bash
docker build -t fact-extractor .
docker run -p 5000:5000 -v ./data:/app/data --env-file .env fact-extractor
```
