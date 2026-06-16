"""
Session boundary detection.
Implements Phase 1 Priority 2 and Section 1.2 from Project Analysis.

A session closes when ANY of the following changes:
  - window handle (HWND) differs
  - process restarted (different creation_time)
  - window title changes within the same process (new task)
"""

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("session_manager")

_IS_WINDOWS = sys.platform == "win32"


# ── Window info snapshot ──────────────────────────────────────────────────────

@dataclass
class WindowInfo:
    hwnd:            int
    window_title:    str
    process_name:    str
    process_id:      int
    executable_path: str
    create_time:     float   # process creation timestamp
    captured_at:     datetime = field(default_factory=datetime.now)

    def key(self) -> tuple:
        """Equality key used for session-change detection."""
        return (self.hwnd, self.process_name.lower(), self.window_title)


# ── Platform implementations ──────────────────────────────────────────────────

def _get_active_window_windows() -> Optional[WindowInfo]:
    try:
        import ctypes
        import ctypes.wintypes as wt
        import psutil

        user32   = ctypes.windll.user32
        hwnd     = user32.GetForegroundWindow()
        if not hwnd:
            return None

        # Window title
        length   = user32.GetWindowTextLengthW(hwnd) + 1
        buf      = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        title    = buf.value.strip()

        # Process ID
        pid      = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_val  = pid.value

        try:
            proc = psutil.Process(pid_val)
            return WindowInfo(
                hwnd            = hwnd,
                window_title    = title,
                process_name    = proc.name(),
                process_id      = pid_val,
                executable_path = proc.exe(),
                create_time     = proc.create_time(),
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return WindowInfo(
                hwnd            = hwnd,
                window_title    = title,
                process_name    = "unknown.exe",
                process_id      = pid_val,
                executable_path = "",
                create_time     = 0.0,
            )
    except Exception as exc:
        logger.debug(f"_get_active_window_windows error: {exc}")
        return None


def _get_active_window_linux() -> Optional[WindowInfo]:
    """Fallback for Linux / CI environments using xdotool."""
    try:
        import subprocess, psutil
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=2
        )
        title = result.stdout.strip() or "Unknown"

        pid_result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowpid"],
            capture_output=True, text=True, timeout=2
        )
        pid = int(pid_result.stdout.strip() or "0")
        proc_name = "unknown"
        exe_path  = ""
        create_t  = 0.0
        if pid:
            try:
                p = psutil.Process(pid)
                proc_name = p.name()
                exe_path  = p.exe()
                create_t  = p.create_time()
            except Exception:
                pass

        return WindowInfo(
            hwnd=0, window_title=title, process_name=proc_name,
            process_id=pid, executable_path=exe_path, create_time=create_t,
        )
    except Exception as exc:
        logger.debug(f"_get_active_window_linux error: {exc}")
        # Return a stub so the tracker doesn't crash on CI
        return WindowInfo(
            hwnd=0, window_title="Unknown", process_name="unknown.exe",
            process_id=0, executable_path="", create_time=0.0,
        )


def get_active_window() -> Optional[WindowInfo]:
    if _IS_WINDOWS:
        return _get_active_window_windows()
    return _get_active_window_linux()


# ── Session manager ───────────────────────────────────────────────────────────

@dataclass
class ActiveSession:
    window:     WindowInfo
    start_time: datetime = field(default_factory=datetime.now)


class SessionManager:
    """
    Tracks the current focus session and decides when to close it.

    Rules (in priority order):
      1. Different HWND → new session
      2. Same process, title changed → new session
      3. Process restarted (create_time differs by >1 s) → new session
      4. Rapid switch (<2 s) and same process → treat as continuation
    """

    # If window changes back within this many seconds and same process,
    # treat it as an interruption rather than a new session.
    CONTINUITY_THRESHOLD_SEC = 2.0

    def __init__(self) -> None:
        self._current: Optional[ActiveSession] = None
        self._last_switch_time: float = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def current(self) -> Optional[ActiveSession]:
        return self._current

    def update(self, new_info: WindowInfo) -> tuple[Optional[ActiveSession], bool]:
        """
        Process a new window snapshot.

        Returns:
          (closed_session, new_session_started)
          closed_session is None if nothing was closed.
        """
        closed: Optional[ActiveSession] = None

        if self._current is None:
            # First ever window
            self._current = ActiveSession(window=new_info)
            return None, True

        if self._should_close(new_info):
            closed           = self._current
            self._last_switch_time = time.monotonic()
            self._current    = ActiveSession(window=new_info)
            logger.debug(
                f"Session closed: [{closed.window.process_name}] "
                f"{closed.window.window_title[:60]!r}"
            )
            return closed, True

        return None, False

    def force_close(self) -> Optional[ActiveSession]:
        """Close the current session unconditionally (e.g. on idle or shutdown)."""
        if self._current:
            closed        = self._current
            self._current = None
            return closed
        return None

    # ── Decision logic ────────────────────────────────────────────────────────

    def _should_close(self, new: WindowInfo) -> bool:
        old = self._current.window  # type: ignore[union-attr]

        # 1. Different window handle (most reliable)
        if old.hwnd != 0 and new.hwnd != 0 and old.hwnd != new.hwnd:
            return True

        # 2. Same process, title changed
        if (old.process_id == new.process_id
                and old.window_title != new.window_title):
            return True

        # 3. Process restarted
        if (old.process_name.lower() == new.process_name.lower()
                and old.create_time != 0.0
                and new.create_time != 0.0
                and abs(old.create_time - new.create_time) > 1.0):
            return True

        return False
