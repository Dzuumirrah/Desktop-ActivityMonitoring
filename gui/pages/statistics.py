"""
Statistics page: weekly/monthly bar charts, hourly heatmap, per-app breakdown.
"""

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from core.database import get_db
from gui.widgets.charts import (
    BarChartWidget, DailyBarChart, HourlyHeatmap,
    StatCard, _seconds_to_hms,
)


def _heading(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "color: #cdd6f4; font-size: 13px; font-weight: bold; padding: 10px 0 4px 0;"
    )
    return lbl


class StatisticsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._db = get_db()
        self._build_ui()
        self.refresh()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Scrollable content
        content = QWidget()
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(24, 16, 24, 16)
        self._layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: #1e1e2e; }")
        outer.addWidget(scroll)

        L = self._layout

        # Title + range selector
        hdr = QHBoxLayout()
        title = QLabel("Statistics")
        title.setStyleSheet("color: #cdd6f4; font-size: 20px; font-weight: bold;")
        hdr.addWidget(title)
        hdr.addStretch()
        self._range_combo = QComboBox()
        self._range_combo.addItems(["Last 7 days", "Last 30 days", "Last 90 days"])
        self._range_combo.setStyleSheet(
            "QComboBox { background: #313244; color: #cdd6f4; "
            "padding: 4px 8px; border-radius: 6px; border: none; }"
        )
        self._range_combo.currentIndexChanged.connect(self.refresh)
        hdr.addWidget(self._range_combo)
        L.addLayout(hdr)

        # Summary cards
        card_row = QHBoxLayout()
        card_row.setSpacing(12)
        self._card_total    = StatCard("Total Active")
        self._card_avg_day  = StatCard("Avg / Day")
        self._card_top_app  = StatCard("Top App")
        self._card_sessions = StatCard("Sessions")
        for c in (self._card_total, self._card_avg_day,
                  self._card_top_app, self._card_sessions):
            card_row.addWidget(c)
        L.addLayout(card_row)

        # Daily bar chart
        L.addWidget(_heading("Daily Active Time"))
        self._daily_chart = DailyBarChart()
        self._daily_chart.setMinimumHeight(160)
        L.addWidget(self._daily_chart)

        # Hourly heatmap
        L.addWidget(_heading("Activity by Hour of Day"))
        self._heatmap = HourlyHeatmap()
        L.addWidget(self._heatmap)

        # Top apps chart
        L.addWidget(_heading("Top Applications"))
        self._app_chart = BarChartWidget()
        self._app_chart.setMinimumHeight(280)
        L.addWidget(self._app_chart)

        L.addStretch()

    # ── Data ──────────────────────────────────────────────────────────────────

    def _date_range(self) -> tuple[datetime, datetime]:
        idx  = self._range_combo.currentIndex()
        days = [7, 30, 90][idx]
        end  = datetime.now()
        start = end - timedelta(days=days)
        return start, end

    def refresh(self) -> None:
        start, end = self._date_range()

        # Summary
        rows = self._db.get_activities(
            start_date=start, end_date=end, include_idle=False, limit=50_000
        )
        total_sec   = sum(r["duration_seconds"] for r in rows)
        days_range  = max(1, (end - start).days)
        avg_sec     = total_sec // days_range

        top = self._db.get_top_apps(start_date=start, end_date=end, limit=1)
        top_name = top[0]["process_name"] if top else "—"

        self._card_total.set_value(_seconds_to_hms(total_sec))
        self._card_avg_day.set_value(_seconds_to_hms(avg_sec))
        self._card_top_app.set_value(top_name[:18])
        self._card_sessions.set_value(str(len(rows)))

        # Daily chart
        daily = self._db.get_daily_totals(start, end)
        self._daily_chart.set_data(daily)

        # Heatmap
        hourly = self._db.get_hourly_heatmap(start, end)
        self._heatmap.set_data(hourly)

        # App chart
        top10 = self._db.get_top_apps(start_date=start, end_date=end, limit=10)
        self._app_chart.set_data(top10)
