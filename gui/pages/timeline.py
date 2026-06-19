"""
Timeline page: chronological session blocks with date navigation.

Zoom controls
─────────────
  Ctrl + scroll wheel  →  zoom in / out (centred on visible window)
  Shift + scroll wheel →  scroll horizontally (pan left / right)
  Scroll wheel alone   →  scroll the page vertically (default)
  [+] / [−] buttons   →  zoom in / out by a fixed step
  [Reset] button       →  restore full-day view
"""

from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PySide6.QtWidgets import (
    QDateEdit, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core.database import get_db
from gui.widgets.charts import COLORS, _seconds_to_hms, _draw_placeholder


_DAY_SEC = 86_400          # seconds in a day
_MAX_ZOOM = 48.0           # 48× = 30-minute visible window
_ZOOM_STEP = 1.5           # per button click
_SCROLL_ZOOM_STEP = 1.12   # per wheel tick


# ── Timeline drawing widget ───────────────────────────────────────────────────

class TimelineCanvas(QWidget):
    """
    Draws a horizontal Gantt-style timeline.
    Supports smooth zoom (Ctrl+scroll / buttons) and pan (Shift+scroll).
    """

    zoom_changed = Signal()

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

        # Zoom / pan state
        self._zoom: float = 1.0      # 1.0 = full day visible
        self._pan_sec: float = 0.0   # offset from day start in seconds

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # Must grab wheel events; otherwise the QScrollArea eats them all
        self.setFocusPolicy(Qt.WheelFocus)

    # ── Public API ────────────────────────────────────────────────────────────

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

    def zoom_in(self) -> None:
        self._apply_zoom(_ZOOM_STEP)

    def zoom_out(self) -> None:
        self._apply_zoom(1.0 / _ZOOM_STEP)

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._pan_sec = 0.0
        self.update()
        self.zoom_changed.emit()

    @property
    def zoom_label(self) -> str:
        """Human-readable zoom level, e.g. '2× (12 h)'"""
        visible_sec = _DAY_SEC / self._zoom
        if visible_sec >= 3600:
            span = f"{visible_sec / 3600:.0f} h"
        else:
            span = f"{visible_sec / 60:.0f} min"
        return f"{self._zoom:.1f}×  ({span} visible)"

    # ── Zoom / pan helpers ────────────────────────────────────────────────────

    def _apply_zoom(self, factor: float, anchor_sec: float | None = None) -> None:
        """
        Zoom by *factor* (>1 = zoom in), keeping *anchor_sec* fixed on screen.
        If anchor_sec is None the centre of the current view is used.
        """
        old_zoom = self._zoom
        new_zoom = max(1.0, min(_MAX_ZOOM, old_zoom * factor))
        if new_zoom == old_zoom:
            return

        old_visible = _DAY_SEC / old_zoom
        if anchor_sec is None:
            anchor_sec = self._pan_sec + old_visible / 2.0

        new_visible = _DAY_SEC / new_zoom
        # Keep the anchor point at the same proportional position in the view
        anchor_ratio = (anchor_sec - self._pan_sec) / old_visible
        self._zoom = new_zoom
        self._pan_sec = anchor_sec - anchor_ratio * new_visible
        self._clamp_pan()
        self.update()
        self.zoom_changed.emit()

    def _clamp_pan(self) -> None:
        visible_sec = _DAY_SEC / self._zoom
        self._pan_sec = max(0.0, min(self._pan_sec, _DAY_SEC - visible_sec))

    def _x_for_sec(self, t_sec: float, draw_w: int) -> int:
        """Convert a time offset (seconds from day start) to canvas x coordinate."""
        visible_sec = _DAY_SEC / self._zoom
        return self.LABEL_W + int(draw_w * (t_sec - self._pan_sec) / visible_sec)

    # ── Events ────────────────────────────────────────────────────────────────

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta    = event.angleDelta().y()   # positive = scroll up / zoom in
        mods     = event.modifiers()

        if mods & Qt.ControlModifier:
            # ── Ctrl + scroll → zoom ──────────────────────────────────────────
            factor = _SCROLL_ZOOM_STEP if delta > 0 else 1.0 / _SCROLL_ZOOM_STEP
            # Zoom toward the mouse cursor position on the time axis
            draw_w = self.width() - self.LABEL_W - self.PAD * 2
            mouse_x = event.position().x() - self.LABEL_W
            if draw_w > 0:
                visible_sec = _DAY_SEC / self._zoom
                anchor_sec = self._pan_sec + visible_sec * (mouse_x / draw_w)
            else:
                anchor_sec = None
            self._apply_zoom(factor, anchor_sec)
            event.accept()

        elif mods & Qt.ShiftModifier:
            # ── Shift + scroll → horizontal pan ──────────────────────────────
            visible_sec = _DAY_SEC / self._zoom
            # One wheel tick (delta=120) pans ~8 % of the visible window
            pan_delta = -(delta / 120.0) * (visible_sec * 0.08)
            self._pan_sec += pan_delta
            self._clamp_pan()
            self.update()
            event.accept()

        else:
            # ── Plain scroll → let the QScrollArea handle vertical scroll ─────
            event.ignore()

    # ── Painting ──────────────────────────────────────────────────────────────

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
        draw_w = W - self.LABEL_W - self.PAD * 2

        visible_sec = _DAY_SEC / self._zoom
        pan_sec     = self._pan_sec

        # ── Hour / half-hour grid ─────────────────────────────────────────────
        # Choose grid interval based on how zoomed in we are
        if visible_sec <= 1800:          # ≤30 min → every 5 min
            grid_step = 300
            fmt_fn = lambda s: f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}"
        elif visible_sec <= 7200:        # ≤2 h → every 15 min
            grid_step = 900
            fmt_fn = lambda s: f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}"
        elif visible_sec <= 14400:       # ≤4 h → every 30 min
            grid_step = 1800
            fmt_fn = lambda s: f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}"
        else:                            # full day → every 2 hours
            grid_step = 7200
            fmt_fn = lambda s: f"{int(s)//3600:02d}h"

        font_small = QFont()
        font_small.setPointSize(8)
        font_label = QFont()
        font_label.setPointSize(9)

        p.setFont(font_small)
        t = 0
        while t <= _DAY_SEC:
            if pan_sec <= t <= pan_sec + visible_sec:
                x = self._x_for_sec(t, draw_w)
                p.setPen(QColor("#313244"))
                p.drawLine(x, 0, x, self.height())
                p.setPen(QColor("#585b70"))
                p.drawText(x + 2, self.height() - 18, 48, 16,
                           Qt.AlignLeft, fmt_fn(t))
            t += grid_step

        # ── Session rows ──────────────────────────────────────────────────────
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

                # Clip to [pan_sec, pan_sec+visible_sec]
                vis_end = pan_sec + visible_sec
                if et_sec <= pan_sec or st_sec >= vis_end:
                    continue  # fully outside view
                st_sec = max(pan_sec, st_sec)
                et_sec = min(vis_end, et_sec)

                x1 = self._x_for_sec(st_sec, draw_w)
                x2 = self._x_for_sec(et_sec, draw_w)
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

        # ── Date navigation ───────────────────────────────────────────────────
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
        for w in (self._prev_btn, self._date_edit, self._next_btn, self._summary_lbl):
            nav.addWidget(w)
        nav.addStretch()
        root.addLayout(nav)

        # ── Canvas in scroll area ─────────────────────────────────────────────
        self._canvas = TimelineCanvas()
        self._canvas.zoom_changed.connect(self._update_zoom_label)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._canvas)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: #1e1e2e; }"
        )
        root.addWidget(self._scroll)

        # ── Zoom controls ─────────────────────────────────────────────────────
        zoom_group = QVBoxLayout()
        zoom_group.setSpacing(2)
        
        zoom_bar = QHBoxLayout()
        zoom_bar.setSpacing(6)
        zoom_bar.addStretch()


        zoom_lbl = QLabel("Zoom:")
        zoom_lbl.setStyleSheet("color: #7f849c; font-size: 11px;")
        zoom_bar.addWidget(zoom_lbl)

        self._zoom_in_btn = self._zoom_btn("+", "Zoom in  (Ctrl + scroll up)")
        self._zoom_in_btn.setFixedWidth(68)
        self._zoom_in_btn.clicked.connect(self._on_zoom_in)
        zoom_bar.addWidget(self._zoom_in_btn)
        
        self._zoom_out_btn = self._zoom_btn("-", "Zoom out  (Ctrl + scroll down)")
        self._zoom_out_btn.setFixedWidth(68)
        self._zoom_out_btn.clicked.connect(self._on_zoom_out)
        zoom_bar.addWidget(self._zoom_out_btn)
        
        self._zoom_reset_btn = self._zoom_btn("⟳ Reset", "Restore full-day view")
        self._zoom_reset_btn.setFixedWidth(68)
        self._zoom_reset_btn.clicked.connect(self._on_zoom_reset)
        zoom_bar.addWidget(self._zoom_reset_btn)

        self._zoom_info = QLabel("1.0×  (24 h visible)")
        self._zoom_info.setStyleSheet(
            "color: #585b70; font-size: 10px; font-family: monospace;"
        )
        zoom_bar.addWidget(self._zoom_info)
        zoom_group.addLayout(zoom_bar)

        zoom_infos = QHBoxLayout()
        zoom_infos.setSpacing(2)
        zoom_infos.addStretch()
        
        hint = QLabel("  Ctrl+scroll = zoom · Shift+scroll = pan · scroll = vertical")
        hint.setStyleSheet("color: #45475a; font-size: 10px;")
        zoom_infos.addWidget(hint)
        
        zoom_group.addLayout(zoom_infos)
        root.addLayout(zoom_group)


    # ── Zoom button factory ───────────────────────────────────────────────────

    @staticmethod
    def _zoom_btn(text: str, tooltip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setText(text)
        btn.setFixedSize(28, 24)
        btn.setToolTip(tooltip)
        btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 5px;
                font-size: 12px;
            }
            QPushButton:hover   { background: #45475a; }
            QPushButton:pressed { background: #585b70; }
        """)
        return btn

    # ── Zoom actions ──────────────────────────────────────────────────────────

    def _on_zoom_in(self) -> None:
        self._canvas.zoom_in()
        self._update_zoom_label()

    def _on_zoom_out(self) -> None:
        self._canvas.zoom_out()
        self._update_zoom_label()

    def _on_zoom_reset(self) -> None:
        self._canvas.reset_zoom()
        self._update_zoom_label()

    def _update_zoom_label(self) -> None:
        self._zoom_info.setText(self._canvas.zoom_label)

    # ── Date navigation ───────────────────────────────────────────────────────

    def _prev_day(self) -> None:
        self._date_edit.setDate(self._date_edit.date().addDays(-1))

    def _next_day(self) -> None:
        d = self._date_edit.date().addDays(1)
        if d <= QDate.currentDate():
            self._date_edit.setDate(d)

    def _on_date_changed(self, qdate: QDate) -> None:
        self._selected_date = datetime(qdate.year(), qdate.month(), qdate.day()).date()
        self.refresh()

    # ── Data ──────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        d         = self._selected_date
        day_start = datetime(d.year, d.month, d.day)
        day_end   = day_start + timedelta(days=1)

        sessions = self._db.get_activities(
            start_date=day_start, end_date=day_end,
            include_idle=False, limit=5000,
        )
        self._canvas.set_data(sessions, day_start)
        self._update_zoom_label()

        total_sec = sum(s.get("duration_seconds", 0) for s in sessions)
        n_apps    = len({s["process_name"] for s in sessions})
        self._summary_lbl.setText(
            f"{len(sessions)} sessions · {n_apps} apps · {_seconds_to_hms(total_sec)} active"
        )

    def on_session_saved(self, _data: dict) -> None:
        today = datetime.now().date()
        if self._selected_date == today:
            self.refresh()
