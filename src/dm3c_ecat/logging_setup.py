from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "ecat_test"
# cwd 相关路径在打包窗口态下指向不可写目录（Program Files 等）时，日志
# 会静默死亡（RotatingFileHandler 在 configure 时就抛 PermissionError，
# 或延迟到第一条记录时崩溃）。固定到用户可写的 %LOCALAPPDATA%。
DEFAULT_LOG_FILE = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    / "SYNTEC-ECAT-Test"
    / "logs"
    / "ecat-test.log"
)


def configure_logging(log_file: str | Path = DEFAULT_LOG_FILE) -> Path:
    path = Path(log_file).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)
    logger.propagate = False
    logger.info("Logging configured: %s", path)
    return path
