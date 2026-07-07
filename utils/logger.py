"""日志配置模块"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler


def setup_logger(log_dir: str = "logs", level: str = "INFO", max_days: int = 30):
    """配置全局日志器，按天滚动，保留 max_days 天。

    Args:
        log_dir: 日志目录
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        max_days: 保留天数
    """
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("monitor")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 文件handler：按天滚动
    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, "monitor.log"),
        when="midnight",
        interval=1,
        backupCount=max_days,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"

    # 控制台handler
    console_handler = logging.StreamHandler()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def get_logger():
    """获取已配置的日志器"""
    return logging.getLogger("monitor")
