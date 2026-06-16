"""
Logging infrastructure with rotating file handlers.
Quick Win #4 from Implementation Roadmap.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_log_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home()))
        log_dir = Path(base) / "ActivityMonitor" / "logs"
    else:
        log_dir = Path.home() / ".activity_monitor" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


LOG_DIR = get_log_dir()

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    Create a logger with both file and console handlers.
    File handler rotates at 10 MB, keeps 5 backups.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(level)

    # --- File handler ---
    log_file = LOG_DIR / f"{name}.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))

    # --- Console handler (INFO+) ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


# Module-level loggers
tracker_log = setup_logger("tracker")
sync_log = setup_logger("sync")
db_log = setup_logger("database")
gui_log = setup_logger("gui")
