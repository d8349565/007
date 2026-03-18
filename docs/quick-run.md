# 快速运行指南

## 快速测试命令

```bash
# 方式1: 直接用 python -c 执行（适合简单操作）
cd f:/Python/007
python -c "import sys; sys.stdout.reconfigure(encoding='utf-8'); from app.models.db import init_db, get_connection; init_db(); conn = get_connection(); conn.execute('PRAGMA foreign_keys=OFF'); conn.execute('DELETE FROM fact_atom'); conn.execute('DELETE FROM evidence_span'); conn.execute('DELETE FROM document_chunk'); conn.execute('DELETE FROM source_document'); conn.execute('DELETE FROM entity_alias'); conn.execute('DELETE FROM entity'); conn.execute('DELETE FROM review_log'); conn.execute('DELETE FROM extraction_task'); conn.commit(); conn.close(); print('[OK] Done')"

# 方式2: 使用 PYTHONIOENCODING=utf-8 避免编码问题
cd f:/Python/007
PYTHONIOENCODING=utf-8 python -m app.main import-url "https://www.sohu.com/a/940360459_425738" --process
```

## 避免卡住的技巧

1. **始终设置编码**: `PYTHONIOENCODING=utf-8`
2. **后台运行**: 使用 `run_in_background=true` 参数
3. **使用 python -c**: 对于简单命令更高效

## 查看结果

```bash
cd f:/Python/007
python -c "import sys; sys.stdout.reconfigure(encoding='utf-8'); from app.models.db import get_connection; conn = get_connection(); print('Docs:', conn.execute('SELECT COUNT(*) FROM source_document').fetchone()[0]); print('Facts:', conn.execute('SELECT COUNT(*) FROM fact_atom').fetchone()[0]); conn.close()"
```
