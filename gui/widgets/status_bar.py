"""
Persistent status bar: tracker health, sync lag, idle indicator.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


def _dot(color: str) -> str:
    return f'<span style="color:{color}; font-size:14px;">●</span>'


class StatusBar(QWidget):
    """
    Thin bar pinned to the bottom of the main window.
    Updates every 5 seconds via internal timer.
    """

    def __init__(self, db, parent=None) -> None:
        super().__init__(parent)
        self._db = db
        self.setFixedHeight(30)
        self.setObjectName("StatusBar")
        self.setStyleSheet(
            "QWidget#StatusBar { background: #181825; border-top: 1px solid #313244; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(24)

        self._tracker_lbl  = QLabel()
        self._idle_lbl     = QLabel()
        self._sync_lbl     = QLabel()
        self._records_lbl  = QLabel()

        for lbl in (self._tracker_lbl, self._idle_lbl,
                    self._sync_lbl, self._records_lbl):
            lbl.setStyleSheet("color: #a6adc8; font-size: 11px;")
            layout.addWidget(lbl)

        layout.addStretch()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(5_000)
        self.refresh()

    # ── State setters (called from tracker callbacks) ─────────────────────────

    def set_idle(self, is_idle: bool, reason: str | None = None) -> None:
        if is_idle:
            self._idle_lbl.setText(f"{_dot('#f38ba8')} Idle ({reason or '…'})")
        else:
            self._idle_lbl.setText(f"{_dot('#a6e3a1')} Active")

    def set_sync_status(self, status: str, count: int = 0) -> None:
        if status == "synced":
            self._sync_lbl.setText(f"{_dot('#a6e3a1')} Synced {count} rows")
        elif status == "failed":
            self._sync_lbl.setText(f"{_dot('#f38ba8')} Sync failed")
        else:
            self._sync_lbl.setText(f"{_dot('#f9e2af')} Syncing…")

    # ── Periodic refresh ──────────────────────────────────────────────────────

    def refresh(self) -> None:
        try:
            health = self._db.get_health()

            alive = health.get("alive", False)
            self._tracker_lbl.setText(
                f"{_dot('#a6e3a1')} Tracker running"
                if alive else
                f"{_dot('#f38ba8')} Tracker offline"
            )

            pending = health.get("pending_sync", 0)
            if not self._sync_lbl.text():
                self._sync_lbl.setText(
                    f"{_dot('#7f849c')} {pending} pending"
                )

            total = health.get("total_records", 0)
            self._records_lbl.setText(f"📊 {total:,} records")

        except Exception:
            pass
