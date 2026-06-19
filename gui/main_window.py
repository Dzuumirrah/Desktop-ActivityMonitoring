"""
Main application window.
Dark-themed, sidebar navigation, stacked page content area.
"""

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMenu, QPushButton, QStackedWidget,
    QSystemTrayIcon, QVBoxLayout, QWidget,
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
QLineEdit:focus, QComboBox:focus { border-color: #4f8ef7; }
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
QPushButton:hover   { background: #45475a; }
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
QMenu {
    background: #313244; color: #cdd6f4;
    border: 1px solid #45475a;
}
QMenu::item:selected { background: #45475a; }
"""


# ── Tray icon ─────────────────────────────────────────────────────────────────
def _make_tray_icon() -> QIcon:
    """Create a simple 32×32 "AM" icon for the system tray."""
    pm = QPixmap(32, 32)
    pm.fill(QColor("#4f8ef7"))
    p = QPainter(pm)
    p.setPen(QColor("#ffffff"))
    f = QFont("Arial", 10, QFont.Bold)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "AM")
    p.end()
    return QIcon(pm)


# ── Sidebar button ────────────────────────────────────────────────────────────
class NavButton(QPushButton):
    def __init__(self, icon_text: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self._icon_text = icon_text
        self._label     = label
        self.setCheckable(True)
        self.setFixedHeight(48)
        self.setText(f"  {icon_text}  {label}")
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


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    _tracker_session_saved = Signal(dict)
    _tracker_idle_changed = Signal(bool, object)
    _sync_status_changed = Signal(object, object)

    def __init__(self, tracker=None, sync_engine=None) -> None:
        super().__init__()
        self._tracker       = tracker
        self._sync_engine   = sync_engine
        self._db            = get_db()
        self._quit_for_real = False
        self._closing       = False   # re-entrancy guard

        self.setWindowTitle("Activity Monitor")
        self.resize(1100, 720)
        self.setMinimumSize(900, 600)

        self._build_ui()
        self._setup_tray()
        self._connect_tracker()
        logger.info("Main window created.")

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root_widget = QWidget()
        self.setCentralWidget(root_widget)
        root_layout = QVBoxLayout(root_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_content())
        root_layout.addLayout(body, stretch=1)

        self._status_bar = StatusBar(self._db, self)
        root_layout.addWidget(self._status_bar)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet("background: #181825; border-right: 1px solid #313244;")

        L = QVBoxLayout(sidebar)
        L.setContentsMargins(0, 0, 0, 0)
        L.setSpacing(0)

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

    # ── System tray ───────────────────────────────────────────────────────────

    def _setup_tray(self) -> None:
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_make_tray_icon())
        self._tray.setToolTip("Activity Monitor")

        menu = QMenu()
        show_action = menu.addAction("Show Window")
        show_action.triggered.connect(self._show_window)
        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self._quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_window()

    def _quit(self) -> None:
        """
        Triggered from tray menu 'Quit'.
        Explicitly hide the tray icon BEFORE calling quit() so it disappears
        immediately, even when called from another virtual desktop.
        """
        if self._closing:
            return
        self._closing       = True
        self._quit_for_real = True

        # Hide tray first — prevents the lingering icon after quit
        try:
            self._tray.hide()
            self._tray.setVisible(False)
        except Exception:
            pass

        QApplication.quit()

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

        self._tracker_session_saved.connect(self._handle_tracker_session_saved)
        self._tracker_idle_changed.connect(self._handle_tracker_idle_changed)
        self._sync_status_changed.connect(self._status_bar.set_sync_status)

        def on_session(data: dict) -> None:
            self._tracker_session_saved.emit(data)

        def on_idle(is_idle: bool, reason) -> None:
            self._tracker_idle_changed.emit(is_idle, reason)

        self._tracker.on_session_saved = on_session
        self._tracker.on_idle_changed  = on_idle

        if self._sync_engine:
            self._sync_engine.set_status_callback(
                lambda status, n: self._sync_status_changed.emit(status, n)
            )

    @Slot(dict)
    def _handle_tracker_session_saved(self, data: dict) -> None:
        self._page_dashboard.on_session_saved(data)
        self._page_timeline.on_session_saved(data)

    @Slot(bool, object)
    def _handle_tracker_idle_changed(self, is_idle: bool, reason) -> None:
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

    # ── Clean shutdown ────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self._quit_for_real:
            # Real quit (from tray menu): stop subsystems
            if not self._closing:
                self._closing = True
            logger.info("Window closing – stopping tracker and sync.")
            if self._tracker:
                try:
                    self._tracker.stop()
                except Exception:
                    pass
            if self._sync_engine:
                try:
                    self._sync_engine.stop()
                except Exception:
                    pass
            try:
                self._tray.hide()
                self._tray.setVisible(False)
            except Exception:
                pass
            event.accept()
        else:
            # X button / Win+Tab / fullscreen overlay: minimize to tray instead
            event.ignore()
            self.hide()
            self._tray.showMessage(
                "Activity Monitor",
                "Still running in the background. Right-click the tray icon to quit.",
                QSystemTrayIcon.Information,
                3000,
            )
