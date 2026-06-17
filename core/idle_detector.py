"""
Multi-signal idle detection.

Signals checked:
  1. Windows GetLastInputInfo (authoritative on Windows)
  2. pynput listeners (cross-platform fallback)
  3. Screen-lock detection
  4. Fullscreen app detection (suppresses idle during media playback)
"""

import sys
import threading
import time
from typing import Optional

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("idle_detector")

_IS_WINDOWS = sys.platform == "win32"


# ── Win32 helpers ─────────────────────────────────────────────────────────────

def _get_windows_idle_ms() -> int:
    """Milliseconds since last user input via Win32 GetLastInputInfo."""
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
        return int(elapsed)
    except Exception as exc:
        logger.warning(f"GetLastInputInfo failed: {exc}")
        return 0


def _is_screen_locked() -> bool:
    """Return True if the Windows workstation is locked."""
    if not _IS_WINDOWS:
        return False
    try:
        import ctypes
        user32   = ctypes.windll.user32
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
        if err == 5:   # ERROR_ACCESS_DENIED – not necessarily locked
            return False
        return True
    except Exception:
        return False


def _is_fullscreen_app_running() -> bool:
    """
    Return True if the foreground window covers the entire primary screen.

    This is used to suppress idle detection while the user is watching a
    fullscreen video (YouTube, VLC, media player, etc.) without mouse/keyboard
    input.  It avoids the IDLE → ACTIVE → IDLE loop that occurs every 300 s
    during passive media consumption.

    NOTE: A maximised window is NOT considered fullscreen here because a
    maximised window still leaves the taskbar visible (its top edge is at
    Y > 0 on Windows, or width < screen width).  A true fullscreen window
    starts at (0, 0) and covers the entire display.
    """
    if not _IS_WINDOWS:
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        hwnd   = user32.GetForegroundWindow()
        if not hwnd:
            return False

        # Primary monitor dimensions
        screen_w = user32.GetSystemMetrics(0)   # SM_CXSCREEN
        screen_h = user32.GetSystemMetrics(1)   # SM_CYSCREEN

        rect = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False

        win_w = rect.right  - rect.left
        win_h = rect.bottom - rect.top

        # Fullscreen: starts at or before (0,0), covers entire screen
        is_fs = (rect.left <= 0 and rect.top <= 0 and
                 win_w >= screen_w and win_h >= screen_h)

        if is_fs:
            logger.debug(
                f"Fullscreen detected: hwnd={hwnd} "
                f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom}) "
                f"screen=({screen_w}x{screen_h})"
            )
        return is_fs
    except Exception:
        return False


# ── Idle detector ─────────────────────────────────────────────────────────────

class IdleDetector:
    """
    Thread-safe idle detector combining Win32 and pynput signals.

    Priority order in is_idle():
      1. Screen locked          → always idle
      2. Fullscreen app active  → never idle (suppress media-playback loops)
      3. Input threshold        → idle if no input for threshold seconds
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
            return _get_windows_idle_ms() / 1000.0
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

        # Priority 2. Fullscreen app (e.g. YouTube) → suppress idle entirely
        #    Also reset the pynput timestamp so we don't immediately idle
        #    the moment the fullscreen app exits.
        if _is_fullscreen_app_running():
            with self._lock:
                self._last_activity = time.monotonic()
            self._idle_state  = False
            self._idle_reason = None
            return False, None

        # 3. Input timeout
        idle_sec = self.seconds_since_input()
        if idle_sec >= self._threshold:
            self._idle_state  = True
            self._idle_reason = "input_timeout"
            return True, "input_timeout"

        self._idle_state  = False
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