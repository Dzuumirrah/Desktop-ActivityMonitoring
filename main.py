"""
Activity Monitor – entry point.

Startup sequence:
  1. Validate/load settings
  2. Open / integrity-check database
  3. Optional startup backup
  4. Start ActivityTracker (background thread)
  5. Start SheetsSyncEngine (background thread)
  6. Schedule maintenance jobs (archival, backup, update check)
  7. Launch PySide6 GUI
"""

import sys
import os
import atexit

# Ensure project root is on sys.path regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Early logging setup ───────────────────────────────────────────────────────
from utils.logger import setup_logger
logger = setup_logger("main")


def _check_for_updates() -> None:
    """Quick Win #10 – weekly update check via GitHub releases."""
    import requests
    from config.settings import settings
    try:
        resp = requests.get(
            "https://api.github.com/repos/Dzuumirrah/Desktop-ActivityMonitoring/releases/latest",
            timeout=5,
        )
        latest = resp.json().get("tag_name", "v0.0.0").lstrip("v")
        current = settings.get("version", "1.0.0")
        if latest > current:
            logger.info(f"Update available: v{current} → v{latest}")
    except Exception as exc:
        logger.debug(f"Update check failed: {exc}")


def _schedule_jobs(db) -> None:
    """Register APScheduler maintenance jobs."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from config.settings import settings, DB_PATH
        from utils.backup import create_backup

        scheduler = BackgroundScheduler(daemon=True)

        # Daily backup at 02:00
        if settings.get("auto_backup", True):
            scheduler.add_job(
                lambda: create_backup(DB_PATH),
                "cron", hour=2, minute=0, id="daily_backup",
            )

        # Nightly archival at 02:30
        scheduler.add_job(
            lambda: db.archive_old_data(settings.get("retention_days", 90)),
            "cron", hour=2, minute=30, id="archive_old",
        )

        # Weekly update check (Sunday 06:00)
        if settings.get("check_for_updates", True):
            scheduler.add_job(
                _check_for_updates,
                "cron", day_of_week="sun", hour=6, id="update_check",
            )

        scheduler.start()
        logger.info("Maintenance scheduler started (backup, archival, update-check).")
        return scheduler
    except ImportError:
        logger.warning("APScheduler not installed; maintenance jobs disabled.")
        return None


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt

    app = QApplication(sys.argv)
    app.setApplicationName("Activity Monitor")
    app.setApplicationVersion("1.0.0")
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app.setQuitOnLastWindowClosed(False)

    # Apply global stylesheet
    from gui.main_window import APP_STYLESHEET
    app.setStyleSheet(APP_STYLESHEET)

    # ── Database ──────────────────────────────────────────────────────────────
    logger.info("Initialising database…")
    from core.database import get_db
    db = get_db()

    # ── Startup backup ────────────────────────────────────────────────────────
    from config.settings import settings, DB_PATH
    if settings.get("backup_on_startup", True):
        from utils.backup import create_backup
        create_backup(DB_PATH)

    # ── Maintenance scheduler ─────────────────────────────────────────────────
    scheduler = _schedule_jobs(db)

    # ── Activity tracker ──────────────────────────────────────────────────────
    logger.info("Starting activity tracker…")
    from core.tracker import ActivityTracker
    tracker = ActivityTracker()
    tracker.start()

    # ── Cloud sync engine ─────────────────────────────────────────────────────
    logger.info("Starting sync engine…")
    from sync.sheets import SheetsSyncEngine
    sync_engine = SheetsSyncEngine()
    # Only start background sync if already authenticated
    if (DB_PATH.parent / "google_token.json").exists():
        if sync_engine.authenticate():
            sync_engine.start()
    else:
        logger.info("Google token not found; configure sync in Settings.")

    # ── Main window ───────────────────────────────────────────────────────────
    from gui.main_window import MainWindow
    window = MainWindow(tracker=tracker, sync_engine=sync_engine)
    window.show()

        # ── Cleanup on any exit path ──────────────────────────────────────────────
    def _on_app_quit() -> None:
        """Called by Qt when app.exec() is about to return."""
        logger.info("Application quitting (aboutToQuit)…")
        # Ensure tray icon is removed even on unexpected exits
        try:
            window._tray.hide()
            window._tray.setVisible(False)
        except Exception:
            pass
        if scheduler:
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                pass

    app.aboutToQuit.connect(_on_app_quit)

    # atexit safety net for process-level crashes
    def _atexit_cleanup() -> None:
        try:
            import ctypes
            # Nothing reliable we can do for the tray here;
            # aboutToQuit covers normal exits.
        except Exception:
            pass

    atexit.register(_atexit_cleanup)

    logger.info("Application launched successfully.")
    ret = app.exec()

    logger.info("Application exiting.")
    return ret


if __name__ == "__main__":
    sys.exit(main())
