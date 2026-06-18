"""
ConsoleDialog — terminal-style view of the current session's logs.

Priority: shows only records from THIS run (streamed from
SessionLogHandler's in-memory buffer), so there is no noise from
previous sessions.

Fallback: "Open Log File ↗" button opens the on-disk .log in the
system text editor — the original behaviour, kept as a convenience.

Features
--------
• Colour-coded by log level (GitHub dark palette)
• Level filter dropdown (ALL / DEBUG / INFO / WARNING / ERROR)
• Auto-scroll toggle
• Real-time streaming: new records appear instantly via Qt signal bridge
• Copy all visible text to clipboard
• Exception tracebacks shown inline
"""

import logging
import os
import subprocess
import sys
import traceback as _tb
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFrame,
    QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from utils.session_log import session_handler

# ── Colour palette (GitHub dark) ──────────────────────────────────────────────
_FG = {
    logging.DEBUG:    "#6e7681",   # muted grey
    logging.INFO:     "#c9d1d9",   # near-white
    logging.WARNING:  "#f0b429",   # amber
    logging.ERROR:    "#f85149",   # red
    logging.CRITICAL: "#ff6b6b",   # bright red
}
_BG = {
    logging.WARNING:  "#2d2a1b",
    logging.ERROR:    "#2d1b1b",
    logging.CRITICAL: "#3d0000",
}
_TS_COLOR   = "#484f58"   # timestamp — muted
_NAME_COLOR = "#7ee787"   # logger name — green

_DIALOG_STYLE = """
QDialog {
    background: #0d1117;
    color: #c9d1d9;
}
QComboBox, QPushButton, QCheckBox {
    background: #161b22;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 11px;
}
QComboBox:hover, QPushButton:hover { background: #21262d; }
QComboBox QAbstractItemView {
    background: #161b22;
    color: #c9d1d9;
    selection-background-color: #264f78;
}
QLabel { color: #8b949e; font-size: 11px; }
"""

_TOOLBAR_STYLE = """
QFrame {
    background: #161b22;
    border-bottom: 1px solid #30363d;
}
"""

_EDITOR_STYLE = """
QPlainTextEdit {
    background: #0d1117;
    color: #c9d1d9;
    border: none;
    padding: 4px 8px;
    selection-background-color: #264f78;
}
"""


# ── Qt signal bridge (logging thread → main thread) ───────────────────────────

class _Relay(QObject):
    """Emit a Qt signal safely from any thread."""
    arrived = Signal(object)   # payload: logging.LogRecord


# ── Record formatter ──────────────────────────────────────────────────────────

def _render(record: logging.LogRecord) -> str:
    """
    Return a plain-text representation of one log record.
    Matches the log-file format (HH:MM:SS instead of full date since
    the console only shows the current session).
    """
    ts   = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
    lvl  = record.levelname[:8].ljust(8)
    name = record.name[:20].ljust(20)
    msg  = record.getMessage()

    if record.exc_info and record.exc_info[0]:
        exc = "".join(_tb.format_exception(*record.exc_info)).rstrip()
        msg = f"{msg}\n{exc}"

    return f"{ts}  {lvl}  {name}  {msg}"


# ── Dialog ────────────────────────────────────────────────────────────────────

