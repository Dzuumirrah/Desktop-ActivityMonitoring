"""
Activity data model with strict input validation.
Implements Quick Win #2 (Input Validation Layer) from Roadmap.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("models")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize(text: str, max_len: int) -> str:
    text = _CONTROL_CHARS.sub("", text or "").strip()
    return text[:max_len]


@dataclass
class ActivityRecord:
    """
    Validated, immutable snapshot of a single focus session.
    Raises ValueError on constraint violations.
    """

    process_name:    str
    window_title:    str
    start_time:      datetime
    end_time:        datetime
    device_name:     str
    device_id:       str

    # Optional enrichment
    process_id:      Optional[int]  = None
    executable_path: str            = ""
    window_handle:   Optional[int]  = None
    is_idle:         bool           = False
    idle_reason:     Optional[str]  = None
    activity_id:     str            = field(default="", init=False)

    MAX_PROCESS_NAME = 255
    MAX_WINDOW_TITLE = 1000
    MAX_DEVICE_NAME  = 128
    MAX_FUTURE_SEC   = 5        # allow 5s clock skew
    MAX_DURATION_SEC = 86_400   # 24 h hard cap

    def __post_init__(self) -> None:
        self._validate_and_normalize()

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_and_normalize(self) -> None:
        # Process name
        if not self.process_name:
            raise ValueError("process_name must not be empty")
        self.process_name = _sanitize(self.process_name, self.MAX_PROCESS_NAME)
        if not self.process_name:
            raise ValueError("process_name is empty after sanitization")

        # Window title – truncate, never raise
        self.window_title = _sanitize(self.window_title or "", self.MAX_WINDOW_TITLE)

        # Device name
        self.device_name = _sanitize(self.device_name or "", self.MAX_DEVICE_NAME)
        if not self.device_name:
            self.device_name = "unknown"

        # Times
        now = datetime.now()
        if self.start_time > (now.replace(second=0) if False else
                              datetime(now.year, now.month, now.day,
                                       now.hour, now.minute, now.second + self.MAX_FUTURE_SEC
                                       if now.second + self.MAX_FUTURE_SEC < 60 else 59)):
            raise ValueError(f"start_time {self.start_time} is in the future")

        if self.start_time > self.end_time:
            raise ValueError(f"start_time {self.start_time} > end_time {self.end_time}")

        duration = self.duration_seconds
        if duration < 0:
            raise ValueError(f"Negative duration: {duration}s")
        if duration > self.MAX_DURATION_SEC:
            raise ValueError(f"Duration {duration}s exceeds 24-hour cap")

        # Executable path – strip only
        self.executable_path = (self.executable_path or "").strip()

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def duration_seconds(self) -> int:
        return int((self.end_time - self.start_time).total_seconds())

    def to_dict(self) -> dict:
        return {
            "activity_id":     self.activity_id,
            "start_time":      self.start_time,
            "end_time":        self.end_time,
            "process_name":    self.process_name,
            "process_id":      self.process_id,
            "executable_path": self.executable_path,
            "window_title":    self.window_title,
            "window_handle":   self.window_handle,
            "device_name":     self.device_name,
            "device_id":       self.device_id,
            "is_idle":         self.is_idle,
            "idle_reason":     self.idle_reason,
        }

    @classmethod
    def from_row(cls, row: dict) -> "ActivityRecord":
        obj = cls.__new__(cls)
        for k, v in row.items():
            if hasattr(obj, k):
                object.__setattr__(obj, k, v)
        return obj
