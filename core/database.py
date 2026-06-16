"""
Database layer: SQLite with WAL mode, proper indexes, and migration support.

Implements:
- Quick Win #1  (database indexes)
- Quick Win #3  (WAL crash recovery)
- Quick Win #7  (duplicate detection via activity_id)
- Quick Win #8  (data retention / archival)
- Section 1.5   (improved schema from Project Analysis)
"""

import hashlib
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generator

from config.settings import DB_PATH
from utils.logger import db_log as logger

# ── Schema version (for future migrations) ───────────────────────────────────
SCHEMA_VERSION = 3


# ── DDL ──────────────────────────────────────────────────────────────────────
_CREATE_ACTIVITY_LOG = """
CREATE TABLE IF NOT EXISTS activity_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Idempotency key: SHA-256(device_id + process_name + window_title + start_time_iso)
    activity_id         TEXT UNIQUE NOT NULL,

    start_time          DATETIME NOT NULL,
    end_time            DATETIME NOT NULL,
    -- duration_seconds is derived on read; NOT stored to avoid source-of-truth split

    process_name        TEXT NOT NULL COLLATE NOCASE,
    process_id          INTEGER,
    executable_path     TEXT,

    window_title        TEXT,
    window_handle       INTEGER,

    device_name         TEXT NOT NULL,
    device_id           TEXT NOT NULL,

    is_idle             INTEGER NOT NULL DEFAULT 0,
    idle_reason         TEXT,

    -- Sync tracking
    sync_status         TEXT NOT NULL DEFAULT 'pending',  -- pending | synced | failed
    sync_batch_id       TEXT,
    sync_time           DATETIME,
    sync_error          TEXT,

    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_modified       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT valid_times CHECK (start_time <= end_time)
);
"""

_CREATE_ACTIVITY_LOG_ARCHIVE = """
CREATE TABLE IF NOT EXISTS activity_log_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id TEXT UNIQUE NOT NULL,
    start_time DATETIME NOT NULL,
    end_time DATETIME NOT NULL,
    process_name TEXT NOT NULL COLLATE NOCASE,
    process_id INTEGER,
    executable_path TEXT,
    window_title TEXT,
    window_handle INTEGER,
    device_name TEXT NOT NULL,
    device_id TEXT NOT NULL,
    is_idle INTEGER NOT NULL DEFAULT 0,
    idle_reason TEXT,
    sync_status TEXT NOT NULL DEFAULT 'pending',
    sync_batch_id TEXT,
    sync_time DATETIME,
    sync_error TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_HEALTH_CHECK = """
CREATE TABLE IF NOT EXISTS health_check (
    check_id    INTEGER PRIMARY KEY,
    last_ping   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    uptime_sec  INTEGER  NOT NULL DEFAULT 0
);
"""

_CREATE_SETTINGS = """
CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
"""

_CREATE_SYNC_LOG = """
CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_time       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    uploaded_rows   INTEGER  NOT NULL DEFAULT 0,
    status          TEXT     NOT NULL,
    batch_id        TEXT,
    error_message   TEXT
);
"""

_CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);
"""

# Quick Win #1 – Indexes
_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_start_time      ON activity_log(start_time DESC);",
    "CREATE INDEX IF NOT EXISTS idx_device_process  ON activity_log(device_name, process_name);",
    "CREATE INDEX IF NOT EXISTS idx_sync_status     ON activity_log(sync_status, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_activity_id     ON activity_log(activity_id);",
    "CREATE INDEX IF NOT EXISTS idx_process_name    ON activity_log(process_name);",
]

# ── Helper ────────────────────────────────────────────────────────────────────

def generate_activity_id(
    device_id: str,
    process_name: str,
    window_title: str,
    start_time: datetime,
) -> str:
    """Deterministic SHA-256 idempotency key (Quick Win #7)."""
    key = f"{device_id}:{process_name.lower()}:{window_title or ''}:{start_time.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()


