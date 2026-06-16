"""
Dashboard page: today's KPIs, top-10 apps, recent sessions.
"""

from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.database import get_db
from gui.widgets.charts import BarChartWidget, StatCard, _seconds_to_hms


def _section(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setStyleSheet(
        "color: #cdd6f4; font-size: 13px; font-weight: bold; "
        "padding: 8px 0 4px 0;"
    )
    return lbl


class DashboardPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._db = get_db()
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(15_000)   # refresh every 15 s
        self.refresh()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(12)

        # Page title
        title = QLabel("Dashboard")
        title.setStyleSheet("color: #cdd6f4; font-size: 20px; font-weight: bold;")
        root.addWidget(title)

        sub = QLabel("Today's activity summary")
        sub.setStyleSheet("color: #7f849c; font-size: 11px; margin-bottom: 8px;")
        root.addWidget(sub)

        # KPI cards row
        card_row = QHBoxLayout()
        card_row.setSpacing(12)
        self._card_active  = StatCard("Active Time")
        self._card_idle    = StatCard("Idle Time")
        self._card_apps    = StatCard("Apps Used")
        self._card_sessions= StatCard("Sessions")
        for c in (self._card_active, self._card_idle,
                  self._card_apps, self._card_sessions):
            card_row.addWidget(c)
        root.addLayout(card_row)

        # Top apps bar chart
        root.addWidget(_section("Top Applications Today"))
        self._bar_chart = BarChartWidget()
        self._bar_chart.setMinimumHeight(220)
        root.addWidget(self._bar_chart)

        # Recent sessions table
        root.addWidget(_section("Recent Sessions"))
        self._table = self._make_table()
        root.addWidget(self._table)

        root.addStretch()

    def _make_table(self) -> QTableWidget:
        cols = ["Time", "Duration", "Application", "Window Title"]
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setStretchLastSection(True)
        t.setColumnWidth(0, 140)
        t.setColumnWidth(1, 80)
        t.setColumnWidth(2, 150)
        t.setStyleSheet("""
            QTableWidget {
                background: #1e1e2e; color: #cdd6f4;
                gridline-color: #313244; border: none;
                font-size: 12px;
            }
            QHeaderView::section {
                background: #181825; color: #7f849c;
                padding: 6px; border: none;
                font-size: 11px; font-weight: bold;
            }
            QTableWidget::item:alternate { background: #181825; }
            QTableWidget::item:selected  { background: #313244; }
        """)
        return t

    # ── Data refresh ──────────────────────────────────────────────────────────

    def refresh(self) -> None:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end   = today_start + timedelta(days=1)

        # KPI data
        rows = self._db.get_activities(
            start_date=today_start, end_date=today_end,
            include_idle=True, limit=2000,
        )
        active_sec  = sum(r["duration_seconds"] for r in rows if not r["is_idle"])
        idle_sec    = sum(r["duration_seconds"] for r in rows if r["is_idle"])
        apps_used   = len({r["process_name"] for r in rows if not r["is_idle"]})
        sessions    = len([r for r in rows if not r["is_idle"]])

        self._card_active.set_value(_seconds_to_hms(active_sec))
        self._card_idle.set_value(_seconds_to_hms(idle_sec))
        self._card_apps.set_value(str(apps_used))
        self._card_sessions.set_value(str(sessions))

        # Top apps
        top = self._db.get_top_apps(start_date=today_start, end_date=today_end, limit=10)
        self._bar_chart.set_data(top)

        # Recent sessions (latest 40)
        recent = self._db.get_activities(
            start_date=today_start, end_date=today_end,
            include_idle=False, limit=40,
        )
        self._populate_table(recent)

    def _populate_table(self, rows: list[dict]) -> None:
        self._table.setRowCount(0)
        for r in rows:
            row_idx = self._table.rowCount()
            self._table.insertRow(row_idx)
            st = r.get("start_time")
            if isinstance(st, str):
                try:
                    st = datetime.fromisoformat(st)
                except Exception:
                    pass
            time_str = st.strftime("%H:%M:%S") if hasattr(st, "strftime") else str(st)
            dur_str  = _seconds_to_hms(int(r.get("duration_seconds", 0)))

            for col, val in enumerate([
                time_str,
                dur_str,
                r.get("process_name", ""),
                r.get("window_title", "")[:120],
            ]):
                item = QTableWidgetItem(str(val))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(row_idx, col, item)

    def on_session_saved(self, _data: dict) -> None:
        """Called by tracker; triggers a lightweight refresh."""
        self.refresh()
