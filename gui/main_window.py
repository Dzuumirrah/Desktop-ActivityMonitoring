"""
Main application window.
Dark-themed, sidebar navigation, stacked page content area.
"""

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QFont
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from core.database import get_db
from gui.pages.dashboard  import DashboardPage
from gui.pages.timeline   import TimelinePage
from gui.pages.statistics import StatisticsPage
from gui.pages.settings   import SettingsPage
from gui.widgets.status_bar import StatusBar
from utils.logger import gui_log as logger

# ── Global dark stylesheet ────────────────────────────────────────────────────
APP_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "Segoe UI", "Inter", "Arial", sans-serif;
}
QScrollBar:vertical {
    background: #181825; width: 8px; border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #45475a; border-radius: 4px; min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: #181825; height: 8px; border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: #45475a; border-radius: 4px;
}
QLineEdit, QComboBox, QSpinBox, QDateEdit {
    background: #313244; color: #cdd6f4;
    border: 1px solid #45475a; border-radius: 6px;
    padding: 4px 8px; font-size: 12px;
}
QLineEdit:focus, QComboBox:focus {
    border-color: #4f8ef7;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: #313244; color: #cdd6f4;
    selection-background-color: #45475a;
    border: 1px solid #45475a;
}
QPushButton {
    background: #313244; color: #cdd6f4;
    border: 1px solid #45475a; border-radius: 6px;
    padding: 5px 14px; font-size: 12px;
}
QPushButton:hover  { background: #45475a; }
QPushButton:pressed { background: #585b70; }
QCheckBox { color: #cdd6f4; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #45475a; border-radius: 4px;
    background: #313244;
}
QCheckBox::indicator:checked { background: #4f8ef7; border-color: #4f8ef7; }
QGroupBox {
    border: 1px solid #313244; border-radius: 8px;
    margin-top: 10px; padding-top: 14px; color: #cdd6f4;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 12px; padding: 0 4px;
}
QCalendarWidget { background: #313244; color: #cdd6f4; }
"""


# ── Sidebar button ────────────────────────────────────────────────────────────

class NavButton(QPushButton):
    def __init__(self, icon_text: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self._icon_text = icon_text
        self._label     = label
        self.setCheckable(True)
        self.setFixedHeight(48)
        self._update_style(False)

    def _update_style(self, active: bool) -> None:
        bg  = "#313244" if active else "transparent"
        clr = "#cdd6f4" if active else "#7f849c"
        bdr = "border-left: 3px solid #4f8ef7;" if active else "border-left: 3px solid transparent;"
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; color: {clr};
                border: none; {bdr}
                border-radius: 0; text-align: left;
                padding: 0 16px; font-size: 13px;
            }}
            QPushButton:hover {{ background: #313244; color: #cdd6f4; }}
        """)

    def setChecked(self, checked: bool) -> None:
        super().setChecked(checked)
        self._update_style(checked)

    def text(self) -> str:
        return f"  {self._icon_text}  {self._label}"


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, tracker=None, sync_engine=None) -> None:
        super().__init__()
        self._tracker     = tracker
        self._sync_engine = sync_engine
        self._db          = get_db()

        self.setWindowTitle("Activity Monitor")
        self.resize(1100, 720)
        self.setMinimumSize(900, 600)

        self._build_ui()
        self._connect_tracker()
        logger.info("Main window created.")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root_widget = QWidget()
        self.setCentralWidget(root_widget)
        root_layout = QVBoxLayout(root_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Body row: sidebar + content
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_content())
        root_layout.addLayout(body, stretch=1)

        # Status bar at bottom
        self._status_bar = StatusBar(self._db, self)
        root_layout.addWidget(self._status_bar)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet("background: #181825; border-right: 1px solid #313244;")

        L = QVBoxLayout(sidebar)
        L.setContentsMargins(0, 0, 0, 0)
        L.setSpacing(0)

        # App logo
        logo = QLabel("  📊 Activity\n      Monitor")
        logo.setStyleSheet(
            "color: #4f8ef7; font-size: 14px; font-weight: bold; "
            "padding: 20px 16px 16px 16px;"
        )
        L.addWidget(logo)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background: #313244; border: none; max-height: 1px;")
        L.addWidget(sep)

        # Nav buttons
        self._nav_buttons: list[NavButton] = []
        nav_items = [
            ("🏠", "Dashboard"),
            ("📅", "Timeline"),
            ("📈", "Statistics"),
            ("⚙️", "Settings"),
        ]
        for icon, label in nav_items:
            btn = NavButton(icon, label)
            btn.clicked.connect(lambda _, l=label: self._navigate(l))
            L.addWidget(btn)
            self._nav_buttons.append(btn)

        L.addStretch()

        # Tracker status indicator
        self._tracker_dot = QLabel("  🟢 Tracker running")
        self._tracker_dot.setStyleSheet(
            "color: #a6adc8; font-size: 10px; padding: 8px 12px;"
        )
        L.addWidget(self._tracker_dot)
        return sidebar

    def _build_content(self) -> QWidget:
        wrapper = QWidget()
        L = QVBoxLayout(wrapper)
        L.setContentsMargins(0, 0, 0, 0)
        L.setSpacing(0)

        self._stack = QStackedWidget()

        self._page_dashboard  = DashboardPage()
        self._page_timeline   = TimelinePage()
        self._page_statistics = StatisticsPage()
        self._page_settings   = SettingsPage(sync_engine=self._sync_engine)

        for page in (self._page_dashboard, self._page_timeline,
                     self._page_statistics, self._page_settings):
            self._stack.addWidget(page)

        L.addWidget(self._stack)
        self._navigate("Dashboard")
        return wrapper

    # ── Navigation ────────────────────────────────────────────────────────────

    _PAGE_MAP = {
        "Dashboard": 0,
        "Timeline":  1,
        "Statistics":2,
        "Settings":  3,
    }

    def _navigate(self, page_name: str) -> None:
        idx = self._PAGE_MAP.get(page_name, 0)
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == idx)

    # ── Tracker callbacks ─────────────────────────────────────────────────────

    def _connect_tracker(self) -> None:
        if not self._tracker:
            self._tracker_dot.setText("  🔴 Tracker offline")
            return

        def on_session(data: dict) -> None:
            self._page_dashboard.on_session_saved(data)
            self._page_timeline.on_session_saved(data)

        def on_idle(is_idle: bool, reason) -> None:
            self._status_bar.set_idle(is_idle, reason)
            if is_idle:
                self._tracker_dot.setText("  🟡 Idle")
                self._tracker_dot.setStyleSheet(
                    "color: #f9e2af; font-size: 10px; padding: 8px 12px;"
                )
            else:
                self._tracker_dot.setText("  🟢 Tracker running")
                self._tracker_dot.setStyleSheet(
                    "color: #a6e3a1; font-size: 10px; padding: 8px 12px;"
                )

        self._tracker.on_session_saved = on_session
        self._tracker.on_idle_changed  = on_idle

        if self._sync_engine:
            self._sync_engine.set_status_callback(
                lambda status, n: self._status_bar.set_sync_status(status, n)
            )

    # ── Clean shutdown ────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        logger.info("Window closing – stopping tracker and sync.")
        if self._tracker:
            self._tracker.stop()
        if self._sync_engine:
            self._sync_engine.stop()
        event.accept()
