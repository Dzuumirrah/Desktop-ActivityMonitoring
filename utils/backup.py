"""
Backup utility: daily automated SQLite backups.
Addresses Section 4.1 (No Backup/Recovery Strategy) from Project Analysis.
"""

import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from utils.logger import setup_logger

logger = setup_logger("backup")


def get_backup_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home()))
        backup_dir = Path(base) / "ActivityMonitor" / "backups"
    else:
        backup_dir = Path.home() / ".activity_monitor" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


BACKUP_DIR = get_backup_dir()
MAX_BACKUPS = 30
MAX_TOTAL_MB = 1024  # 1 GB ceiling


def create_backup(db_path: Path) -> Path | None:
    """
    Copy the SQLite database to the backup directory.
    Filename format: activity_monitor_YYYY-MM-DD_HHMMSS.db
    """
    if not db_path.exists():
        logger.warning(f"Database not found at {db_path}; skipping backup.")
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUP_DIR / f"activity_monitor_{timestamp}.db"
    try:
        shutil.copy2(db_path, dest)
        logger.info(f"Backup created: {dest} ({dest.stat().st_size // 1024} KB)")
        _cleanup_old_backups()
        return dest
    except OSError as exc:
        logger.error(f"Backup failed: {exc}")
        return None


def _cleanup_old_backups() -> None:
    """Remove oldest backups once count > MAX_BACKUPS or total size > MAX_TOTAL_MB."""
    backups = sorted(BACKUP_DIR.glob("activity_monitor_*.db"), key=lambda p: p.stat().st_mtime)

    # Enforce count limit
    while len(backups) > MAX_BACKUPS:
        old = backups.pop(0)
        old.unlink(missing_ok=True)
        logger.info(f"Removed old backup: {old.name}")

    # Enforce size limit
    total_bytes = sum(p.stat().st_size for p in backups)
    while total_bytes > MAX_TOTAL_MB * 1024 * 1024 and backups:
        old = backups.pop(0)
        total_bytes -= old.stat().st_size
        old.unlink(missing_ok=True)
        logger.info(f"Removed backup to stay under size limit: {old.name}")


def get_latest_backup() -> Path | None:
    """Return the most recent backup file, or None if none exist."""
    backups = sorted(BACKUP_DIR.glob("activity_monitor_*.db"), key=lambda p: p.stat().st_mtime)
    return backups[-1] if backups else None


def restore_backup(db_path: Path, backup_path: Path | None = None) -> bool:
    """
    Restore database from a backup.
    Uses the latest backup if backup_path is not specified.
    """
    source = backup_path or get_latest_backup()
    if not source:
        logger.error("No backup available to restore.")
        return False

    # Keep the corrupted file for manual inspection
    corrupt_dest = db_path.with_suffix(".corrupted")
    if db_path.exists():
        shutil.copy2(db_path, corrupt_dest)
        logger.warning(f"Corrupted DB saved to: {corrupt_dest}")

    shutil.copy2(source, db_path)
    logger.info(f"Database restored from {source.name}")
    return True


def list_backups() -> list[dict]:
    """Return metadata for all backups, newest first."""
    backups = sorted(
        BACKUP_DIR.glob("activity_monitor_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    result = []
    for p in backups:
        stat = p.stat()
        result.append(
            {
                "path": str(p),
                "name": p.name,
                "size_kb": stat.st_size // 1024,
                "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )
    return result
