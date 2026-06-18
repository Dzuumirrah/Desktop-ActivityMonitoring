"""
Logging infrastructure with rotating file handlers.
Quick Win #4 from Implementation Roadmap.

Change: session_handler (utils/session_log.py) is added to every logger
created here so ConsoleDialog can stream current-session records in real
time without reading the on-disk .log file.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_log_dir() -> Path:
    if sys.platform == "win32":
        base    = os.environ.get("APPDATA", str(Path.home()))
        log_dir = Path(base) / "ActivityMonitor" / "logs"
    else:
        log_dir = Path.home() / ".activity_monitor" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


LOG_DIR = get_log_dir()

_LOG_FORMAT  = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    Create (or return an existing) logger with:
      • rotating file handler  → LOG_DIR/<name>.log  (10 MB × 5)
      • console handler        → stdout              (INFO+)
      • session_handler        → in-memory buffer    (DEBUG+, current session)
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger   # already configured

    logger.setLevel(level)

    # ── File handler ──────────────────────────────────────────────────────────
    file_handler = RotatingFileHandler(
        LOG_DIR / f"{name}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))

    # ── Console handler ───────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # ── Session handler (in-memory, current run only) ─────────────────────────
    # Import here to avoid a circular dependency at module load time.
    # session_log.py has no project imports, so this is safe.
    from utils.session_log import session_handler
    session_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    logger.addHandler(session_handler)

    logger.propagate = False
    return logger


# ── Module-level loggers ──────────────────────────────────────────────────────
tracker_log = setup_logger("tracker")
sync_log    = setup_logger("sync")
db_log      = setup_logger("database")
gui_log     = setup_logger("gui")