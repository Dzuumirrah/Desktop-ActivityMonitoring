"""
Privacy manager: orchestrates tracking consent, exclusion lists, and redaction.
Implements Section 1.3 and Phase 1 Priority 4 from the Roadmap.
"""

from config.settings import settings
from privacy.redactor import WindowTitleRedactor
from utils.logger import setup_logger

logger = setup_logger("privacy.manager")


class PrivacyManager:
    """
    Single point of control for all privacy decisions.

    Usage:
        pm = PrivacyManager()
        if pm.should_track("chrome.exe"):
            safe_title = pm.safe_title("chrome.exe", raw_title)
            # persist safe_title
    """

    MODES = {
        "off":  "Tracking disabled",
        "low":  "Track app names only; window titles hidden",
        "full": "Track everything with sensitive-pattern redaction",
    }

    def __init__(self) -> None:
        mode = settings.get("privacy_mode", "full")
        excluded_raw = settings.get("excluded_apps", [])
        if not isinstance(excluded_raw, list):
            logger.warning("excluded_apps is not a list; resetting")
            excluded = set()
        else:
            excluded = {app.lower() for app in excluded_raw if isinstance(app, str)}
        self._redactor = WindowTitleRedactor(mode=mode, sensitive_apps=excluded)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._redactor.mode

    @mode.setter
    def mode(self, value: str) -> None:
        self._redactor.set_mode(value)
        settings.set("privacy_mode", value)

    @property
    def excluded_apps(self) -> set[str]:
        return set(self._redactor.sensitive_apps)

    def should_track(self, process_name: str) -> bool:
        if self.mode == "off":
            return False
        if process_name.lower() in self._redactor.sensitive_apps:
            logger.debug(f"Excluded from tracking: {process_name}")
            return False
        return True

    def safe_title(self, process_name: str, window_title: str) -> str:
        return self._redactor.redact(process_name, window_title)

    def add_excluded_app(self, process_name: str) -> None:
        self._redactor.add_sensitive_app(process_name)
        current: list = settings.get("excluded_apps", [])
        proc = process_name.lower().strip()
        if proc not in current:
            current.append(proc)
            settings.set("excluded_apps", current)

    def remove_excluded_app(self, process_name: str) -> None:
        self._redactor.remove_sensitive_app(process_name)
        current: list = settings.get("excluded_apps", [])
        proc = process_name.lower().strip()
        if proc in current:
            current.remove(proc)
            settings.set("excluded_apps", current)

    def mode_description(self) -> str:
        return self.MODES.get(self.mode, "Unknown mode")


# Module-level singleton
privacy_manager = PrivacyManager()
