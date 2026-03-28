---
description: "Add a new fact type to the system — generates checklist of all files to update"
agent: "agent"
argument-hint: "Fact type name, e.g. PRODUCT_LAUNCH 产品发布"
---
添加新事实类型到系统中。用户会提供英文类型名和中文名称。

请按以下清单逐步完成所有变更：

## 必须修改的文件

1. **`app/models/schema.sql`** — 在 `fact_atom.fact_type` 的注释中添加新类型
2. **`config.yaml`** — 在 `fact_types` 列表中添加新类型名
3. **`app/prompts/fact_type_rules/`** — 创建 `<type_name>.txt` 提示词文件，参考同目录下已有文件的格式
4. **`README.md`** — 在"支持的事实类型"表格中添加新行
5. **`app/web/review_app.py`** — 在 `FACT_TYPE_NAMES` 映射中添加中文名

## 执行步骤

1. 先读取一个已有的 fact_type_rules 文件（如 `app/prompts/fact_type_rules/investment.txt`）了解格式
2. 读取 `config.yaml` 确认当前 fact_types 列表
3. 逐个文件完成修改
4. 最后总结所有变更
