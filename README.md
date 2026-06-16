# Activity Monitor

A Windows desktop application that records computer activity in the background,
stores it locally in SQLite, syncs to Google Sheets, and provides a full dashboard.

---

## Quick Start

### Windows
```bat
run.bat
```

### Linux / macOS (dev)
```bash
chmod +x run.sh && ./run.sh
```

---

## Architecture

```
main.py
  ├── core/tracker.py          ← Background polling loop (1-second default)
  │     ├── core/idle_detector.py    ← Multi-signal idle detection
  │     ├── core/session_manager.py  ← Window-boundary logic
  │     └── core/database.py         ← SQLite (WAL, indexes, migrations)
  ├── privacy/
  │     ├── manager.py         ← Tracking consent & exclusion list
  │     └── redactor.py        ← Pattern + per-app title redaction
  ├── sync/sheets.py           ← Google Sheets (batch, retry, dedup)
  ├── utils/
  │     ├── logger.py          ← Rotating file + console logs
  │     └── backup.py          ← Daily SQLite backups
  ├── config/settings.py       ← Validated persistent settings
  └── gui/
        ├── main_window.py     ← PySide6 dark-themed shell
        └── pages/
              ├── dashboard.py    ← KPIs, top apps, recent sessions
              ├── timeline.py     ← Gantt timeline with date picker
              ├── statistics.py   ← Charts: daily, hourly heatmap, top-10
              └── settings.py     ← All user configuration
```

---

## Features Implemented

### Phase 0 – Quick Wins (all 10)
| # | Feature | File |
|---|---------|------|
| 1 | Database indexes | `core/database.py` |
| 2 | Input validation | `core/models.py` |
| 3 | WAL crash recovery + integrity check | `core/database.py` |
| 4 | Rotating file logging | `utils/logger.py` |
| 5 | Health check (60 s heartbeat) | `core/database.py`, `core/tracker.py` |
| 6 | Privacy redaction | `privacy/redactor.py` |
| 7 | Duplicate detection (SHA-256 activity_id) | `core/database.py` |
| 8 | Data retention + archival | `core/database.py` |
| 9 | Settings validation | `config/settings.py` |
| 10 | Update check (weekly, GitHub API) | `main.py` |

### Phase 1 – Core Stability
| Feature | File |
|---------|------|
| Multi-signal idle detection (Win32 + pynput + screen lock) | `core/idle_detector.py` |
| Proper session boundaries (HWND + process restart + title change) | `core/session_manager.py` |
| Cloud sync with retry + exponential backoff + dedup | `sync/sheets.py` |
| Privacy framework (Off / Low / Full modes, exclusion list) | `privacy/manager.py` |

### Database Schema Improvements
- `activity_id` SHA-256 idempotency key
- `sync_status / sync_batch_id / sync_time / sync_error` columns
- `is_idle / idle_reason` columns
- `device_id` persistent UUID
- CHECK constraint: `start_time <= end_time`
- Indexes on `start_time`, `(device, process)`, `sync_status`, `activity_id`
- Schema migration system with version tracking
- Auto-archival to `activity_log_archive`

---

## Configuration

All settings persist to `%APPDATA%\ActivityMonitor\settings.json` (Windows)
or `~/.activity_monitor/settings.json`.

| Key | Default | Valid values |
|-----|---------|-------------|
| `polling_interval` | `1` s | 1, 2, 5, 10 |
| `idle_timeout` | `300` s | 60, 120, 300, 600 |
| `sync_interval` | `15` min | 5, 15, 30, 60 |
| `retention_days` | `90` | 30, 60, 90, 180, 365 |
| `privacy_mode` | `"full"` | `"off"`, `"low"`, `"full"` |
| `excluded_apps` | `[]` | list of `process.exe` strings |

---

## Google Sheets Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → enable **Google Sheets API**
3. Create **OAuth 2.0 Desktop** credentials → download `credentials.json`
4. Place `credentials.json` in `%APPDATA%\ActivityMonitor\`
5. In the app: **Settings → Authenticate Google**
6. Paste your **Spreadsheet ID** (from the Sheets URL) and save

---

## Privacy Modes

| Mode | What is tracked |
|------|----------------|
| `off` | Nothing – tracker disabled |
| `low` | App names only; all window titles hidden |
| `full` | App + window title with sensitive-pattern redaction |

Sensitive apps (Signal, Outlook, KeePass, etc.) are fully redacted in all modes.
Add custom apps via **Settings → Privacy → Excluded Apps**.

---

## File Locations (Windows)

| File | Purpose |
|------|---------|
| `%APPDATA%\ActivityMonitor\activity_monitor.db` | Main SQLite database |
| `%APPDATA%\ActivityMonitor\backups\` | Daily backups (30 kept) |
| `%APPDATA%\ActivityMonitor\logs\` | Rotating logs (10 MB × 5) |
| `%APPDATA%\ActivityMonitor\settings.json` | User settings |
| `%APPDATA%\ActivityMonitor\google_token.json` | OAuth token |

---

## Requirements

- Python 3.11+
- Windows 10/11 (for full tracker; GUI runs cross-platform)
- pywin32, psutil, pynput, PySide6, APScheduler
- (Optional) gspread, google-auth for cloud sync
