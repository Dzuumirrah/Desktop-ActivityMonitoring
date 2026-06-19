"""
Activity Tracker: background polling loop.

Responsibilities:
  - Poll active window every N seconds
  - Detect idle / resume transitions
  - Delegate session-boundary decisions to SessionManager
  - Apply privacy rules before persistence
  - Ping health check every 60 s
  - Emit Qt signals so the GUI updates in real-time

Idle handling (revised)
-----------------------
When idle is detected the active session is LEFT OPEN.  Only the idle
gap itself is recorded as a separate is_idle=True row when the user
returns.  This means active sessions are never split by idle timeouts;
the session's duration includes idle gaps but idle time is separately
queryable via the is_idle column.

Sleep / wake still closes and reopens sessions because the machine is
powered down and gaps can be many hours.
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

    Public callbacks (set before .start()):
        on_session_saved(activity_dict)
        on_idle_changed(is_idle, reason)
    """

    HEALTH_PING_INTERVAL = 60   # seconds between health-check DB pings
    _MIN_SESSION_SEC     = 5    # discard sessions shorter than this

    def __init__(self) -> None:
        self._db            = get_db()
        self._session_mgr   = SessionManager()
        self._idle_detector = IdleDetector()

        self._thread: Optional[threading.Thread] = None
        self._stop_event    = threading.Event()
        self._paused        = False
        self._idle_active   = False

        self._last_health_ping    = 0.0
        self._last_settings_check = time.time()

        # Idle gap recording: track start of idle period so we can write
        # the gap as a single is_idle=True row when the user returns.
        self._idle_start:       Optional[datetime] = None
        self._idle_last_reason: Optional[str]      = None
        # Window at the moment idle began (used to associate the idle record)
        self._idle_window:      Optional[WindowInfo] = None

        # Session continuation after sleep / wake only
        self._last_closed_window: Optional[WindowInfo] = None
        self._last_closed_end:    Optional[datetime]   = None
        self._just_resumed:       bool                 = False

        # Sleep / hibernate state
        self._sleep_start:  Optional[datetime] = None
        self._sleep_reason: Optional[str]      = None

        # GUI callbacks
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
        logger.info("Tracker stopping…")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
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

    # ── Sleep / wake (called from PowerMonitor) ───────────────────────────────

    def _on_sleep(self, reason: str = "sleep") -> None:
        """
        Called just before the system suspends.

        Saves the active session so sleep time is not included in its
        duration.  Sleep gaps can be many hours so we still close here
        (unlike short idle timeouts which leave the session open).
        """
        logger.info(f"System going to {reason} — saving active session.")
        closed = self._session_mgr.force_close()
        now = datetime.now()
        if closed:
            self._persist_session(
                closed.window, closed.start_time, now,
                is_idle=False, idle_reason=None,
            )
            self._last_closed_window = closed.window
            self._last_closed_end    = now

        self._sleep_start  = now
        self._sleep_reason = reason
        self._idle_active  = True

    def _on_wake(self) -> None:
        """
        Called just after the system resumes from sleep / hibernate.

        Records the sleep gap as an idle session then resets so tracking
        restarts on the next tick.
        """
        now = datetime.now()
        if self._sleep_start:
            dur = int((now - self._sleep_start).total_seconds())
            logger.info(f"System woke after {dur}s of {self._sleep_reason or 'sleep'}.")
            # Persist the sleep gap as an idle record
            if self._last_closed_window:
                self._persist_session(
                    self._last_closed_window,
                    self._sleep_start,
                    now,
                    is_idle=True,
                    idle_reason=self._sleep_reason or "sleep",
                )

        self._sleep_start  = None
        self._sleep_reason = None
        self._idle_active  = False
        self._idle_detector.reset()
        self._just_resumed = True

        logger.info("Tracker resumed after system wake.")
        if self.on_idle_changed:
            self.on_idle_changed(False, None)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if not self._paused:
                    self._tick()
            except Exception as exc:
                logger.error(f"Tracker tick error: {exc}", exc_info=True)
            poll_sec = settings.get("polling_interval", 1)
            self._stop_event.wait(timeout=poll_sec)

    def _tick(self) -> None:
        now = time.monotonic()

        # ── Live settings reload ──────────────────────────────────────────────
        if now - self._last_settings_check > 10:
            new_idle = settings.get("idle_timeout", 300)
            if new_idle != self._idle_detector._threshold:
                self._idle_detector.update_threshold(new_idle)
            self._last_settings_check = now

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
                # ── Going idle ────────────────────────────────────────────────
                # Do NOT close the active session.  Just record when idle
                # started and which window was active so we can write the
                # gap record when the user returns.
                self._idle_start       = datetime.now()
                self._idle_last_reason = reason
                # Snapshot the current window for the idle record
                current = self._session_mgr.current
                self._idle_window = current.window if current else None
                return

            else:
                # ── Returning from idle ───────────────────────────────────────
                # Persist the idle gap as its own row.
                if self._idle_start is not None:
                    idle_end = datetime.now()
                    # Prefer the window that was active when idle started;
                    # fall back to whatever is current now.
                    win = self._idle_window or (
                        self._session_mgr.current.window
                        if self._session_mgr.current else None
                    )
                    if win:
                        self._persist_session(
                            win,
                            self._idle_start,
                            idle_end,
                            is_idle=True,
                            idle_reason=self._idle_last_reason,
                        )
                self._idle_start       = None
                self._idle_last_reason = None
                self._idle_window      = None
                # Active session was never closed — no resume needed.
                
        # ── Window sampling ───────────────────────────────────────────────────
        win_info: Optional[WindowInfo] = get_active_window()
        if not win_info:
            return

        if not privacy_manager.should_track(win_info.process_name):
            return

        closed, new_started = self._session_mgr.update(win_info)

        # ── Session continuation after sleep / wake ───────────────────────────
        if self._just_resumed:
            self._just_resumed = False
            if (new_started
                    and self._last_closed_window is not None
                    and self._last_closed_end    is not None
                    and win_info.process_name == self._last_closed_window.process_name
                    and win_info.window_title == self._last_closed_window.window_title):
                self._session_mgr.resume_from(self._last_closed_end)
                self._last_closed_window = None
                logger.info(
                    f"Session continued after wake: [{win_info.process_name}] "
                    f"{win_info.window_title[:50]!r}"
                )

        if closed:
            self._persist_session(closed.window, closed.start_time, datetime.now())

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist_session(
        self,
        window:      WindowInfo,
        start_time:  datetime,
        end_time:    datetime,
        is_idle:     bool = False,
        idle_reason: Optional[str] = None,
    ) -> None:
        duration = (end_time - start_time).total_seconds()

        if duration < self._MIN_SESSION_SEC:
            logger.debug(
                f"Skipping short session ({duration:.1f}s < {self._MIN_SESSION_SEC}s): "
                f"[{window.process_name}] {window.window_title[:40]!r}"
            )
            return

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
            tag = "IDLE" if is_idle else "ACTIVE"
            logger.info(
                f"Saved [{tag}]: [{window.process_name}] {safe_title[:60]!r} "
                f"({int(duration)}s)"
            )
            if self.on_session_saved:
                self.on_session_saved({**data, "duration_seconds": int(duration)})

    # ── Device identity ───────────────────────────────────────────────────────

    def _get_or_create_device_id(self) -> str:
        import sys as _sys
        if _sys.platform == "win32":
            try:
                import subprocess
                result = subprocess.run(
                    ["wmic", "csproduct", "get", "UUID"],
                    capture_output=True, text=True, timeout=5,
                )
                lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
                if (len(lines) >= 2
                        and lines[1] != "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF"):
                    return lines[1]
            except Exception:
                pass

        try:
            from config.settings import DATA_DIR
            id_path = DATA_DIR / ".device_id"
            if id_path.exists():
                return id_path.read_text().strip()
            new_id = str(uuid.uuid4())
            id_path.write_text(new_id)
            return new_id
        except Exception:
            return str(uuid.uuid4())