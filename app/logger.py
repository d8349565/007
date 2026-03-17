"""日志配置模块：logging 标准库 + 文件持久化"""

import logging
import os
from logging.handlers import RotatingFileHandler

from app.config import get_config


_logger_initialized = False


def setup_logging() -> logging.Logger:
    """配置全局日志：控制台 + 文件轮转"""
    global _logger_initialized

    cfg = get_config().get("logging", {})
    level = getattr(logging, cfg.get("level", "INFO").upper(), logging.INFO)
    log_dir = cfg.get("log_dir", "logs")
    max_bytes = cfg.get("max_bytes", 10 * 1024 * 1024)
    backup_count = cfg.get("backup_count", 5)

    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("fact_extract")

    if _logger_initialized:
        return logger

    logger.setLevel(level)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件（带轮转）
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "fact_extract.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _logger_initialized = True
    return logger


def get_logger(name: str = "fact_extract") -> logging.Logger:
    """获取子 logger"""
    setup_logging()
    return logging.getLogger(name)