class ConsoleDialog(QDialog):
    """
    Non-modal terminal-like dialog.

    Open once and leave it open; it streams log records in real time.
    Closing it unsubscribes from the handler so no callbacks fire into
    a destroyed widget.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Console  —  current session")
        self.resize(1000, 580)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, False)  # reuse on re-open

        self._min_level   = logging.DEBUG
        self._auto_scroll = True
        self._line_count  = 0

        # Signal bridge: logging thread emits, main thread renders
        self._relay = _Relay(self)
        self._relay.arrived.connect(self._on_record, Qt.QueuedConnection)

        self.setStyleSheet(_DIALOG_STYLE)
        self._build_ui()
        self._load_snapshot()

        session_handler.subscribe(self._relay.arrived.emit)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._make_toolbar())

        # ── Terminal text area ─────────────────────────────────────────────
        self._editor = QPlainTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setMaximumBlockCount(15_000)
        self._editor.setStyleSheet(_EDITOR_STYLE)

        font = QFont()
        font.setFamilies(["Cascadia Code", "Consolas", "Courier New", "monospace"])
        font.setPointSize(10)
        self._editor.setFont(font)

        root.addWidget(self._editor)

        # ── Status bar ─────────────────────────────────────────────────────
        status = QFrame()
        status.setStyleSheet(
            "QFrame { background: #161b22; border-top: 1px solid #30363d; }"
        )
        status_l = QHBoxLayout(status)
        status_l.setContentsMargins(8, 2, 8, 2)
        self._status_lbl = QLabel("0 lines")
        self._status_lbl.setStyleSheet("color: #484f58; font-size: 10px;")
        status_l.addWidget(self._status_lbl)
        status_l.addStretch()
        hint = QLabel("Tip: use level filter to reduce noise  •  'Open Log File' for full history")
        hint.setStyleSheet("color: #30363d; font-size: 10px;")
        status_l.addWidget(hint)
        root.addWidget(status)

    def _make_toolbar(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(_TOOLBAR_STYLE)
        bar = QHBoxLayout(frame)
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(8)

        bar.addWidget(QLabel("Level:"))

        self._filter = QComboBox()
        self._filter.addItems(["ALL", "DEBUG", "INFO", "WARNING", "ERROR"])
        self._filter.currentTextChanged.connect(self._on_filter)
        bar.addWidget(self._filter)

        self._scroll_cb = QCheckBox("Auto-scroll")
        self._scroll_cb.setChecked(True)
        self._scroll_cb.toggled.connect(lambda v: setattr(self, "_auto_scroll", v))
        bar.addWidget(self._scroll_cb)

        bar.addStretch()

        for label, slot, tip in [
            ("Clear View",     self._clear_view,  "Clear the on-screen view (keeps in-memory buffer)"),
            ("Copy All",       self._copy_all,    "Copy visible text to clipboard"),
            ("Open Log File ↗", self._open_log_file,
             "Fallback: open the on-disk .log in your text editor (shows all sessions)"),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)

        return frame

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_snapshot(self) -> None:
        """Replay records already captured before the dialog was opened."""
        for record in session_handler.snapshot():
            self._append(record, _initial=True)
        if self._auto_scroll:
            self._editor.moveCursor(QTextCursor.End)
        self._refresh_status()

    def _on_record(self, record: logging.LogRecord) -> None:
        """Called on the Qt main thread whenever a new record arrives."""
        self._append(record)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _append(self, record: logging.LogRecord, _initial: bool = False) -> None:
        if record.levelno < self._min_level:
            return

        text  = _render(record)
        lines = text.split("\n")

        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.End)

        fg = _FG.get(record.levelno, "#c9d1d9")
        bg = _BG.get(record.levelno, "")

        # First line: timestamp (muted) + rest (level colour)
        self._insert_line(cursor, lines[0], fg, bg, bold=(record.levelno >= logging.CRITICAL))

        # Continuation lines (e.g. traceback)
        for extra in lines[1:]:
            self._insert_line(cursor, "          " + extra, fg, bg)

        self._line_count += 1

        if not _initial:
            if self._auto_scroll:
                self._editor.setTextCursor(cursor)
                self._editor.ensureCursorVisible()
            self._refresh_status()

    def _insert_line(
        self,
        cursor: QTextCursor,
        text:   str,
        fg:     str,
        bg:     str,
        bold:   bool = False,
    ) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(fg))
        if bg:
            fmt.setBackground(QColor(bg))
        if bold:
            fmt.setFontWeight(700)
        cursor.insertText(text + "\n", fmt)

    # ── Toolbar actions ───────────────────────────────────────────────────────

    def _on_filter(self, text: str) -> None:
        mapping = {
            "ALL": logging.DEBUG, "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,  "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
        }
        self._min_level = mapping.get(text, logging.DEBUG)
        # Redraw from the in-memory snapshot with the new filter
        self._editor.clear()
        self._line_count = 0
        for record in session_handler.snapshot():
            self._append(record, _initial=True)
        if self._auto_scroll:
            self._editor.moveCursor(QTextCursor.End)
        self._refresh_status()

    def _clear_view(self) -> None:
        """Clear the visible text only — the in-memory buffer is untouched."""
        self._editor.clear()
        self._line_count = 0
        self._status_lbl.setText("View cleared  (buffer intact — reopen to replay)")

    def _copy_all(self) -> None:
        QApplication.clipboard().setText(self._editor.toPlainText())

    def _open_log_file(self) -> None:
        """
        Fallback: open the on-disk tracker.log in the system text editor.
        This is the original behaviour, kept because the file contains
        records from all sessions and can be searched with external tools.
        """
        from utils.logger import LOG_DIR

        candidates = sorted(
            LOG_DIR.glob("*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            QMessageBox.information(
                self, "No log files",
                f"No .log files found in:\n{LOG_DIR}"
            )
            return

        log_file = LOG_DIR / "tracker.log"
        if not log_file.exists():
            log_file = candidates[0]

        try:
            if sys.platform == "win32":
                os.startfile(str(log_file))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(log_file)])
            else:
                subprocess.Popen(["xdg-open", str(log_file)])
        except Exception as exc:
            QMessageBox.warning(self, "Cannot open file", str(exc))

    def _refresh_status(self) -> None:
        self._status_lbl.setText(f"{self._line_count:,} lines this session")

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        session_handler.unsubscribe(self._relay.arrived.emit)
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        """Re-subscribe when the dialog is shown again after being hidden."""
        session_handler.subscribe(self._relay.arrived.emit)
        super().showEvent(event)