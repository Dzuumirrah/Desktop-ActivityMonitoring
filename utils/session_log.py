"""
In-memory log handler for the current application session.

SessionLogHandler captures every LogRecord emitted since app start.
It is installed into every logger created by utils/logger.py so the
ConsoleDialog can replay current-session records without reading the
on-disk .log file (which mixes previous runs).

Subscribers are notified synchronously from the logging thread.
ConsoleDialog bridges to the Qt main thread via a Qt signal.
"""

import logging
import threading
from typing import Callable, List

# Keep at most this many records in memory (≈ a full day of heavy use)
_MAX_RECORDS = 10_000


class SessionLogHandler(logging.Handler):
    """
    Thread-safe logging.Handler that:
      • stores the last _MAX_RECORDS LogRecords in a ring buffer
      • notifies registered callbacks on every new record

    One instance is shared across all loggers (module-level singleton below).
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self._records: List[logging.LogRecord] = []
        self._lock     = threading.Lock()
        self._subs: List[Callable[[logging.LogRecord], None]] = []

    # ── logging.Handler interface ─────────────────────────────────────────────

    def emit(self, record: logging.LogRecord) -> None:
        # Finalise the record (fills in exc_text, stack_info, etc.)
        try:
            self.format(record)
        except Exception:
            pass

        with self._lock:
            self._records.append(record)
            if len(self._records) > _MAX_RECORDS:
                self._records.pop(0)

        # Fire subscribers outside the lock so they can call snapshot()
        for cb in list(self._subs):
            try:
                cb(record)
            except Exception:
                pass

    # ── Subscriber API (used by ConsoleDialog) ────────────────────────────────

    def subscribe(self, cb: Callable[[logging.LogRecord], None]) -> None:
        """Register *cb*; called from the logging thread on each new record."""
        if cb not in self._subs:
            self._subs.append(cb)

    def unsubscribe(self, cb: Callable[[logging.LogRecord], None]) -> None:
        if cb in self._subs:
            self._subs.remove(cb)

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> List[logging.LogRecord]:
        """Return a shallow copy of all records collected this session."""
        with self._lock:
            return list(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


# ── Module-level singleton ────────────────────────────────────────────────────
# Imported by utils/logger.py and gui/widgets/console_dialog.py.
session_handler = SessionLogHandler()