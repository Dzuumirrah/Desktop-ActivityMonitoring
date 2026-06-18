"""
Windows power event monitor.

Creates a message-only Win32 window in a dedicated daemon thread that
receives WM_POWERBROADCAST messages.  Works correctly whether the Qt
main window is visible, hidden (tray mode), or on another virtual
desktop — because a message-only HWND (HWND_MESSAGE) always receives
broadcast messages regardless of z-order or visibility.

No pywin32 dependency: uses ctypes only.

Callbacks are invoked from the background thread.  Any GUI updates
must be queued back to the main thread (e.g. via Qt signals).
"""

import ctypes
import ctypes.wintypes as wt
import sys
import threading
from typing import Callable, Optional

from utils.logger import setup_logger

logger = setup_logger("power_monitor")

_IS_WINDOWS = sys.platform == "win32"

# ── Win32 constants ───────────────────────────────────────────────────────────
if _IS_WINDOWS:
    WM_POWERBROADCAST      = 0x0218
    WM_QUIT                = 0x0012
    PBT_APMSUSPEND         = 0x0004   # system is about to sleep / hibernate
    PBT_APMRESUMESUSPEND   = 0x0007   # resumed from user-triggered suspend
    PBT_APMRESUMEAUTOMATIC = 0x0012   # resumed automatically (e.g. scheduled task)
    HWND_MESSAGE           = -3       # pseudo-parent for message-only windows


class PowerMonitor:
    """
    Listen for Windows sleep / hibernate / wake events and fire callbacks.

    Usage::

        monitor = PowerMonitor(
            on_sleep=lambda reason: ...,   # called just before suspend
            on_wake=lambda: ...,           # called just after resume
        )
        monitor.start()
        # … later …
        monitor.stop()

    ``on_sleep`` receives a string: ``"sleep"`` (S3) or ``"hibernate"`` (S4).
    On non-Windows platforms the class is a no-op.
    """

    def __init__(
        self,
        on_sleep: Callable[[str], None],
        on_wake:  Callable[[], None],
    ) -> None:
        self._on_sleep  = on_sleep
        self._on_wake   = on_wake
        self._hwnd: Optional[int] = None
        self._thread    = threading.Thread(
            target=self._message_loop, name="PowerMonitor", daemon=True
        )

    def start(self) -> None:
        if not _IS_WINDOWS:
            logger.debug("Power monitor: non-Windows platform — skipped.")
            return
        self._thread.start()
        logger.info("Power monitor started.")

    def stop(self) -> None:
        if self._hwnd is not None:
            try:
                ctypes.windll.user32.PostMessageW(self._hwnd, WM_QUIT, 0, 0)
            except Exception:
                pass

    # ── Background message loop ───────────────────────────────────────────────

    def _message_loop(self) -> None:
        user32   = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Window procedure function type
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM
        )

        def _wnd_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_POWERBROADCAST:
                if wparam == PBT_APMSUSPEND:
                    logger.info("Power event: SUSPEND (sleep/hibernate)")
                    try:
                        self._on_sleep("sleep")
                    except Exception as exc:
                        logger.error(f"on_sleep callback raised: {exc}", exc_info=True)

                elif wparam == PBT_APMRESUMESUSPEND:
                    logger.info("Power event: RESUME (user-triggered)")
                    try:
                        self._on_wake()
                    except Exception as exc:
                        logger.error(f"on_wake callback raised: {exc}", exc_info=True)

                elif wparam == PBT_APMRESUMEAUTOMATIC:
                    logger.info("Power event: RESUME AUTOMATIC")
                    try:
                        self._on_wake()
                    except Exception as exc:
                        logger.error(f"on_wake callback raised: {exc}", exc_info=True)

            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        _wnd_proc_cb = WNDPROC(_wnd_proc)

        # ── Register a minimal window class ───────────────────────────────────
        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize",        wt.UINT),
                ("style",         wt.UINT),
                ("lpfnWndProc",   WNDPROC),
                ("cbClsExtra",    ctypes.c_int),
                ("cbWndExtra",    ctypes.c_int),
                ("hInstance",     wt.HANDLE),
                ("hIcon",         wt.HANDLE),
                ("hCursor",       wt.HANDLE),
                ("hbrBackground", wt.HANDLE),
                ("lpszMenuName",  wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR),
                ("hIconSm",       wt.HANDLE),
            ]

        class_name = "ActivityMonitorPowerWatcher_v2"
        wc = WNDCLASSEXW()
        wc.cbSize        = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc   = _wnd_proc_cb
        wc.hInstance     = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = class_name

        # RegisterClassExW returns 0 on failure, but class may already exist
        # on a second run — that is fine, we just proceed to CreateWindow.
        user32.RegisterClassExW(ctypes.byref(wc))

        # ── Create a message-only window ──────────────────────────────────────
        hwnd = user32.CreateWindowExW(
            0,             # dwExStyle
            class_name,    # lpClassName
            "PowerWatcher",
            0,             # dwStyle
            0, 0, 0, 0,    # x, y, nWidth, nHeight
            HWND_MESSAGE,  # hWndParent — message-only
            None,          # hMenu
            wc.hInstance,  # hInstance
            None,          # lpParam
        )

        if not hwnd:
            err = kernel32.GetLastError()
            logger.error(
                f"CreateWindowExW failed (error {err}). "
                "Sleep/wake events will NOT be detected."
            )
            return

        self._hwnd = hwnd
        logger.debug(f"Power monitor window hwnd={hwnd}")

        # ── Blocking message pump ─────────────────────────────────────────────
        msg = wt.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        logger.debug("Power monitor message loop exited.")