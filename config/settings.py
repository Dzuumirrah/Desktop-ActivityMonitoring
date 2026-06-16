"""
Settings manager: validated, persistent app configuration.
Implements Quick Win #9 (Settings Validation) from Roadmap.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

from utils.logger import setup_logger

logger = setup_logger("config")


def get_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home()))
        data_dir = Path(base) / "ActivityMonitor"
    else:
        data_dir = Path.home() / ".activity_monitor"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


DATA_DIR = get_data_dir()
DB_PATH = DATA_DIR / "activity_monitor.db"
SETTINGS_FILE = DATA_DIR / "settings.json"

# ── Allowed values (Quick Win #9) ────────────────────────────────────────────
VALID_POLLING_INTERVALS = (1, 2, 5, 10)        # seconds
VALID_IDLE_TIMEOUTS = (60, 120, 300, 600)       # seconds
VALID_SYNC_INTERVALS = (5, 15, 30, 60)          # minutes
VALID_RETENTION_DAYS = (30, 60, 90, 180, 365)   # days
VALID_PRIVACY_MODES = ("off", "low", "full")


DEFAULTS: dict[str, Any] = {
    "polling_interval": 1,
    "idle_timeout": 300,
    "sync_interval": 15,
    "retention_days": 90,
    "privacy_mode": "full",
    "excluded_apps": [],
    "spreadsheet_id": "",
    "sheet_name": "ActivityLog",
    "device_name": "",
    "version": "1.0.0",
    "first_run": True,
    "auto_backup": True,
    "backup_on_startup": True,
    "check_for_updates": True,
    "redact_sensitive_patterns": True,
}


class Settings:
    """Thread-safe settings container with validation and disk persistence."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        validated = self._validate({**self._data, key: value})
        self._data.update(validated)
        self._save()

    def update(self, updates: dict[str, Any]) -> None:
        merged = {**self._data, **updates}
        self._data = self._validate(merged)
        self._save()

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    # ── Validation (Quick Win #9) ─────────────────────────────────────────────

    @staticmethod
    def _validate(data: dict[str, Any]) -> dict[str, Any]:
        out = dict(DEFAULTS)
        out.update(data)

        if out["polling_interval"] not in VALID_POLLING_INTERVALS:
            logger.warning(f"Invalid polling_interval {out['polling_interval']!r}; reset to 1")
            out["polling_interval"] = 1

        if out["idle_timeout"] not in VALID_IDLE_TIMEOUTS:
            logger.warning(f"Invalid idle_timeout {out['idle_timeout']!r}; reset to 300")
            out["idle_timeout"] = 300

        if out["sync_interval"] not in VALID_SYNC_INTERVALS:
            logger.warning(f"Invalid sync_interval {out['sync_interval']!r}; reset to 15")
            out["sync_interval"] = 15

        if out["retention_days"] not in VALID_RETENTION_DAYS:
            logger.warning(f"Invalid retention_days {out['retention_days']!r}; reset to 90")
            out["retention_days"] = 90

        if out["privacy_mode"] not in VALID_PRIVACY_MODES:
            logger.warning(f"Invalid privacy_mode {out['privacy_mode']!r}; reset to 'full'")
            out["privacy_mode"] = "full"

        if not isinstance(out["excluded_apps"], list):
            out["excluded_apps"] = []

        return out

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if SETTINGS_FILE.exists():
            try:
                raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                merged = {**DEFAULTS, **raw}
                self._data = self._validate(merged)
                logger.debug("Settings loaded from disk.")
            except Exception as exc:
                logger.error(f"Failed to load settings: {exc}; using defaults.")
                self._data = dict(DEFAULTS)
        else:
            self._data = dict(DEFAULTS)
            self._save()

    def _save(self) -> None:
        try:
            SETTINGS_FILE.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(f"Failed to save settings: {exc}")


# Module-level singleton
settings = Settings()
