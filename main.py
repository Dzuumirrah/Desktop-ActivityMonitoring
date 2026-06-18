"""
Activity Monitor – entry point.

Changes
-------
Issue 1 – live settings
  app.setQuitOnLastWindowClosed(False) prevents Qt quitting when the
  window is hidden to tray.  (Added in previous session.)

Issue 2 – sleep / hibernate
  PowerMonitor is started after the tracker so sleep/wake events are
  forwarded to tracker._on_sleep / tracker._on_wake.
"""

import sys
import os
import atexit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import setup_logger
logger = setup_logger("main")


def _check_for_updates() -> None:
    import requests
    from config.settings import settings
    try:
        resp = requests.get(
            "https://api.github.com/repos/Dzuumirrah/Desktop-ActivityMonitoring/releases/latest",
            timeout=5,
        )
        latest  = resp.json().get("tag_name", "v0.0.0").lstrip("v")
        current = settings.get("version", "1.0.0")
        if latest > current:
            logger.info(f"Update available: v{current} → v{latest}")
    except Exception as exc:
        logger.debug(f"Update check failed: {exc}")


def _schedule_jobs(db):
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from config.settings import settings, DB_PATH
        from utils.backup import create_backup

        scheduler = BackgroundScheduler(daemon=True)

        if settings.get("auto_backup", True):
            scheduler.add_job(
                lambda: create_backup(DB_PATH),
                "cron", hour=2, minute=0, id="daily_backup",
            )

        scheduler.add_job(
            lambda: db.archive_old_data(settings.get("retention_days", 90)),
            "cron", hour=2, minute=30, id="archive_old",
        )

        if settings.get("check_for_updates", True):
            scheduler.add_job(
                _check_for_updates,
                "cron", day_of_week="sun", hour=6, id="update_check",
            )

        scheduler.start()
        logger.info("Maintenance scheduler started.")
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

    # Prevent Qt from quitting when the window is hidden to the system tray.
    app.setQuitOnLastWindowClosed(False)

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

    # ── Power monitor (sleep / hibernate / wake) ──────────────────────────────
    # Must be started AFTER the tracker so the callbacks are valid.
    from utils.power_monitor import PowerMonitor
    power_monitor = PowerMonitor(
        on_sleep=tracker._on_sleep,
        on_wake=tracker._on_wake,
    )
    power_monitor.start()

    # ── Cloud sync engine ─────────────────────────────────────────────────────
    logger.info("Starting sync engine…")
    from sync.sheets import SheetsSyncEngine
    sync_engine = SheetsSyncEngine()
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
        logger.info("Application quitting (aboutToQuit)…")
        try:
            window._tray.hide()
            window._tray.setVisible(False)
        except Exception:
            pass
        power_monitor.stop()
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