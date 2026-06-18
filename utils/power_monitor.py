"""
Windows power event monitor.

Creates a message-only Win32 window in a dedicated daemon thread that
receives WM_POWERBROADCAST messages.  Works whether the Qt main window
is visible, hidden (tray mode), or on another virtual desktop — because
a message-only HWND (HWND_MESSAGE) always receives broadcast messages
regardless of z-order or visibility.

Bug fixes vs. previous version
--------------------------------
1. HWND_MESSAGE (-3) was passed as a plain Python int which ctypes
   marshals as c_int (4 bytes).  On 64-bit Windows, HWND is 8 bytes,
   so the upper 4 bytes were undefined → ERROR_INVALID_WINDOW_HANDLE
   (error 1400).  Fix: set explicit argtypes on CreateWindowExW so the
   hWndParent slot is typed as wt.HWND and pass wt.HWND(-3).

2. WNDPROC return type was ctypes.c_long (32-bit on Windows / MSVC ABI)
   but LRESULT is LONG_PTR — 8 bytes on 64-bit Windows.  Fix: use
   ctypes.c_ssize_t for the return type.

3. The WNDPROC callback object was stored in a local variable.  If the
   GC collected it between RegisterClassExW and the GetMessage loop, the
   function pointer stored in the WNDCLASSEXW struct would dangle.  Fix:
   store the callback as self._cb so it lives as long as the monitor.

4. RegisterClassExW return value was not checked.  It now logs errors
   (but treats ERROR_CLASS_ALREADY_EXISTS as non-fatal so restarts work).
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
WM_POWERBROADCAST      = 0x0218
WM_QUIT                = 0x0012
PBT_APMSUSPEND         = 0x0004   # system is about to sleep / hibernate
PBT_APMRESUMESUSPEND   = 0x0007   # resumed from user-triggered suspend
PBT_APMRESUMEAUTOMATIC = 0x0012   # resumed automatically (e.g. scheduled task)

ERROR_CLASS_ALREADY_EXISTS = 1410  # RegisterClassEx returns this on re-register


class PowerMonitor:
    """
    Listen for Windows sleep / hibernate / wake events and fire callbacks.

    Usage::

        monitor = PowerMonitor(
            on_sleep=lambda reason: ...,
            on_wake=lambda: ...,
        )
        monitor.start()
        # …
        monitor.stop()

    ``on_sleep`` receives ``"sleep"`` (S3) or ``"hibernate"`` (S4).
    Callbacks are invoked from the background thread — queue GUI updates
    back to the Qt main thread if needed.

    On non-Windows platforms this class is a no-op.
    """

    def __init__(
        self,
        on_sleep: Callable[[str], None],
        on_wake:  Callable[[], None],
    ) -> None:
        self._on_sleep = on_sleep
        self._on_wake  = on_wake
        self._hwnd: Optional[int] = None
        self._cb   = None           # strong ref: keeps WNDPROC thunk alive
        self._thread = threading.Thread(
            target=self._message_loop, name="PowerMonitor", daemon=True
        )

    def start(self) -> None:
        if not _IS_WINDOWS:
            logger.debug("Power monitor: non-Windows — skipped.")
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

        # ── 1. Correct WNDPROC type ───────────────────────────────────────────
        # Return type is LRESULT = LONG_PTR = 8 bytes on 64-bit Windows.
        # c_long is only 4 bytes under the Windows/MSVC ABI → use c_ssize_t.
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,           # LRESULT (pointer-sized)
            wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
        )

        def _wnd_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_POWERBROADCAST:
                if wparam == PBT_APMSUSPEND:
                    logger.info("Power event: SUSPEND (going to sleep/hibernate)")
                    try:
                        self._on_sleep("sleep")
                    except Exception as exc:
                        logger.error(f"on_sleep callback: {exc}", exc_info=True)
                elif wparam == PBT_APMRESUMESUSPEND:
                    logger.info("Power event: RESUME (user-triggered)")
                    try:
                        self._on_wake()
                    except Exception as exc:
                        logger.error(f"on_wake callback: {exc}", exc_info=True)
                elif wparam == PBT_APMRESUMEAUTOMATIC:
                    logger.info("Power event: RESUME AUTOMATIC")
                    try:
                        self._on_wake()
                    except Exception as exc:
                        logger.error(f"on_wake callback: {exc}", exc_info=True)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        # ── 2. Keep strong reference so GC never collects the thunk ──────────
        self._cb = WNDPROC(_wnd_proc)

        # ── 3. WNDCLASSEXW structure ──────────────────────────────────────────
        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize",        wt.UINT),
                ("style",         wt.UINT),
                ("lpfnWndProc",   WNDPROC),
                ("cbClsExtra",    ctypes.c_int),
                ("cbWndExtra",    ctypes.c_int),
                ("hInstance",     wt.HINSTANCE),
                ("hIcon",         wt.HICON),
                ("hCursor",       wt.HANDLE),
                ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName",  wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR),
                ("hIconSm",       wt.HICON),
            ]

        # ── 4. Explicit argtypes / restype for every API call ─────────────────
        kernel32.GetModuleHandleW.restype  = wt.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]

        user32.RegisterClassExW.restype    = wt.ATOM
        user32.RegisterClassExW.argtypes   = [ctypes.POINTER(WNDCLASSEXW)]

        user32.DefWindowProcW.restype      = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes     = [
            wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
        ]

        # hWndParent MUST be wt.HWND so -3 sign-extends to 8 bytes correctly
        user32.CreateWindowExW.restype     = wt.HWND
        user32.CreateWindowExW.argtypes    = [
            wt.DWORD,    # dwExStyle
            wt.LPCWSTR,  # lpClassName
            wt.LPCWSTR,  # lpWindowName
            wt.DWORD,    # dwStyle
            ctypes.c_int, ctypes.c_int,   # X, Y
            ctypes.c_int, ctypes.c_int,   # nWidth, nHeight
            wt.HWND,     # hWndParent  ← typed HWND so -3 is 8 bytes wide
            wt.HMENU,    # hMenu
            wt.HMODULE,  # hInstance
            wt.LPVOID,   # lpParam
        ]

        user32.GetMessageW.restype         = ctypes.c_int
        user32.GetMessageW.argtypes        = [
            ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT,
        ]

        # ── 5. Register window class ──────────────────────────────────────────
        hinstance  = kernel32.GetModuleHandleW(None)
        class_name = "ActivityMonitorPowerWatcher_v3"

        wc                = WNDCLASSEXW()
        wc.cbSize         = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc    = self._cb          # strong ref already stored above
        wc.hInstance      = hinstance
        wc.lpszClassName  = class_name

        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            err = kernel32.GetLastError()
            if err == ERROR_CLASS_ALREADY_EXISTS:
                # Class persists from a previous run in the same process — OK
                logger.debug("Window class already registered; continuing.")
            else:
                logger.error(
                    f"RegisterClassExW failed (error {err}). "
                    "Sleep/wake events will NOT be detected."
                )
                return

        # ── 6. Create message-only window ─────────────────────────────────────
        # HWND_MESSAGE = (HWND)-3.  Pass as wt.HWND(-3) so ctypes
        # sign-extends to 8 bytes on 64-bit Windows (fixes error 1400).
        hwnd = user32.CreateWindowExW(
            0,              # dwExStyle
            class_name,     # lpClassName
            "PowerWatcher", # lpWindowName
            0,              # dwStyle
            0, 0, 0, 0,     # x, y, nWidth, nHeight
            wt.HWND(-3),    # hWndParent = HWND_MESSAGE  ← THE FIX
            None,           # hMenu
            hinstance,      # hInstance
            None,           # lpParam
        )

        if not hwnd:
            err = kernel32.GetLastError()
            logger.error(
                f"CreateWindowExW failed (error {err}). "
                "Sleep/wake events will NOT be detected."
            )
            return

        self._hwnd = hwnd
        logger.info(f"Power monitor window ready (hwnd={hwnd}).")

        # ── 7. Blocking message pump ──────────────────────────────────────────
        msg = wt.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        logger.debug("Power monitor message loop exited.")