# ── Database class ────────────────────────────────────────────────────────────

class Database:
    """
    Wraps a SQLite connection.
    - WAL mode for crash safety (Quick Win #3)
    - Row factory for dict-like access
    - Context manager for transactions
    """

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._start_time = datetime.now()
        self._initialize()

    # ── Connection management ─────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=False,
            timeout=30,
        )
        conn.row_factory = sqlite3.Row
        # Quick Win #3 – WAL mode
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA cache_size=-64000;")   # 64 MB page cache
        conn.execute("PRAGMA temp_store=MEMORY;")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Initialisation ────────────────────────────────────────────────────────

    def _initialize(self) -> None:
        self._check_integrity()
        self._create_schema()
        self._run_migrations()
        logger.info(f"Database ready at {self.db_path}")

    def _check_integrity(self) -> None:
        """Quick Win #3 – integrity check on startup."""
        if not self.db_path.exists():
            return
        try:
            conn = self._connect()
            row = conn.execute("PRAGMA integrity_check;").fetchone()
            conn.close()
            if row[0] != "ok":
                logger.error("Database integrity check FAILED; attempting restore.")
                from utils.backup import restore_backup
                if not restore_backup(self.db_path):
                    logger.critical("Could not restore backup; starting fresh.")
                    self.db_path.unlink(missing_ok=True)
            else:
                logger.debug("Database integrity OK.")
        except sqlite3.DatabaseError as exc:
            logger.error(f"Integrity check exception: {exc}; wiping and starting fresh.")
            self.db_path.unlink(missing_ok=True)

    def _create_schema(self) -> None:
        with self.transaction() as c:
            c.execute(_CREATE_SCHEMA_VERSION)
            c.execute(_CREATE_ACTIVITY_LOG)
            c.execute(_CREATE_ACTIVITY_LOG_ARCHIVE)
            c.execute(_CREATE_HEALTH_CHECK)
            c.execute(_CREATE_SETTINGS)
            c.execute(_CREATE_SYNC_LOG)
            for idx in _CREATE_INDEXES:
                c.execute(idx)

    def _run_migrations(self) -> None:
        """Apply any pending schema migrations in order."""
        current = self.conn.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_version"
        ).fetchone()[0]

        migrations = {
            1: ("Initial schema", lambda c: None),
            2: ("Add process_id and window_handle columns", self._migration_v2),
            3: ("Add daily_stats view", self._migration_v3),
        }
        for ver, (desc, fn) in sorted(migrations.items()):
            if ver > current:
                with self.transaction() as c:
                    fn(c)
                    c.execute(
                        "INSERT INTO schema_version(version, description) VALUES (?,?)",
                        (ver, desc),
                    )
                logger.info(f"Migration v{ver} applied: {desc}")

    def _migration_v2(self, conn: sqlite3.Connection) -> None:
        for col, definition in [
            ("process_id", "INTEGER"),
            ("window_handle", "INTEGER"),
        ]:
            try:
                conn.execute(f"ALTER TABLE activity_log ADD COLUMN {col} {definition};")
            except sqlite3.OperationalError:
                pass  # column already exists

    def _migration_v3(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                stat_date       DATE        NOT NULL,
                process_name    TEXT        NOT NULL COLLATE NOCASE,
                device_id       TEXT        NOT NULL,
                total_seconds   INTEGER     NOT NULL DEFAULT 0,
                session_count   INTEGER     NOT NULL DEFAULT 0,
                PRIMARY KEY (stat_date, process_name, device_id)
            );
        """)

    # ── Activity CRUD ─────────────────────────────────────────────────────────

    def insert_activity(self, activity: dict) -> bool:
        """
        Insert a new activity record. Returns False if it's a duplicate.
        Quick Win #7 – duplicate detection via activity_id.
        """
        activity_id = activity.get("activity_id")
        if not activity_id:
            activity_id = generate_activity_id(
                activity["device_id"],
                activity["process_name"],
                activity.get("window_title", ""),
                activity["start_time"],
            )
            activity["activity_id"] = activity_id

        try:
            with self.transaction() as c:
                c.execute(
                    """
                    INSERT OR IGNORE INTO activity_log
                        (activity_id, start_time, end_time, process_name, process_id,
                         executable_path, window_title, window_handle,
                         device_name, device_id, is_idle, idle_reason)
                    VALUES
                        (:activity_id, :start_time, :end_time, :process_name, :process_id,
                         :executable_path, :window_title, :window_handle,
                         :device_name, :device_id, :is_idle, :idle_reason)
                    """,
                    {
                        "activity_id": activity_id,
                        "start_time": activity["start_time"],
                        "end_time": activity["end_time"],
                        "process_name": activity["process_name"],
                        "process_id": activity.get("process_id"),
                        "executable_path": activity.get("executable_path", ""),
                        "window_title": activity.get("window_title", ""),
                        "window_handle": activity.get("window_handle"),
                        "device_name": activity["device_name"],
                        "device_id": activity["device_id"],
                        "is_idle": int(activity.get("is_idle", False)),
                        "idle_reason": activity.get("idle_reason"),
                    },
                )
            return True
        except sqlite3.IntegrityError:
            logger.debug(f"Duplicate activity skipped: {activity_id[:16]}…")
            return False

    def get_activities(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        process_name: str | None = None,
        include_idle: bool = True,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict]:
        where_clauses = []
        params: list = []

        if start_date:
            where_clauses.append("start_time >= ?")
            params.append(start_date)
        if end_date:
            where_clauses.append("end_time <= ?")
            params.append(end_date)
        if process_name:
            where_clauses.append("process_name = ?")
            params.append(process_name)
        if not include_idle:
            where_clauses.append("is_idle = 0")

        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        sql = f"""
            SELECT *,
                   CAST((julianday(end_time) - julianday(start_time)) * 86400 AS INTEGER)
                   AS duration_seconds
            FROM activity_log
            {where}
            ORDER BY start_time DESC
            LIMIT ? OFFSET ?
        """
        rows = self.conn.execute(sql, [*params, limit, offset]).fetchall()
        return [dict(r) for r in rows]

    def get_pending_sync(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT *,
                   CAST((julianday(end_time) - julianday(start_time)) * 86400 AS INTEGER)
                   AS duration_seconds
            FROM activity_log
            WHERE sync_status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_synced(self, activity_ids: list[str], batch_id: str) -> None:
        with self.transaction() as c:
            c.executemany(
                """
                UPDATE activity_log
                SET sync_status   = 'synced',
                    sync_batch_id = ?,
                    sync_time     = CURRENT_TIMESTAMP,
                    last_modified = CURRENT_TIMESTAMP
                WHERE activity_id = ?
                """,
                [(batch_id, aid) for aid in activity_ids],
            )

    def mark_sync_failed(self, activity_ids: list[str], error: str) -> None:
        with self.transaction() as c:
            c.executemany(
                """
                UPDATE activity_log
                SET sync_status   = 'failed',
                    sync_error    = ?,
                    last_modified = CURRENT_TIMESTAMP
                WHERE activity_id = ?
                """,
                [(error, aid) for aid in activity_ids],
            )

    # ── Statistics ────────────────────────────────────────────────────────────

    def get_top_apps(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 10,
    ) -> list[dict]:
        where = ""
        params: list = []
        conditions = []
        if start_date:
            conditions.append("start_time >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("end_time <= ?")
            params.append(end_date)
        conditions.append("is_idle = 0")
        where = "WHERE " + " AND ".join(conditions)

        rows = self.conn.execute(
            f"""
            SELECT process_name,
                   SUM(CAST((julianday(end_time)-julianday(start_time))*86400 AS INTEGER))
                     AS total_seconds,
                   COUNT(*) AS session_count
            FROM activity_log
            {where}
            GROUP BY process_name
            ORDER BY total_seconds DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def get_daily_totals(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT DATE(start_time) AS activity_date,
                   SUM(CAST((julianday(end_time)-julianday(start_time))*86400 AS INTEGER))
                     AS total_seconds
            FROM activity_log
            WHERE start_time >= ?
              AND end_time   <= ?
              AND is_idle    = 0
            GROUP BY activity_date
            ORDER BY activity_date
            """,
            (start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_hourly_heatmap(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT CAST(strftime('%H', start_time) AS INTEGER) AS hour,
                   SUM(CAST((julianday(end_time)-julianday(start_time))*86400 AS INTEGER))
                     AS total_seconds
            FROM activity_log
            WHERE start_time >= ?
              AND end_time   <= ?
              AND is_idle    = 0
            GROUP BY hour
            ORDER BY hour
            """,
            (start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Health check ──────────────────────────────────────────────────────────

    def ping_health(self) -> None:
        """Update heartbeat timestamp (Quick Win #5)."""
        uptime = int((datetime.now() - self._start_time).total_seconds())
        with self.transaction() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO health_check(check_id, last_ping, uptime_sec)
                VALUES (1, CURRENT_TIMESTAMP, ?)
                """,
                (uptime,),
            )

    def is_tracker_alive(self, max_seconds: int = 120) -> bool:
        """Return True if tracker pinged within max_seconds (Quick Win #5)."""
        row = self.conn.execute(
            """
            SELECT (julianday('now') - julianday(last_ping)) * 86400 AS seconds_ago
            FROM health_check WHERE check_id = 1
            """
        ).fetchone()
        return bool(row and row[0] < max_seconds)

    def get_health(self) -> dict:
        row = self.conn.execute(
            "SELECT last_ping, uptime_sec FROM health_check WHERE check_id = 1"
        ).fetchone()
        size_mb = self.db_path.stat().st_size / (1024 * 1024) if self.db_path.exists() else 0
        total = self.conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
        pending = self.conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE sync_status='pending'"
        ).fetchone()[0]
        return {
            "last_ping": row["last_ping"] if row else None,
            "uptime_sec": row["uptime_sec"] if row else 0,
            "db_size_mb": round(size_mb, 2),
            "total_records": total,
            "pending_sync": pending,
            "alive": self.is_tracker_alive(),
        }

    # ── Data retention / archival (Quick Win #8) ─────────────────────────────

    def archive_old_data(self, retention_days: int = 90) -> int:
        """
        Move records older than retention_days from activity_log to
        activity_log_archive. Returns the number of rows moved.
        """
        cutoff = datetime.now() - timedelta(days=retention_days)
        with self.transaction() as c:
            c.execute(
                """
                INSERT OR IGNORE INTO activity_log_archive
                SELECT id, activity_id, start_time, end_time, process_name,
                       process_id, executable_path, window_title, window_handle,
                       device_name, device_id, is_idle, idle_reason,
                       sync_status, sync_batch_id, sync_time, sync_error,
                       created_at, last_modified
                FROM activity_log
                WHERE start_time < ?
                """,
                (cutoff,),
            )
            rows_moved = c.execute(
                "SELECT changes()"
            ).fetchone()[0]
            c.execute(
                "DELETE FROM activity_log WHERE start_time < ?",
                (cutoff,),
            )
        logger.info(f"Archived {rows_moved} records older than {cutoff.date()}")
        return rows_moved

    # ── Sync log ──────────────────────────────────────────────────────────────

    def log_sync_result(
        self,
        uploaded_rows: int,
        status: str,
        batch_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.transaction() as c:
            c.execute(
                """
                INSERT INTO sync_log(uploaded_rows, status, batch_id, error_message)
                VALUES (?, ?, ?, ?)
                """,
                (uploaded_rows, status, batch_id, error),
            )

    def get_sync_log(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM sync_log
            ORDER BY sync_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# Module-level singleton
_db_instance: Database | None = None


def get_db() -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance
