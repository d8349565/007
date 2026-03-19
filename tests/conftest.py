"""测试配置 — 统一管理测试数据库隔离"""

import os
import sys
import tempfile
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 在所有测试模块导入前，设置临时数据库路径
_test_db_path = os.path.join(tempfile.gettempdir(), "test_mvp.db")
os.environ["DATABASE_PATH_OVERRIDE"] = _test_db_path
