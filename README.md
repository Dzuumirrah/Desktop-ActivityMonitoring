# Activity Monitor (v1.1)

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
  ├── core/tracker.py              ← Background polling loop (1-second default)
  │     ├── core/idle_detector.py       ← Multi-signal idle detection
  │     ├── core/session_manager.py     ← Window-boundary logic
  │     └── core/database.py            ← SQLite (WAL, indexes, migrations)
  ├── privacy/
  │     ├── manager.py             ← Tracking consent & exclusion list
  │     └── redactor.py            ← Pattern + per-app title redaction
  ├── sync/sheets.py               ← Google Sheets (batch, retry, dedup)
  ├── utils/
  │     ├── logger.py              ← Rotating file + console logs
  │     ├── session_log.py         ← In-memory log ring buffer (current session)
  │     ├── backup.py              ← Daily SQLite backups
  │     └── power_monitor.py       ← Windows sleep / wake / hibernate events
  ├── config/settings.py           ← Validated persistent settings
  └── gui/
        ├── main_window.py         ← PySide6 dark-themed shell + system tray
        └── pages/
        │     ├── dashboard.py        ← KPIs, top apps, recent sessions
        │     ├── timeline.py         ← Gantt timeline with date picker + zoom
        │     ├── statistics.py       ← Charts: daily, hourly heatmap, top-10
        │     └── settings.py         ← All user configuration
        └── widgets/
              ├── charts.py           ← BarChart, DailyBarChart, HourlyHeatmap, StatCard
              ├── status_bar.py       ← Persistent bottom bar (tracker health, sync lag)
              └── console_dialog.py   ← Live in-app terminal (current-session logs)
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
| Fullscreen suppression (suppress idle during media playback) | `core/idle_detector.py` |
| Proper session boundaries (HWND + process restart + title change) | `core/session_manager.py` |
| Session continuation after idle / wake (no 300-s stubs) | `core/tracker.py` |
| Cloud sync with retry + exponential backoff + dedup | `sync/sheets.py` |
| Privacy framework (Off / Low / Full modes, exclusion list) | `privacy/manager.py` |
| Sleep / hibernate / wake detection (Win32 message-only window) | `utils/power_monitor.py` |
| System tray (close-to-tray, right-click quit) | `gui/main_window.py` |

### GUI
| Feature | File |
|---------|------|
| Dark-themed shell with sidebar navigation | `gui/main_window.py` |
| Dashboard with KPI cards, top-apps bar chart, sessions table | `gui/pages/dashboard.py` |
| Timeline with zoom (Ctrl+scroll), pan (Shift+scroll), date picker | `gui/pages/timeline.py` |
| Statistics: daily bar chart, hourly heatmap, top-10 apps | `gui/pages/statistics.py` |
| Settings: tracker, privacy, sync, data management, health | `gui/pages/settings.py` |
| Live in-app console (current-session logs, level filter, auto-scroll) | `gui/widgets/console_dialog.py` |
| Persistent status bar (tracker alive, idle state, sync status) | `gui/widgets/status_bar.py` |
| Placeholder visuals on all chart widgets before data arrives | `gui/widgets/charts.py` |

### Database Schema
- `activity_id` SHA-256 idempotency key
- `sync_status / sync_batch_id / sync_time / sync_error` columns
- `is_idle / idle_reason` columns
- `device_id` persistent UUID
- CHECK constraint: `start_time <= end_time`
- Indexes on `start_time`, `(device, process)`, `sync_status`, `activity_id`
- Schema migration system (v1 → v3) with version tracking
- Auto-archival to `activity_log_archive`

---

## In-App Console

The **Settings → System Health → Show Console** button opens a live terminal
window showing log output from the current session only.

| Feature | Detail |
|---------|--------|
| Real-time streaming | New records appear instantly via Qt signal bridge |
| Level filter | ALL / DEBUG / INFO / WARNING / ERROR dropdown |
| Auto-scroll | Toggle to pin or free the view |
| Copy all | Copies visible text to clipboard |
| Open Log File | Fallback: opens the on-disk `.log` in your system editor |
| Colour coding | DEBUG (grey) · INFO (white) · WARNING (amber) · ERROR (red) |

Log records are stored in a 10,000-record in-memory ring buffer
(`utils/session_log.py`) that is fed by every logger in the application.
The dialog can be closed and re-opened without losing records.

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

All settings take effect **immediately** (no restart required) — the tracker
re-reads `polling_interval` and `idle_timeout` on every cycle, and the sync
engine re-reads `sync_interval` on every wait.

---

## Google Sheets Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → enable **Google Sheets API**
3. Create **OAuth 2.0 Desktop** credentials → download `credentials.json`
4. Rename and place it at `%APPDATA%\ActivityMonitor\google_credentials.json`
5. In the app: **Settings → Cloud Sync → Authenticate Google**
6. Paste your **Spreadsheet ID** (from the Sheets URL) and click **Save Sync Settings**

> **Note:** If your OAuth consent screen is in *Testing* mode, add your Google
> account as a test user. To allow any Google account, publish the app to
> *Production* in the consent screen settings.

---

## Privacy Modes

| Mode | What is tracked |
|------|----------------|
| `off` | Nothing – tracker disabled |
| `low` | App names only; all window titles hidden |
| `full` | App + window title with sensitive-pattern redaction |

Sensitive apps (Signal, Outlook, KeePass, etc.) are fully redacted in all modes.
Add custom apps via **Settings → Privacy → Excluded Apps**.

Sensitive patterns always redacted in `full` mode: passwords, tokens, API keys,
secrets, SSNs, credit card numbers, salary mentions, and medical references.

---

## Diagnostics

Run `diagnose.py` to identify issues before opening a bug report:

```bat
python diagnose.py
```

Checks: window detection (Win32), idle detection (GetLastInputInfo), pynput
listeners, database integrity, settings, privacy manager, tracker initialisation,
and log file presence.

---

## File Locations (Windows)

| File | Purpose |
|------|---------|
| `%APPDATA%\ActivityMonitor\activity_monitor.db` | Main SQLite database |
| `%APPDATA%\ActivityMonitor\backups\` | Daily backups (30 kept, 1 GB cap) |
| `%APPDATA%\ActivityMonitor\logs\` | Rotating logs (10 MB × 5 per module) |
| `%APPDATA%\ActivityMonitor\settings.json` | User settings |
| `%APPDATA%\ActivityMonitor\google_token.json` | OAuth token |
| `%APPDATA%\ActivityMonitor\google_credentials.json` | OAuth client credentials |
| `%APPDATA%\ActivityMonitor\.device_id` | Persistent device UUID |

---

## Requirements

- Python 3.11+
- Windows 10/11 (for full tracker; GUI runs cross-platform)
- `pywin32`, `psutil`, `pynput`, `PySide6`, `APScheduler`
- (Optional) `gspread`, `google-auth`, `google-auth-oauthlib`, `google-api-python-client` for cloud sync

Install all dependencies via:
```bat
pip install -r requirements.txt
```