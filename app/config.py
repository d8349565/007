"""配置加载模块：读取 config.yaml + .env"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _find_project_root() -> Path:
    """从当前文件向上查找 config.yaml 所在目录作为项目根目录"""
    current = Path(__file__).resolve().parent.parent
    if (current / "config.yaml").exists():
        return current
    # fallback: 当前工作目录
    cwd = Path.cwd()
    if (cwd / "config.yaml").exists():
        return cwd
    raise FileNotFoundError("找不到 config.yaml，请确认项目根目录")


PROJECT_ROOT = _find_project_root()


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """加载 config.yaml 并合并 .env 中的环境变量"""
    # 加载 .env
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    # 加载 config.yaml
    if config_path is None:
        config_path = str(PROJECT_ROOT / "config.yaml")

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 将 API Key 注入配置
    cfg["llm"]["api_key"] = os.getenv("DEEPSEEK_API_KEY", "")

    # 解析数据库路径为绝对路径
    db_path = cfg.get("database", {}).get("path", "data/mvp.db")
    cfg["database"]["path"] = str(PROJECT_ROOT / db_path)

    # 解析日志目录为绝对路径
    log_dir = cfg.get("logging", {}).get("log_dir", "logs")
    cfg["logging"]["log_dir"] = str(PROJECT_ROOT / log_dir)

    return cfg


# 模块级单例
_config: dict[str, Any] | None = None


def get_config() -> dict[str, Any]:
    """获取全局配置（懒加载单例）"""
    global _config
    if _config is None:
        _config = load_config()
    return _config
