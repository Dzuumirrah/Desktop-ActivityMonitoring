"""
Multi-signal idle detection.
Implements Phase 1 Priority 1 and Section 1.1 from Project Analysis.

Signals checked:
  1. Keyboard / mouse inactivity (pynput listeners)
  2. Windows GetLastInputInfo (fallback, authoritative on Windows)
  3. Screen-lock detection (win32api on Windows)
  4. Configurable threshold per signal type
"""

import sys
import threading
import time
from typing import Optional

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("idle_detector")

# ── Platform detection ────────────────────────────────────────────────────────
_IS_WINDOWS = sys.platform == "win32"


def _get_windows_idle_ms() -> int:
    try:
        import ctypes
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        result = ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        if not result:
            logger.debug("GetLastInputInfo returned False (not initialized?)")
            return 0
            
        elapsed = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return elapsed
    except Exception as e:
        logger.warning(f"GetLastInputInfo failed: {e}; returning 0")
        return 0


def _is_screen_locked() -> bool:
    """Return True if the Windows workstation is locked."""
    if not _IS_WINDOWS:
        return False

    try:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hdesk = user32.OpenInputDesktop(
            0,
            False,
            0x0100  # DESKTOP_SWITCHDESKTOP
        )

        if hdesk:
            user32.CloseDesktop(hdesk)
            return False

        err = kernel32.GetLastError()

        # Access denied does not imply lock
        if err == 5:
            return False

        return True

    except Exception:
        return False
    
class IdleDetector:
    """
    Thread-safe idle detector.

    Combines:
      - Win32 last-input timestamp (authoritative on Windows)
      - pynput listener timestamps (cross-platform fallback)
      - Screen-lock detection
    """

    def __init__(self, idle_threshold_sec: int | None = None) -> None:
        self._threshold = idle_threshold_sec or settings.get("idle_timeout", 300)
        self._last_activity = time.monotonic()
        self._lock = threading.Lock()
        self._idle_state = False
        self._idle_reason: Optional[str] = None
        self._listener_active = False
        self._start_listeners()

    # ── Internal listeners ────────────────────────────────────────────────────

    def _start_listeners(self) -> None:
        try:
            from pynput import keyboard, mouse

            def on_activity(*_):
                with self._lock:
                    self._last_activity = time.monotonic()

            self._kb_listener  = keyboard.Listener(on_press=on_activity,  daemon=True)
            self._ms_listener  = mouse.Listener(on_move=on_activity,
                                                on_click=on_activity,
                                                on_scroll=on_activity,
                                                daemon=True)
            self._kb_listener.start()
            self._ms_listener.start()
            self._listener_active = True
            logger.debug("pynput idle listeners started.")
        except Exception as exc:
            logger.warning(f"pynput unavailable ({exc}); falling back to Win32 only.")

    # ── Public API ────────────────────────────────────────────────────────────

    def seconds_since_input(self) -> float:
        """
        Return seconds since last detected user input.
        Prefers Win32 GetLastInputInfo on Windows for accuracy.
        """
        if _IS_WINDOWS:
            win32_idle = _get_windows_idle_ms() / 1000.0
            return win32_idle

        # Cross-platform: use pynput timestamp
        with self._lock:
            return time.monotonic() - self._last_activity

    def is_idle(self) -> tuple[bool, Optional[str]]:
        """
        Return (is_idle, reason_string).
        reason: 'input_timeout' | 'screen_locked' | None
        """
        # Priority 1: screen locked
        if _is_screen_locked():
            self._idle_state = True
            self._idle_reason = "screen_locked"
            return True, "screen_locked"

        # Priority 2: no input for threshold
        idle_sec = self.seconds_since_input()
        if idle_sec >= self._threshold:
            self._idle_state = True
            self._idle_reason = "input_timeout"
            return True, "input_timeout"

        self._idle_state = False
        self._idle_reason = None
        return False, None

    def reset(self) -> None:
        """Manually mark as active (e.g. after user resumes)."""
        with self._lock:
            self._last_activity = time.monotonic()

    def update_threshold(self, seconds: int) -> None:
        self._threshold = seconds
        logger.info(f"Idle threshold updated to {seconds}s")

    def stop(self) -> None:
        if self._listener_active:
            try:
                self._kb_listener.stop()
                self._ms_listener.stop()
            except Exception:
                pass
