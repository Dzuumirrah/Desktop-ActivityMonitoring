"""
Cloud sync engine: Google Sheets with batched upload, retry, and deduplication.

Issue 1 fix: sync_interval is now read inside the wait() call on every
iteration so a change in Settings takes effect within one cycle.
"""

import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import settings, DATA_DIR
from core.database import get_db
from utils.logger import sync_log as logger

TOKEN_FILE       = DATA_DIR / "google_token.json"
CREDENTIALS_FILE = DATA_DIR / "google_credentials.json"

BATCH_SIZE   = 50
MAX_RETRIES  = 3
BASE_BACKOFF = 2   # seconds


def _build_sheets_service():
    """
    Build and return an authenticated Google Sheets API service object.
    Uses OAuth 2.0 browser flow; tokens stored locally.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
        creds  = None

        if TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not CREDENTIALS_FILE.exists():
                    raise FileNotFoundError(
                        f"Google credentials not found at {CREDENTIALS_FILE}. "
                        "Download credentials.json from Google Cloud Console."
                    )
                flow  = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_FILE), SCOPES
                )
                creds = flow.run_local_server(port=0)
            TOKEN_FILE.write_text(creds.to_json())

        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    except ImportError as exc:
        raise ImportError(
            "Google API libraries not installed. "
            "Run: pip install gspread google-auth google-auth-oauthlib"
        ) from exc


class SheetsSyncEngine:
    """
    Uploads pending activity records to a Google Sheet.

    Lifecycle::

        engine = SheetsSyncEngine()
        engine.start()
        engine.stop()
    """

    HEADER_ROW = [
        "activity_id", "start_time", "end_time", "duration_seconds",
        "process_name", "executable_path", "window_title",
        "device_name", "device_id", "is_idle",
    ]

    def __init__(self) -> None:
        self._db             = get_db()
        self._stop_event     = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._service        = None
        self._authenticated  = False
        self._last_error: Optional[str] = None
        self._on_status: Optional[callable] = None

    # ── Authentication ────────────────────────────────────────────────────────

    def authenticate(self) -> bool:
        try:
            self._service       = _build_sheets_service()
            self._authenticated = True
            logger.info("Google Sheets authenticated.")
            return True
        except Exception as exc:
            self._last_error    = str(exc)
            self._authenticated = False
            logger.error(f"Authentication failed: {exc}")
            return False

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def set_status_callback(self, fn: callable) -> None:
        self._on_status = fn

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="SheetsSyncEngine", daemon=True
        )
        self._thread.start()
        logger.info("Sync engine started.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)
        logger.info("Sync engine stopped.")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        """
        sync_interval is read on every wait() call so a change in the
        Settings page applies on the next cycle without a restart.
        """
        while not self._stop_event.is_set():
            if self._authenticated:
                try:
                    self._sync_cycle()
                except Exception as exc:
                    logger.error(f"Sync cycle error: {exc}", exc_info=True)
                    self._last_error = str(exc)
            # ── Live settings read (Issue 1 fix) ──────────────────────────────
            interval_sec = settings.get("sync_interval", 15) * 60
            self._stop_event.wait(timeout=interval_sec)

    def _sync_cycle(self) -> None:
        spreadsheet_id = settings.get("spreadsheet_id", "")
        sheet_name     = settings.get("sheet_name", "ActivityLog")
        if not spreadsheet_id:
            logger.debug("No spreadsheet_id configured; skipping sync.")
            return

        pending = self._db.get_pending_sync(limit=BATCH_SIZE)
        if not pending:
            logger.debug("No pending records.")
            return

        logger.info(f"Syncing {len(pending)} records to Sheets…")
        self._ensure_header(spreadsheet_id, sheet_name)

        batch_id = str(uuid.uuid4())
        uploaded = self._upload_batch(pending, spreadsheet_id, sheet_name, batch_id)

        if uploaded:
            ids = [r["activity_id"] for r in pending]
            self._db.mark_synced(ids, batch_id)
            self._db.log_sync_result(len(pending), "success", batch_id)
            logger.info(f"Synced {len(pending)} records (batch {batch_id[:8]})")
            if self._on_status:
                self._on_status("synced", len(pending))
        else:
            ids = [r["activity_id"] for r in pending]
            self._db.mark_sync_failed(ids, self._last_error or "unknown")
            self._db.log_sync_result(0, "failed", batch_id, self._last_error)

    # ── Upload ────────────────────────────────────────────────────────────────

    def _upload_batch(
        self,
        records: list[dict],
        spreadsheet_id: str,
        sheet_name: str,
        batch_id: str,
    ) -> bool:
        """Upload with exponential-backoff retry (MAX_RETRIES attempts)."""
        # Deduplicate against what's already in the sheet
        existing = self._get_remote_ids(spreadsheet_id, sheet_name)
        to_upload = [r for r in records if r["activity_id"] not in existing]
        if not to_upload:
            logger.info("All records already synced.")
            return True

        rows = [self._record_to_row(r) for r in to_upload]
        body = {"values": rows}

        for attempt in range(MAX_RETRIES):
            try:
                self._service.spreadsheets().values().append(
                    spreadsheetId=spreadsheet_id,
                    range=f"'{sheet_name}'!A:J",
                    valueInputOption="USER_ENTERED",
                    body=body,
                ).execute()
                return True
            except Exception as exc:
                wait = BASE_BACKOFF ** attempt
                self._last_error = str(exc)
                if attempt < MAX_RETRIES - 1:
                    logger.warning(
                        f"Upload attempt {attempt+1} failed ({exc}); "
                        f"retry in {wait}s"
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        f"Upload failed after {MAX_RETRIES} attempts: {exc}"
                    )
        return False

    def _get_remote_ids(self, spreadsheet_id: str, sheet_name: str) -> set[str]:
        """Return the set of activity_ids already present in the sheet."""
        try:
            result = self._service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"'{sheet_name}'!A:A",
            ).execute()
            values = result.get("values", [])
            return {row[0] for row in values[1:] if row}
        except Exception as exc:
            logger.warning(f"Could not fetch remote IDs: {exc}")
            return set()

    def _ensure_header(self, spreadsheet_id: str, sheet_name: str) -> None:
        """Write header row if the sheet is empty."""
        try:
            result = self._service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"'{sheet_name}'!A1",
            ).execute()
            if not result.get("values"):
                self._service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"'{sheet_name}'!A1",
                    valueInputOption="RAW",
                    body={"values": [self.HEADER_ROW]},
                ).execute()
                logger.info("Header row written to sheet.")
        except Exception as exc:
            logger.warning(f"Could not ensure header: {exc}")

    @staticmethod
    def _record_to_row(r: dict) -> list:
        return [
            r.get("activity_id",    ""),
            str(r.get("start_time", "")),
            str(r.get("end_time",   "")),
            r.get("duration_seconds", 0),
            r.get("process_name",   ""),
            r.get("executable_path",""),
            r.get("window_title",   ""),
            r.get("device_name",    ""),
            r.get("device_id",      ""),
            1 if r.get("is_idle") else 0,
        ]

    def sync_now(self) -> bool:
        """Force an immediate sync cycle (called from GUI)."""
        if not self._authenticated:
            logger.warning("Cannot sync: not authenticated.")
            return False
        try:
            self._sync_cycle()
            return True
        except Exception as exc:
            logger.error(f"Manual sync failed: {exc}")
            return False