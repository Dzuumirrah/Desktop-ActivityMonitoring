"""
Timeline page: chronological session blocks with date navigation.
"""

from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PySide6.QtWidgets import (
    QDateEdit, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core.database import get_db
from gui.widgets.charts import COLORS, _seconds_to_hms, _draw_placeholder


# ── Timeline drawing widget ───────────────────────────────────────────────────

class TimelineCanvas(QWidget):
    """
    Draws a horizontal Gantt-style timeline.
    Each process gets its own row; blocks are coloured by app.
    """

    ROW_H   = 40
    LABEL_W = 140
    PAD     = 12

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, list[dict]]] = []
        self._day_start: datetime = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self._color_map: dict[str, QColor] = {}
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def set_data(self, sessions: list[dict], day_start: datetime) -> None:
        self._day_start = day_start
        order: list[str] = []
        groups: dict[str, list[dict]] = {}
        for s in sorted(sessions, key=lambda x: x.get("start_time", "")):
            pn = s.get("process_name", "unknown")
            if pn not in groups:
                order.append(pn)
                groups[pn] = []
                self._color_map.setdefault(pn, COLORS[len(self._color_map) % len(COLORS)])
            groups[pn].append(s)
        self._rows = [(app, groups[app]) for app in order]
        self.setMinimumHeight(max(200, len(self._rows) * self.ROW_H + 48))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        if not self._rows:
            _draw_placeholder(p, self.width(), self.height(),
                              "Switch between apps to start recording sessions.\n"
                              "They will appear here as a timeline.")
            p.end()
            return

        W = self.width()
        day_sec = 86_400
        draw_w  = W - self.LABEL_W - self.PAD * 2

        font_small = QFont(); font_small.setPointSize(8)
        font_label = QFont(); font_label.setPointSize(9)

        p.setFont(font_small)
        for hr in range(0, 25, 2):
            x = self.LABEL_W + int(draw_w * (hr * 3600) / day_sec)
            p.setPen(QColor("#313244"))
            p.drawLine(x, 0, x, self.height())
            p.setPen(QColor("#585b70"))
            if hr < 24:
                p.drawText(x + 2, self.height() - 18, 32, 16,
                           Qt.AlignLeft, f"{hr:02d}h")

        for row_idx, (app, sessions) in enumerate(self._rows):
            y_top = row_idx * self.ROW_H + 4
            color = self._color_map[app]

            p.setFont(font_label)
            p.setPen(QColor("#cdd6f4"))
            p.drawText(2, y_top, self.LABEL_W - 6, self.ROW_H - 8,
                       Qt.AlignRight | Qt.AlignVCenter, app[:20])

            for s in sessions:
                st = s.get("start_time")
                et = s.get("end_time")
                if not (st and et):
                    continue
                if isinstance(st, str):
                    try: st = datetime.fromisoformat(st)
                    except Exception: continue
                if isinstance(et, str):
                    try: et = datetime.fromisoformat(et)
                    except Exception: continue

                st_sec = (st - self._day_start).total_seconds()
                et_sec = (et - self._day_start).total_seconds()
                st_sec = max(0, min(st_sec, day_sec))
                et_sec = max(0, min(et_sec, day_sec))

                x1 = self.LABEL_W + int(draw_w * st_sec / day_sec)
                x2 = self.LABEL_W + int(draw_w * et_sec / day_sec)
                bw = max(2, x2 - x1)

                p.setBrush(QBrush(color))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(x1, y_top + 4, bw, self.ROW_H - 14, 3, 3)

        p.end()


# ── Timeline page ─────────────────────────────────────────────────────────────

class TimelinePage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._db = get_db()
        self._selected_date = datetime.now().date()
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(8)

        title = QLabel("Timeline")
        title.setStyleSheet("color: #cdd6f4; font-size: 20px; font-weight: bold;")
        root.addWidget(title)

        nav = QHBoxLayout()
        self._prev_btn = QPushButton("◀ Prev")
        self._prev_btn.setFixedWidth(80)
        self._prev_btn.clicked.connect(self._prev_day)
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.dateChanged.connect(self._on_date_changed)
        self._next_btn = QPushButton("Next ▶")
        self._next_btn.setFixedWidth(80)
        self._next_btn.clicked.connect(self._next_day)
        self._summary_lbl = QLabel()
        self._summary_lbl.setStyleSheet("color: #7f849c; font-size: 11px;")
        for w in (self._prev_btn, self._date_edit, self._next_btn,
                  self._summary_lbl):
            nav.addWidget(w)
        nav.addStretch()
        root.addLayout(nav)

        self._canvas = TimelineCanvas()
        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: #1e1e2e; }"
        )
        root.addWidget(scroll)

    def _prev_day(self) -> None:
        self._date_edit.setDate(self._date_edit.date().addDays(-1))

    def _next_day(self) -> None:
        d = self._date_edit.date().addDays(1)
        if d <= QDate.currentDate():
            self._date_edit.setDate(d)

    def _on_date_changed(self, qdate: QDate) -> None:
        self._selected_date = datetime(qdate.year(), qdate.month(), qdate.day()).date()
        self.refresh()

    def refresh(self) -> None:
        d          = self._selected_date
        day_start  = datetime(d.year, d.month, d.day)
        day_end    = day_start + timedelta(days=1)

        sessions   = self._db.get_activities(
            start_date=day_start, end_date=day_end,
            include_idle=False, limit=5000,
        )
        self._canvas.set_data(sessions, day_start)

        total_sec  = sum(s.get("duration_seconds", 0) for s in sessions)
        n_apps     = len({s["process_name"] for s in sessions})
        self._summary_lbl.setText(
            f"{len(sessions)} sessions · {n_apps} apps · {_seconds_to_hms(total_sec)} active"
        )

    def on_session_saved(self, _data: dict) -> None:
        today = datetime.now().date()
        if self._selected_date == today:
            self.refresh()