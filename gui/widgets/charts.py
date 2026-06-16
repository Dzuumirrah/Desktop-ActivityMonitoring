"""
Reusable chart widgets: bar chart, timeline bar, hourly heatmap.
Built on pyqtgraph for performance; falls back to a plain label if unavailable.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PySide6.QtWidgets import QWidget, QSizePolicy, QVBoxLayout, QLabel

# ── Color palette (dark-friendly) ─────────────────────────────────────────────
ACCENT   = QColor("#4f8ef7")
ACCENT2  = QColor("#a78bfa")
BG_CARD  = QColor("#1e1e2e")
FG_TEXT  = QColor("#cdd6f4")
IDLE_CLR = QColor("#585b70")
COLORS   = [
    QColor("#4f8ef7"), QColor("#a78bfa"), QColor("#f38ba8"),
    QColor("#a6e3a1"), QColor("#fab387"), QColor("#89dceb"),
    QColor("#f9e2af"), QColor("#cba6f7"), QColor("#89b4fa"),
    QColor("#74c7ec"),
]


def _seconds_to_hms(sec: int) -> str:
    h, m = divmod(sec, 3600)
    m, s = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# ── Bar Chart ─────────────────────────────────────────────────────────────────

class BarChartWidget(QWidget):
    """
    Horizontal bar chart with labels and value annotations.
    Used for "Top Applications" view.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: list[tuple[str, int]] = []   # [(label, value_sec), …]
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, data: list[dict]) -> None:
        """data: list of {process_name, total_seconds}"""
        self._data = [(d["process_name"], int(d["total_seconds"])) for d in data]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        W = self.width()
        H = self.height()
        label_w  = 150
        value_w  = 80
        bar_area = W - label_w - value_w - 24
        n        = len(self._data)
        row_h    = H / n
        bar_h    = max(8, row_h * 0.5)
        max_val  = max(v for _, v in self._data) or 1

        font = QFont()
        font.setPointSize(9)
        p.setFont(font)

        for i, (label, val) in enumerate(self._data):
            y_center = i * row_h + row_h / 2
            bar_w    = int(bar_area * val / max_val)
            color    = COLORS[i % len(COLORS)]

            # Bar
            p.setBrush(QBrush(color))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(
                int(label_w + 8),
                int(y_center - bar_h / 2),
                max(4, bar_w), int(bar_h), 3, 3
            )

            # Label (left)
            p.setPen(FG_TEXT)
            p.drawText(
                4, int(y_center - 8), label_w - 8, 18,
                Qt.AlignRight | Qt.AlignVCenter,
                label[:25],
            )

            # Value (right)
            p.drawText(
                int(label_w + bar_area + 12),
                int(y_center - 8), value_w, 18,
                Qt.AlignLeft | Qt.AlignVCenter,
                _seconds_to_hms(val),
            )
        p.end()


# ── Daily bar chart ───────────────────────────────────────────────────────────

class DailyBarChart(QWidget):
    """Vertical bar chart: one bar per day, showing total active time."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: list[dict] = []
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, data: list[dict]) -> None:
        """data: list of {activity_date, total_seconds}"""
        self._data = data
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        W       = self.width()
        H       = self.height()
        bottom  = H - 24
        n       = len(self._data)
        bar_w   = max(6, W // (n + 1))
        spacing = (W - n * bar_w) // (n + 1)
        max_val = max(d["total_seconds"] for d in self._data) or 1

        font = QFont()
        font.setPointSize(8)
        p.setFont(font)

        for i, row in enumerate(self._data):
            val    = int(row["total_seconds"])
            bh     = int((val / max_val) * (bottom - 12))
            x      = spacing + i * (bar_w + spacing)
            y      = bottom - bh

            p.setBrush(QBrush(ACCENT))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(x, y, bar_w, bh, 3, 3)

            # Date label
            label = str(row["activity_date"])[-5:]   # MM-DD
            p.setPen(FG_TEXT)
            p.drawText(x - 4, bottom + 4, bar_w + 8, 18,
                       Qt.AlignCenter, label)
        p.end()


# ── Hourly Heatmap ────────────────────────────────────────────────────────────

class HourlyHeatmap(QWidget):
    """24-cell horizontal heatmap showing active seconds per hour."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: dict[int, int] = {}
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, rows: list[dict]) -> None:
        """rows: list of {hour, total_seconds}"""
        self._data = {int(r["hour"]): int(r["total_seconds"]) for r in rows}
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        W      = self.width()
        H      = self.height()
        cell_w = W / 24
        max_v  = max(self._data.values(), default=1)

        font = QFont()
        font.setPointSize(7)
        p.setFont(font)

        for hour in range(24):
            val    = self._data.get(hour, 0)
            ratio  = val / max_v
            alpha  = int(40 + ratio * 215)
            color  = QColor(79, 142, 247, alpha)

            x = int(hour * cell_w)
            p.setBrush(QBrush(color))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(x + 1, 2, int(cell_w) - 2, H - 24, 3, 3)

            p.setPen(FG_TEXT)
            p.drawText(x, H - 20, int(cell_w), 18,
                       Qt.AlignCenter, str(hour))
        p.end()


# ── Stat Card ─────────────────────────────────────────────────────────────────

class StatCard(QWidget):
    """Small card showing a single KPI (label + big value)."""

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._value = "—"
        self.setMinimumSize(140, 80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, value: str) -> None:
        self._value = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        W, H = self.width(), self.height()

        # Card background
        p.setBrush(QBrush(BG_CARD))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, W, H, 10, 10)

        # Label
        lf = QFont()
        lf.setPointSize(9)
        p.setFont(lf)
        p.setPen(QColor("#7f849c"))
        p.drawText(0, 8, W, 20, Qt.AlignCenter, self._label)

        # Value
        vf = QFont()
        vf.setPointSize(16)
        vf.setBold(True)
        p.setFont(vf)
        p.setPen(FG_TEXT)
        p.drawText(0, 28, W, 40, Qt.AlignCenter, self._value)
        p.end()
