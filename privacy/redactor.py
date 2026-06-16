"""
Window title redaction: pattern-based and per-app rules.
Implements Quick Win #6 and Section 1.3/5.2 from Project Analysis.
"""

import re
from typing import Callable

from utils.logger import setup_logger

logger = setup_logger("privacy.redactor")


# ── Sensitive keyword patterns ────────────────────────────────────────────────

_SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"password", re.I),  "[PASSWORD HIDDEN]"),
    (re.compile(r"\btoken\b",  re.I), "[TOKEN HIDDEN]"),
    (re.compile(r"\bapi[ _-]?key\b", re.I), "[KEY HIDDEN]"),
    (re.compile(r"\bsecret\b", re.I), "[SECRET HIDDEN]"),
    (re.compile(r"\bssn\b",    re.I), "[SSN HIDDEN]"),
    (re.compile(r"\bcredit card\b", re.I), "[CARD HIDDEN]"),
    (re.compile(r"\bsalary\b", re.I), "[SALARY HIDDEN]"),
    (re.compile(r"\bmedical\b", re.I), "[MEDICAL HIDDEN]"),
]

# ── Default fully-redacted applications ──────────────────────────────────────

DEFAULT_SENSITIVE_APPS: set[str] = {
    "outlook.exe",
    "thunderbird.exe",
    "signal.exe",
    "whatsapp.exe",
    "telegram.exe",
    "keepass.exe",
    "1password.exe",
    "bitwarden.exe",
    "lastpass.exe",
}


class WindowTitleRedactor:
    """
    Applies privacy rules to window titles before persistence.

    Modes:
      'off'  – tracking is disabled (handled upstream; returns title unchanged)
      'low'  – retain only process name, blank title
      'full' – pattern-level redaction only (default)
    """

    def __init__(
        self,
        mode: str = "full",
        sensitive_apps: set[str] | None = None,
        extra_patterns: list[tuple[re.Pattern, str]] | None = None,
    ) -> None:
        self.mode = mode
        self.sensitive_apps: set[str] = (sensitive_apps or set()) | DEFAULT_SENSITIVE_APPS
        self._extra_patterns = extra_patterns or []

    # ── Public API ────────────────────────────────────────────────────────────

    def redact(self, process_name: str, window_title: str) -> str:
        """Return a privacy-safe version of window_title."""
        if not window_title:
            return ""

        proc = process_name.lower().strip()

        # Low mode: hide all titles
        if self.mode == "low":
            return f"[{process_name}]"

        # Full redaction for sensitive apps
        if proc in self.sensitive_apps:
            logger.debug(f"Full redaction: {process_name}")
            return f"[{process_name} – REDACTED]"

        # Pattern-based redaction
        title = window_title
        for pattern, replacement in _SENSITIVE_PATTERNS + self._extra_patterns:
            if pattern.search(title):
                logger.debug(f"Pattern redaction on '{proc}': {pattern.pattern!r}")
                return replacement

        return title

    def add_sensitive_app(self, process_name: str) -> None:
        self.sensitive_apps.add(process_name.lower().strip())
        logger.info(f"Added sensitive app: {process_name}")

    def remove_sensitive_app(self, process_name: str) -> None:
        self.sensitive_apps.discard(process_name.lower().strip())

    def set_mode(self, mode: str) -> None:
        if mode not in ("off", "low", "full"):
            raise ValueError(f"Unknown privacy mode: {mode!r}")
        self.mode = mode
        logger.info(f"Privacy mode changed to: {mode}")
