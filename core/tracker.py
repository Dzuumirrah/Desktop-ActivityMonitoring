"""
Activity Tracker: background polling loop.

Responsibilities:
  - Poll active window every N seconds
  - Detect idle / resume transitions
  - Delegate session-boundary decisions to SessionManager
  - Apply privacy rules before persistence
  - Ping health check every 60 s
  - Emit Qt signals so the GUI updates in real-time
"""

import socket
import threading
import time
import uuid
from datetime import datetime
from typing import Callable, Optional

from config.settings import settings
from core.database import get_db, generate_activity_id
from core.idle_detector import IdleDetector
from core.models import ActivityRecord
from core.session_manager import SessionManager, WindowInfo, get_active_window
from privacy.manager import privacy_manager
from utils.logger import tracker_log as logger


class ActivityTracker:
    """
    Run in a background thread via .start() / .stop().

    Emits callbacks:
      on_session_saved(activity_dict)
      on_idle_changed(is_idle, reason)
    """

    HEALTH_PING_INTERVAL = 60   # seconds

    def __init__(self) -> None:
        self._db              = get_db()
        self._session_mgr     = SessionManager()
        self._idle_detector   = IdleDetector()
        self._thread: Optional[threading.Thread] = None
        self._stop_event      = threading.Event()
        self._paused          = False
        self._idle_active     = False
        self._last_health_ping = 0.0

        # Callbacks (set by GUI or sync engine)
        self.on_session_saved: Optional[Callable[[dict], None]] = None
        self.on_idle_changed:  Optional[Callable[[bool, Optional[str]], None]] = None

        # Device identity
        self._device_name = settings.get("device_name") or socket.gethostname()
        self._device_id   = self._get_or_create_device_id()

        logger.info(
            f"Tracker initialized | device={self._device_name} | "
            f"polling={settings.get('polling_interval')}s | "
            f"idle_threshold={settings.get('idle_timeout')}s"
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.warning("Tracker already running.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="ActivityTracker", daemon=True
        )
        self._thread.start()
        logger.info("Tracker started.")

    def stop(self) -> None:
        """Cleanly flush the current session before exiting."""
        logger.info("Tracker stopping…")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        # Save whatever was open
        closed = self._session_mgr.force_close()
        if closed:
            self._persist_session(closed.window, closed.start_time, datetime.now())
        self._idle_detector.stop()
        logger.info("Tracker stopped.")

    def pause(self) -> None:
        self._paused = True
        logger.info("Tracker paused.")

    def resume(self) -> None:
        self._paused = False
        self._idle_detector.reset()
        logger.info("Tracker resumed.")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        poll_sec = settings.get("polling_interval", 1)
        while not self._stop_event.is_set():
            try:
                if not self._paused:
                    self._tick()
            except Exception as exc:
                logger.error(f"Tracker tick error: {exc}", exc_info=True)
            self._stop_event.wait(timeout=poll_sec)

    def _tick(self) -> None:
        now = time.monotonic()

        # ── Health ping ───────────────────────────────────────────────────────
        if now - self._last_health_ping >= self.HEALTH_PING_INTERVAL:
            self._db.ping_health()
            self._last_health_ping = now

        # ── Idle detection ────────────────────────────────────────────────────
        is_idle, reason = self._idle_detector.is_idle()
        if is_idle != self._idle_active:
            self._idle_active = is_idle
            logger.info(f"Idle state → {'IDLE' if is_idle else 'ACTIVE'} ({reason})")
            if self.on_idle_changed:
                self.on_idle_changed(is_idle, reason)

            if is_idle:
                # Close open session when going idle
                closed = self._session_mgr.force_close()
                if closed:
                    self._persist_session(
                        closed.window, closed.start_time, datetime.now(),
                        is_idle=True, idle_reason=reason
                    )
                return  # Don't start new session while idle

        if is_idle:
            return  # Stay paused while idle

        # ── Window sampling ───────────────────────────────────────────────────
        win_info: Optional[WindowInfo] = get_active_window()
        if not win_info:
            return

        # Privacy gate
        if not privacy_manager.should_track(win_info.process_name):
            return

        closed, new_started = self._session_mgr.update(win_info)
        if closed:
            self._persist_session(closed.window, closed.start_time, datetime.now())

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist_session(
        self,
        window: WindowInfo,
        start_time: datetime,
        end_time:   datetime,
        is_idle:    bool = False,
        idle_reason: Optional[str] = None,
    ) -> None:
        if (end_time - start_time).total_seconds() < 1:
            return  # Skip sub-second flickers

        safe_title = privacy_manager.safe_title(window.process_name, window.window_title)

        try:
            record = ActivityRecord(
                process_name    = window.process_name,
                window_title    = safe_title,
                start_time      = start_time,
                end_time        = end_time,
                device_name     = self._device_name,
                device_id       = self._device_id,
                process_id      = window.process_id,
                executable_path = window.executable_path,
                window_handle   = window.hwnd,
                is_idle         = is_idle,
                idle_reason     = idle_reason,
            )
        except ValueError as exc:
            logger.warning(f"Validation rejected session: {exc}")
            return

        data = record.to_dict()
        data["activity_id"] = generate_activity_id(
            self._device_id,
            window.process_name,
            safe_title,
            start_time,
        )

        saved = self._db.insert_activity(data)
        if saved:
            duration = int((end_time - start_time).total_seconds())
            logger.info(
                f"Saved: [{window.process_name}] {safe_title[:60]!r} "
                f"({duration}s)"
            )
            if self.on_session_saved:
                self.on_session_saved({**data, "duration_seconds": duration})

    # ── Device identity ───────────────────────────────────────────────────────

    def _get_or_create_device_id(self) -> str:
        # Try Windows hardware UUID first
        import sys
        if sys.platform == "win32":
            try:
                import subprocess
                result = subprocess.run(
                    ["wmic", "csproduct", "get", "UUID"],
                    capture_output=True, text=True, timeout=5
                )
                lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
                if len(lines) >= 2 and lines[1] != "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
                    return lines[1]
            except Exception:
                pass

        # Fallback: read/generate persistent UUID
        import os
        id_file_path = None
        try:
            from config.settings import DATA_DIR
            id_file_path = DATA_DIR / ".device_id"
            if id_file_path.exists():
                return id_file_path.read_text().strip()
            new_id = str(uuid.uuid4())
            id_file_path.write_text(new_id)
            return new_id
        except Exception:
            return str(uuid.uuid4())
