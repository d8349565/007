---
description: "Use when creating migration scripts, data backfill scripts, or database fix scripts. Covers dry-run pattern, argparse, and DB connection management."
applyTo: "scripts/**/*.py"
---
# 迁移/修复脚本规范

## 文件结构

```python
"""脚本说明：做什么 + 逻辑概要"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import get_connection
from app.logger import get_logger

logger = get_logger(__name__)
```

## 核心函数

```python
def migrate_xxx(dry_run: bool = True) -> int:
    conn = get_connection()
    updated = 0
    try:
        rows = conn.execute("SELECT ...").fetchall()
        for row in rows:
            if not dry_run:
                conn.execute("UPDATE ... WHERE id=?", (row["id"],))
            updated += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return updated
```

## 入口

```python
def main():
    import argparse
    parser = argparse.ArgumentParser(description="脚本说明")
    parser.add_argument("--apply", action="store_true", help="实际执行（默认 dry-run）")
    args = parser.parse_args()

    dry_run = not args.apply
    count = migrate_xxx(dry_run=dry_run)

    mode = "[DRY-RUN]" if dry_run else "[APPLIED]"
    print(f"{mode} 影响 {count} 条记录")

if __name__ == "__main__":
    main()
```

## 关键约定

- **默认 dry-run**：不带 `--apply` 时只统计不修改
- **幂等**：重复执行不产生副作用
- **日志**：用 `logger.debug()` 记录每条变更细节
- **SQL 安全**：`?` 占位符，禁止字符串拼接
