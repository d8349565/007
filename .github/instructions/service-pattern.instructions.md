---
description: "Use when creating or editing service functions in the services layer. Covers LLM integration, DB connection management, error handling, and task tracking patterns."
applyTo: "app/services/**/*.py"
---
# 服务函数编写规范

## 文件结构

```python
"""模块说明"""

import json
import uuid
from pathlib import Path

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection
from app.services.llm_client import get_llm_client

logger = get_logger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
```

## 函数模式

纯函数，无类，无状态。每个函数遵循：

1. **加载 Prompt** — `(_PROMPT_DIR / "xxx.txt").read_text(encoding="utf-8")`
2. **构造输入** — `json.dumps({...}, ensure_ascii=False)`
3. **调用 LLM** — `client = get_llm_client()` → `client.chat_json(system_prompt, user_input)`
4. **验证结果** — 检查返回数据结构
5. **写入 DB** — `conn.execute()` + `conn.commit()`
6. **返回结果** — 返回 `dict` 或 `list[dict]`

## DB 连接

```python
conn = get_connection()
try:
    conn.execute("INSERT INTO ... VALUES (?, ?)", (val1, val2))
    conn.commit()
finally:
    conn.close()
```

- 每函数独立获取连接，`try/finally` 确保关闭
- 用 `?` 占位符，禁止拼接 SQL
- ID 用 `str(uuid.uuid4())`

## 错误处理

```python
try:
    result = client.chat_json(system_prompt, user_input)
    _record_task_end(task_id, "success", result["input_tokens"], result["output_tokens"])
except Exception as e:
    _record_task_end(task_id, "failed", error=str(e))
    logger.error("操作失败 [id=%s]: %s", some_id[:8], e)
    return []  # 返回空值，不向上抛异常
```

## 任务追踪

涉及 LLM 调用的函数需记录到 `extraction_task` 表：
- 开始时 `_record_task_start(task_id, document_id, chunk_id, task_type)`
- 结束时 `_record_task_end(task_id, status, input_tokens, output_tokens)`
