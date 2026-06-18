"""
Settings page: tracker config, privacy controls, sync setup, data management.
"""

import subprocess
import threading
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from config.settings import settings, VALID_POLLING_INTERVALS, VALID_IDLE_TIMEOUTS
from core.database import get_db
from privacy.manager import privacy_manager
from utils.backup import create_backup, list_backups, restore_backup
from utils.logger import setup_logger, LOG_DIR

logger = setup_logger("gui.settings")


def _group(title: str) -> QGroupBox:
    g = QGroupBox(title)
    g.setStyleSheet("""
        QGroupBox {
            color: #cdd6f4; font-size: 12px; font-weight: bold;
            border: 1px solid #313244; border-radius: 8px;
            margin-top: 10px; padding-top: 14px;
        }
        QGroupBox::title {
            subcontrol-origin: margin; subcontrol-position: top left;
            left: 12px; padding: 0 4px;
        }
    """)
    return g


def _label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #a6adc8; font-size: 11px;")
    return lbl


class _Signals(QObject):
    settings_changed = Signal()


class SettingsPage(QWidget):
    def __init__(self, sync_engine=None, parent=None) -> None:
        super().__init__(parent)
        self._db          = get_db()
        self._sync_engine = sync_engine
        self._signals     = _Signals()
        self.settings_changed = self._signals.settings_changed

        # Console process tracking
        self._console_proc: subprocess.Popen | None = None
        self._console_lock = threading.Lock()
        self._process_check_timer = QTimer(self)
        self._process_check_timer.timeout.connect(self._check_console_process)
        self._temp_batch_file: Path | None = None

        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        L = QVBoxLayout(content)
        L.setContentsMargins(24, 16, 24, 24)
        L.setSpacing(16)

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: #1e1e2e; }")
        outer.addWidget(scroll)

        title = QLabel("Settings")
        title.setStyleSheet("color: #cdd6f4; font-size: 20px; font-weight: bold;")
        L.addWidget(title)

        L.addWidget(self._build_tracker_group())
        L.addWidget(self._build_privacy_group())
        L.addWidget(self._build_sync_group())
        L.addWidget(self._build_data_group())
        L.addWidget(self._build_health_group())
        L.addStretch()

    # ── Tracker group ─────────────────────────────────────────────────────────

    def _build_tracker_group(self) -> QGroupBox:
        g = _group("Tracker")
        form = QFormLayout(g)
        form.setSpacing(10)

        # Polling interval
        self._polling_combo = QComboBox()
        for v in VALID_POLLING_INTERVALS:
            self._polling_combo.addItem(f"{v} second{'s' if v>1 else ''}", v)
        self._polling_combo.setCurrentIndex(
            VALID_POLLING_INTERVALS.index(settings.get("polling_interval", 1))
        )
        form.addRow(_label("Polling interval"), self._polling_combo)

        # Idle timeout
        self._idle_combo = QComboBox()
        for v in VALID_IDLE_TIMEOUTS:
            self._idle_combo.addItem(f"{v//60} min" if v >= 60 else f"{v}s", v)
        cur_idle = settings.get("idle_timeout", 300)
        idx = VALID_IDLE_TIMEOUTS.index(cur_idle) if cur_idle in VALID_IDLE_TIMEOUTS else 2
        self._idle_combo.setCurrentIndex(idx)
        form.addRow(_label("Idle timeout"), self._idle_combo)

        # Device name
        self._device_name_edit = QLineEdit(settings.get("device_name", ""))
        self._device_name_edit.setPlaceholderText("Leave blank to use hostname")
        form.addRow(_label("Device name"), self._device_name_edit)

        # Save button
        save_btn = QPushButton("Save Tracker Settings")
        save_btn.clicked.connect(self._save_tracker)
        form.addRow("", save_btn)
        self._style_button(save_btn)
        return g

    # ── Privacy group ─────────────────────────────────────────────────────────

    def _build_privacy_group(self) -> QGroupBox:
        g = _group("Privacy")
        L = QVBoxLayout(g)
        L.setSpacing(10)

        # Mode selector
        mode_row = QHBoxLayout()
        mode_row.addWidget(_label("Privacy mode:"))
        self._privacy_combo = QComboBox()
        for mode, desc in privacy_manager.MODES.items():
            self._privacy_combo.addItem(f"{mode} – {desc}", mode)
        cur = privacy_manager.mode
        for i in range(self._privacy_combo.count()):
            if self._privacy_combo.itemData(i) == cur:
                self._privacy_combo.setCurrentIndex(i)
                break
        self._privacy_combo.currentIndexChanged.connect(self._on_privacy_mode_changed)
        mode_row.addWidget(self._privacy_combo)
        L.addLayout(mode_row)

        # Excluded apps list
        L.addWidget(_label("Excluded apps (window title fully redacted):"))
        self._excluded_list = QListWidget()
        self._excluded_list.setMaximumHeight(100)
        self._excluded_list.setStyleSheet(
            "QListWidget { background: #181825; color: #cdd6f4; "
            "border: 1px solid #313244; border-radius: 6px; }"
        )
        for app in sorted(privacy_manager.excluded_apps):
            self._excluded_list.addItem(app)
        L.addWidget(self._excluded_list)

        row = QHBoxLayout()
        self._new_app_edit = QLineEdit()
        self._new_app_edit.setPlaceholderText("e.g. outlook.exe")
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_excluded_app)
        rem_btn = QPushButton("Remove")
        rem_btn.clicked.connect(self._remove_excluded_app)
        for w in (self._new_app_edit, add_btn, rem_btn):
            row.addWidget(w)
        self._style_button(add_btn)
        self._style_button(rem_btn, danger=True)
        L.addLayout(row)
        return g

    # ── Sync group ────────────────────────────────────────────────────────────

    def _build_sync_group(self) -> QGroupBox:
        g = _group("Cloud Sync (Google Sheets)")
        form = QFormLayout(g)
        form.setSpacing(10)

        self._spreadsheet_edit = QLineEdit(settings.get("spreadsheet_id", ""))
        self._spreadsheet_edit.setPlaceholderText("Google Spreadsheet ID")
        form.addRow(_label("Spreadsheet ID"), self._spreadsheet_edit)

        self._sheet_name_edit = QLineEdit(settings.get("sheet_name", "ActivityLog"))
        form.addRow(_label("Sheet name"), self._sheet_name_edit)

        self._sync_interval_combo = QComboBox()
        for v in (5, 15, 30, 60):
            self._sync_interval_combo.addItem(f"Every {v} min", v)
        cur_si = settings.get("sync_interval", 15)
        for i in range(self._sync_interval_combo.count()):
            if self._sync_interval_combo.itemData(i) == cur_si:
                self._sync_interval_combo.setCurrentIndex(i)
                break
        form.addRow(_label("Sync interval"), self._sync_interval_combo)

        btn_row = QHBoxLayout()
        auth_btn = QPushButton("Authenticate Google")
        auth_btn.clicked.connect(self._authenticate_google)
        sync_now_btn = QPushButton("Sync Now")
        sync_now_btn.clicked.connect(self._sync_now)
        save_sync_btn = QPushButton("Save Sync Settings")
        save_sync_btn.clicked.connect(self._save_sync)
        for b in (auth_btn, sync_now_btn, save_sync_btn):
            self._style_button(b)
            btn_row.addWidget(b)
        form.addRow("", btn_row)

        self._sync_status_lbl = QLabel("Not authenticated")
        self._sync_status_lbl.setStyleSheet("color: #f38ba8; font-size: 11px;")
        form.addRow("", self._sync_status_lbl)
        return g

    # ── Data management group ─────────────────────────────────────────────────

    def _build_data_group(self) -> QGroupBox:
        g = _group("Data Management")
        L = QVBoxLayout(g)
        L.setSpacing(10)

        # Retention
        ret_row = QHBoxLayout()
        ret_row.addWidget(_label("Keep active records for:"))
        self._retention_combo = QComboBox()
        for d in (30, 60, 90, 180, 365):
            self._retention_combo.addItem(f"{d} days", d)
        cur_ret = settings.get("retention_days", 90)
        for i in range(self._retention_combo.count()):
            if self._retention_combo.itemData(i) == cur_ret:
                self._retention_combo.setCurrentIndex(i)
                break
        ret_row.addWidget(self._retention_combo)
        L.addLayout(ret_row)

        # Auto backup toggle
        self._auto_backup_cb = QCheckBox("Auto-backup database daily")
        self._auto_backup_cb.setChecked(settings.get("auto_backup", True))
        self._auto_backup_cb.setStyleSheet("color: #cdd6f4;")
        L.addWidget(self._auto_backup_cb)

        # Buttons
        btn_row = QHBoxLayout()
        backup_now_btn = QPushButton("Backup Now")
        backup_now_btn.clicked.connect(self._backup_now)
        archive_btn = QPushButton("Archive Old Data")
        archive_btn.clicked.connect(self._archive_data)
        restore_btn = QPushButton("Restore Backup…")
        restore_btn.clicked.connect(self._restore_backup)
        save_data_btn = QPushButton("Save Data Settings")
        save_data_btn.clicked.connect(self._save_data_settings)
        for b in (backup_now_btn, archive_btn, restore_btn, save_data_btn):
            self._style_button(b)
            btn_row.addWidget(b)
        L.addLayout(btn_row)

        self._data_status_lbl = QLabel("")
        self._data_status_lbl.setStyleSheet("color: #a6e3a1; font-size: 11px;")
        L.addWidget(self._data_status_lbl)
        return g

    # ── Health group ──────────────────────────────────────────────────────────

    def _build_health_group(self) -> QGroupBox:
        g = _group("System Health")
        L = QVBoxLayout(g)

        btn_row = QHBoxLayout()

        refresh_btn = QPushButton("Refresh Health Info")
        refresh_btn.clicked.connect(self._refresh_health)
        self._style_button(refresh_btn)
        btn_row.addWidget(refresh_btn)

        self._console_btn = QPushButton("🖥  Show Console")
        self._console_btn.clicked.connect(self._toggle_console)
        self._style_button(self._console_btn)
        btn_row.addWidget(self._console_btn)

        btn_row.addStretch()
        L.addLayout(btn_row)

        self._health_lbl = QLabel("Click refresh to check.")
        self._health_lbl.setStyleSheet(
            "color: #a6adc8; font-size: 11px; font-family: monospace;"
        )
        self._health_lbl.setWordWrap(True)
        L.addWidget(self._health_lbl)
        return g

    # ── Actions ───────────────────────────────────────────────────────────────

    def _save_tracker(self) -> None:
        settings.update({
            "polling_interval": self._polling_combo.currentData(),
            "idle_timeout":     self._idle_combo.currentData(),
            "device_name":      self._device_name_edit.text().strip(),
        })
        self._show_toast(self._data_status_lbl, "Tracker settings saved.")
        self.settings_changed.emit()

    def _on_privacy_mode_changed(self) -> None:
        mode = self._privacy_combo.currentData()
        privacy_manager.mode = mode
        logger.info(f"Privacy mode changed to: {mode}")

    def _add_excluded_app(self) -> None:
        app = self._new_app_edit.text().strip()
        if not app:
            return
        privacy_manager.add_excluded_app(app)
        self._excluded_list.addItem(app.lower())
        self._new_app_edit.clear()

    def _remove_excluded_app(self) -> None:
        row = self._excluded_list.currentRow()
        if row < 0:
            return
        app = self._excluded_list.item(row).text()
        privacy_manager.remove_excluded_app(app)
        self._excluded_list.takeItem(row)

    def _authenticate_google(self) -> None:
        if not self._sync_engine:
            self._sync_status_lbl.setText("Sync engine not available.")
            return
        self._sync_status_lbl.setText("Opening browser for authentication…")
        def _auth():
            ok = self._sync_engine.authenticate()
            txt = "✓ Authenticated" if ok else f"✗ {self._sync_engine.last_error}"
            color = "#a6e3a1" if ok else "#f38ba8"
            self._sync_status_lbl.setText(txt)
            self._sync_status_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")
        threading.Thread(target=_auth, daemon=True).start()

    def _sync_now(self) -> None:
        if not self._sync_engine:
            return
        self._sync_status_lbl.setText("Syncing…")
        def _do():
            ok = self._sync_engine.sync_now()
            self._sync_status_lbl.setText(
                "✓ Sync complete" if ok else "✗ Sync failed"
            )
        threading.Thread(target=_do, daemon=True).start()

    def _save_sync(self) -> None:
        settings.update({
            "spreadsheet_id": self._spreadsheet_edit.text().strip(),
            "sheet_name":     self._sheet_name_edit.text().strip() or "ActivityLog",
            "sync_interval":  self._sync_interval_combo.currentData(),
        })
        self._show_toast(self._sync_status_lbl, "Sync settings saved.")

    def _save_data_settings(self) -> None:
        settings.update({
            "retention_days": self._retention_combo.currentData(),
            "auto_backup":    self._auto_backup_cb.isChecked(),
        })
        self._show_toast(self._data_status_lbl, "Data settings saved.")

    def _backup_now(self) -> None:
        from config.settings import DB_PATH
        dest = create_backup(DB_PATH)
        msg = f"Backup created: {Path(dest).name}" if dest else "Backup failed."
        self._show_toast(self._data_status_lbl, msg)

    def _archive_data(self) -> None:
        days = settings.get("retention_days", 90)
        n = self._db.archive_old_data(days)
        self._show_toast(self._data_status_lbl, f"Archived {n} records older than {days} days.")

    def _restore_backup(self) -> None:
        from config.settings import DB_PATH
        backups = list_backups()
        if not backups:
            QMessageBox.information(self, "Restore", "No backups found.")
            return
        names = [f"{b['name']}  ({b['size_kb']} KB)" for b in backups[:10]]
        from PySide6.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(
            self, "Restore Backup", "Select backup:", names, 0, False
        )
        if not ok:
            return
        idx   = names.index(choice)
        bpath = Path(backups[idx]["path"])
        reply = QMessageBox.question(
            self, "Confirm Restore",
            f"Replace current database with:\n{bpath.name}\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            restore_backup(DB_PATH, bpath)
            self._show_toast(self._data_status_lbl, "Database restored. Restart recommended.")

    def _refresh_health(self) -> None:
        h = self._db.get_health()
        ping = h.get("last_ping", "never")
        txt = (
            f"Tracker alive:   {'Yes' if h['alive'] else 'No'}\n"
            f"Last ping:       {ping}\n"
            f"Uptime:          {h['uptime_sec']}s\n"
            f"DB size:         {h['db_size_mb']} MB\n"
            f"Total records:   {h['total_records']:,}\n"
            f"Pending sync:    {h['pending_sync']:,}"
        )
        self._health_lbl.setText(txt)

    def _create_console_batch(self) -> Path:
        """
        Create a temporary batch script that continuously displays the log.
        This is much more reliable than trying to use PowerShell.
        """
        log_file = LOG_DIR / "combined.log"
        if not log_file.exists():
            log_file = LOG_DIR / "tracker.log"
        if not log_file.exists():
            log_file = LOG_DIR / "main.log"

        # Create temp batch script
        batch_content = f'''@echo off
setlocal enabledelayedexpansion

title Activity Monitor - Console Log
color 0A
cls

echo.
echo ========================================
echo  Activity Monitor - Live Console Log
echo ========================================
echo.
echo Log file: {log_file}
echo.
echo Updates every 2 seconds (press Ctrl+C to exit)
echo.
echo ========================================
echo.

:loop
cls
echo Activity Monitor - Console Log
echo ========================================
echo Updated: %date% %time%
echo.
powershell -NoProfile -Command "(Get-Content '{log_file}' -ErrorAction SilentlyContinue | Select-Object -Last 100) -join [Environment]::NewLine"
echo.
echo ========================================
echo Press Ctrl+C to exit, window will close in 60s...
timeout /t 2 /nobreak >nul 2>&1
goto loop
'''

        # Write to temp file
        try:
            temp_fd, temp_path = tempfile.mkstemp(suffix=".bat", text=True)
            with open(temp_fd, 'w', encoding='utf-8') as f:
                f.write(batch_content)
            return Path(temp_path)
        except Exception as exc:
            logger.error(f"Failed to create temp batch file: {exc}")
            return None

    def _toggle_console(self) -> None:
        """
        Toggle the console window on/off.
        Uses a temporary batch script for better reliability.
        """
        with self._console_lock:
            # If process is already running, terminate it
            if self._console_proc is not None and self._console_proc.poll() is None:
                try:
                    self._console_proc.terminate()
                    self._console_proc.wait(timeout=2)
                except Exception as exc:
                    logger.warning(f"Failed to terminate console: {exc}")
                    try:
                        self._console_proc.kill()
                    except Exception:
                        pass

                self._console_proc = None

                # Clean up temp batch file
                if self._temp_batch_file and self._temp_batch_file.exists():
                    try:
                        self._temp_batch_file.unlink()
                    except Exception:
                        pass
                    self._temp_batch_file = None

                self._console_btn.setText("🖥  Show Console")
                self._process_check_timer.stop()
                logger.info("Console window closed by button click.")
                return

            # Prevent duplicate processes
            if self._console_proc is not None and self._console_proc.poll() is None:
                logger.warning("Console already open; ignoring duplicate request.")
                return

        # Create temporary batch script for tailing
        batch_path = self._create_console_batch()
        if not batch_path:
            logger.error("Could not create console batch script.")
            return

        self._temp_batch_file = batch_path

        # Launch the batch file directly (no shell wrapping)
        try:
            # Use CreationFlags=0x08000000 to launch in new window without blocking
            self._console_proc = subprocess.Popen(
                [str(batch_path)],
                creationflags=0x08000000,  # CREATE_NEW_WINDOW
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._console_btn.setText("🖥  Hide Console")
            logger.info(f"Console opened with batch script: {batch_path.name}")
            # Start periodic check to detect manual window close
            self._process_check_timer.start(1000)  # check every 1 second
        except Exception as exc:
            logger.error(f"Failed to open console: {exc}")
            self._console_proc = None
            if self._temp_batch_file and self._temp_batch_file.exists():
                try:
                    self._temp_batch_file.unlink()
                except Exception:
                    pass
                self._temp_batch_file = None

    def _check_console_process(self) -> None:
        """
        Periodic check: if the console process dies (user closed the window),
        update the button state to reflect reality.
        """
        with self._console_lock:
            if self._console_proc is None:
                self._process_check_timer.stop()
                return

            # poll() returns None if process is still alive, exit code otherwise
            if self._console_proc.poll() is not None:
                # Process is dead; user closed the window
                self._console_proc = None

                # Clean up temp batch file
                if self._temp_batch_file and self._temp_batch_file.exists():
                    try:
                        self._temp_batch_file.unlink()
                    except Exception:
                        pass
                    self._temp_batch_file = None

                self._console_btn.setText("🖥  Show Console")
                self._process_check_timer.stop()
                logger.debug("Console window was closed; button state synced.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _style_button(btn: QPushButton, danger: bool = False) -> None:
        color = "#f38ba8" if danger else "#4f8ef7"
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {color}22; color: {color};
                border: 1px solid {color}66; border-radius: 6px;
                padding: 5px 12px; font-size: 11px;
            }}
            QPushButton:hover {{ background: {color}44; }}
            QPushButton:pressed {{ background: {color}66; }}
        """)

    @staticmethod
    def _show_toast(label: QLabel, text: str) -> None:
        label.setText(text)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(4000, lambda: label.setText(""))