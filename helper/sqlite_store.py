
"""SQLite persistence for ANIFLIX.

All persistent bot data is stored in the SQLite database.
Existing state_snapshot, file_records and link_records tables are preserved.

Schema version 2 adds:
- Persistent settings
- Scheduled message deletions
- Batch delivery and cancellation records

No MongoDB, Redis, JSON files or separate database backups are created.
JSON serialization is used only inside SQLite's TEXT columns.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
DEFAULT_AUTO_DELETE_MINUTES = 30
MIN_AUTO_DELETE_MINUTES = 30
MAX_AUTO_DELETE_MINUTES = 1440
AUTO_DELETE_STEP_MINUTES = 30


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteStore:
    def __init__(self, path: str = "data/aniflix.sqlite3"):
        self.path = str(path or "data/aniflix.sqlite3")

        # Create only the parent directory required for the SQLite database.
        Path(self.path).expanduser().resolve().parent.mkdir(
            parents=True, exist_ok=True
        )

        self._lock = threading.RLock()
        self._connection = self._connect()

        try:
            self._migrate()
        except Exception:
            self._connection.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _migrate(self) -> None:
        """Create new tables without deleting or rebuilding existing tables."""
        with self._lock:
            integrity = self._connection.execute(
                "PRAGMA quick_check"
            ).fetchone()[0]

            if integrity != "ok":
                raise sqlite3.DatabaseError(
                    f"SQLite integrity check failed: {integrity}"
                )

            current_version = int(
                self._connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
            )

            if current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema {current_version} is newer than "
                    f"this bot supports ({SCHEMA_VERSION})."
                )

            existing_tables = {
                row[0]
                for row in self._connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    """
                )
            }

            known_tables = {
                "state_snapshot",
                "file_records",
                "link_records",
            }

            user_tables = existing_tables - {"sqlite_sequence"}

            # Do not silently initialize an unrelated or unrecognized database.
            if user_tables and not (user_tables & known_tables):
                raise RuntimeError(
                    "The SQLite database contains an unrecognized schema. "
                    "No existing tables or records were changed."
                )

            expected_columns = {
                "state_snapshot": {
                    "singleton_id",
                    "revision",
                    "payload",
                    "updated_at",
                },
                "file_records": {
                    "id",
                    "source_chat_id",
                    "source_message_id",
                    "archive_chat_id",
                    "archive_message_id",
                    "media_type",
                    "file_unique_id",
                    "file_name",
                    "file_size",
                    "caption",
                    "uploader_id",
                    "created_at",
                },
                "link_records": {
                    "id",
                    "payload",
                    "link_type",
                    "archive_message_id",
                    "created_at",
                },
            }

            for table_name in user_tables & known_tables:
                columns = {
                    row[1]
                    for row in self._connection.execute(
                        f"PRAGMA table_info({table_name})"
                    )
                }

                if not expected_columns[table_name].issubset(columns):
                    raise RuntimeError(
                        f"Existing SQLite table {table_name!r} "
                        "has an incompatible schema. "
                        "No existing data was changed."
                    )

            # Additive migration. Existing tables and records are untouched.
            migration_sql = """
            BEGIN IMMEDIATE;

            CREATE TABLE IF NOT EXISTS state_snapshot (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                revision INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS file_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_chat_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                archive_chat_id INTEGER NOT NULL,
                archive_message_id INTEGER NOT NULL UNIQUE,
                media_type TEXT,
                file_unique_id TEXT,
                file_name TEXT,
                file_size INTEGER NOT NULL DEFAULT 0,
                caption TEXT,
                uploader_id INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(source_chat_id, source_message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_file_records_unique
                ON file_records(file_unique_id);

            CREATE TABLE IF NOT EXISTS link_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL UNIQUE,
                link_type TEXT NOT NULL,
                archive_message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scheduled_deletions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                delete_at REAL NOT NULL,
                next_attempt_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_scheduled_deletions_due
                ON scheduled_deletions(next_attempt_at, delete_at);

            CREATE TABLE IF NOT EXISTS batch_deliveries (
                delivery_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                manifest_id INTEGER,
                status TEXT NOT NULL DEFAULT 'running',
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                sent_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_batch_deliveries_user
                ON batch_deliveries(user_id, status);

            PRAGMA user_version = 2;

            COMMIT;
            """

            try:
                self._connection.executescript(migration_sql)
            except Exception:
                try:
                    self._connection.rollback()
                except Exception:
                    pass
                raise

            logger.info(
                "ANIFLIX SQLite schema is ready (version %s).",
                SCHEMA_VERSION,
            )

    # ---------------------------------------------------------
    # EXISTING STATE SNAPSHOT METHODS
    # ---------------------------------------------------------

    def load_state(self) -> Optional[tuple[dict[str, Any], int]]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT payload, revision
                FROM state_snapshot
                WHERE singleton_id = 1
                """
            ).fetchone()

            if not row:
                return None

            data = json.loads(row["payload"])

            if not isinstance(data, dict):
                raise ValueError(
                    "SQLite state snapshot is not a JSON object."
                )

            return data, int(row["revision"])

    def save_state(
        self,
        state: dict[str, Any],
        revision: int,
    ) -> None:
        payload = json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        now = _utc_now()

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO state_snapshot(
                    singleton_id,
                    revision,
                    payload,
                    updated_at
                )
                VALUES(1, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    revision = excluded.revision,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (int(revision), payload, now),
            )

    # ---------------------------------------------------------
    # EXISTING FILE RECORD METHODS
    # ---------------------------------------------------------

    def find_file(
        self,
        source_chat_id: int,
        source_message_id: int,
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT *
                FROM file_records
                WHERE source_chat_id = ?
                  AND source_message_id = ?
                """,
                (int(source_chat_id), int(source_message_id)),
            ).fetchone()

            return dict(row) if row else None

    def record_file(
        self,
        *,
        source_chat_id: int,
        source_message_id: int,
        archive_chat_id: int,
        archive_message_id: int,
        media_type: str = "",
        file_unique_id: str = "",
        file_name: str = "",
        file_size: int = 0,
        caption: str = "",
        uploader_id: Optional[int] = None,
    ) -> None:
        now = _utc_now()

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO file_records(
                    source_chat_id,
                    source_message_id,
                    archive_chat_id,
                    archive_message_id,
                    media_type,
                    file_unique_id,
                    file_name,
                    file_size,
                    caption,
                    uploader_id,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_chat_id, source_message_id)
                DO UPDATE SET
                    archive_chat_id = excluded.archive_chat_id,
                    archive_message_id = excluded.archive_message_id,
                    media_type = excluded.media_type,
                    file_unique_id = excluded.file_unique_id,
                    file_name = excluded.file_name,
                    file_size = excluded.file_size,
                    caption = excluded.caption,
                    uploader_id = excluded.uploader_id
                """,
                (
                    int(source_chat_id),
                    int(source_message_id),
                    int(archive_chat_id),
                    int(archive_message_id),
                    str(media_type or ""),
                    str(file_unique_id or ""),
                    str(file_name or ""),
                    max(0, int(file_size or 0)),
                    str(caption or ""),
                    int(uploader_id) if uploader_id else None,
                    now,
                ),
            )

    # ---------------------------------------------------------
    # EXISTING LINK RECORD METHODS
    # ---------------------------------------------------------

    def record_link(
        self,
        payload: str,
        link_type: str,
        archive_message_id: int,
    ) -> None:
        now = _utc_now()

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO link_records(
                    payload,
                    link_type,
                    archive_message_id,
                    created_at
                )
                VALUES(?, ?, ?, ?)
                """,
                (
                    str(payload),
                    str(link_type),
                    int(archive_message_id),
                    now,
                ),
            )

    # ---------------------------------------------------------
    # BOT SETTINGS
    # ---------------------------------------------------------

    def set_setting(self, key: str, value: Any) -> None:
        """Store a setting inside SQLite."""
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO bot_settings(
                    setting_key,
                    setting_value,
                    updated_at
                )
                VALUES(?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (str(key), encoded, _utc_now()),
            )

    def get_setting(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT setting_value
                FROM bot_settings
                WHERE setting_key = ?
                """,
                (str(key),),
            ).fetchone()

            if not row:
                return default

            try:
                return json.loads(row["setting_value"])
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning(
                    "Invalid SQLite setting value for %s.",
                    key,
                )
                return default

    def get_auto_delete_minutes(self) -> int:
        value = self.get_setting(
            "auto_delete_minutes",
            DEFAULT_AUTO_DELETE_MINUTES,
        )

        try:
            value = int(value)
        except (TypeError, ValueError):
            value = DEFAULT_AUTO_DELETE_MINUTES

        value = max(
            MIN_AUTO_DELETE_MINUTES,
            min(MAX_AUTO_DELETE_MINUTES, value),
        )

        # Keep the value aligned to 30-minute increments.
        value = (
            round(value / AUTO_DELETE_STEP_MINUTES)
            * AUTO_DELETE_STEP_MINUTES
        )

        return max(
            MIN_AUTO_DELETE_MINUTES,
            min(MAX_AUTO_DELETE_MINUTES, value),
        )

    def set_auto_delete_minutes(self, minutes: int) -> int:
        minutes = int(minutes)

        if not (
            MIN_AUTO_DELETE_MINUTES
            <= minutes
            <= MAX_AUTO_DELETE_MINUTES
        ):
            raise ValueError(
                "Auto-delete timer must be between 30 and 1440 minutes."
            )

        if minutes % AUTO_DELETE_STEP_MINUTES != 0:
            raise ValueError(
                "Auto-delete timer must use 30-minute increments."
            )

        self.set_setting("auto_delete_minutes", minutes)
        return minutes

    # ---------------------------------------------------------
    # SCHEDULED MESSAGE DELETIONS
    # ---------------------------------------------------------

    def schedule_message_deletion(
        self,
        chat_id: int,
        message_id: int,
        delete_at: float,
    ) -> int:
        """Add or update a Telegram message deletion job."""
        now = _utc_now()

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO scheduled_deletions(
                    chat_id,
                    message_id,
                    delete_at,
                    next_attempt_at,
                    attempts,
                    last_error,
                    created_at
                )
                VALUES(?, ?, ?, ?, 0, NULL, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    delete_at = excluded.delete_at,
                    next_attempt_at = excluded.next_attempt_at,
                    attempts = 0,
                    last_error = NULL
                """,
                (
                    int(chat_id),
                    int(message_id),
                    float(delete_at),
                    float(delete_at),
                    now,
                ),
            )

            row = self._connection.execute(
                """
                SELECT id
                FROM scheduled_deletions
                WHERE chat_id = ? AND message_id = ?
                """,
                (int(chat_id), int(message_id)),
            ).fetchone()

            return int(row["id"])

    def get_due_deletions(
        self,
        now: Optional[float] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return deletion jobs whose retry time and deletion time have arrived."""
        import time

        current_time = float(now if now is not None else time.time())
        limit = max(1, min(int(limit), 1000))

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT *
                FROM scheduled_deletions
                WHERE delete_at <= ?
                  AND next_attempt_at <= ?
                ORDER BY delete_at ASC, id ASC
                LIMIT ?
                """,
                (current_time, current_time, limit),
            ).fetchall()

            return [dict(row) for row in rows]

    def complete_scheduled_deletion(self, job_id: int) -> None:
        """Remove a job only after Telegram confirms deletion."""
        with self._lock, self._connection:
            self._connection.execute(
                """
                DELETE FROM scheduled_deletions
                WHERE id = ?
                """,
                (int(job_id),),
            )

    def retry_scheduled_deletion(
        self,
        job_id: int,
        error: str,
        next_attempt_at: float,
    ) -> None:
        """Keep a failed job in SQLite for another attempt."""
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE scheduled_deletions
                SET attempts = attempts + 1,
                    last_error = ?,
                    next_attempt_at = ?
                WHERE id = ?
                """,
                (
                    str(error or "")[:1000],
                    float(next_attempt_at),
                    int(job_id),
                ),
            )

    def count_scheduled_deletions(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM scheduled_deletions"
            ).fetchone()

            return int(row["total"] if row else 0)

    # ---------------------------------------------------------
    # BATCH DELIVERY AND CANCELLATION
    # ---------------------------------------------------------

    def start_batch_delivery(
        self,
        delivery_id: str,
        user_id: int,
        chat_id: int,
        manifest_id: Optional[int] = None,
    ) -> None:
        now = _utc_now()

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO batch_deliveries(
                    delivery_id,
                    user_id,
                    chat_id,
                    manifest_id,
                    status,
                    cancel_requested,
                    sent_count,
                    failed_count,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, 'running', 0, 0, 0, ?, ?)
                ON CONFLICT(delivery_id) DO UPDATE SET
                    status = 'running',
                    cancel_requested = 0,
                    updated_at = excluded.updated_at
                """,
                (
                    str(delivery_id),
                    int(user_id),
                    int(chat_id),
                    int(manifest_id) if manifest_id is not None else None,
                    now,
                    now,
                ),
            )

    def get_batch_delivery(
        self,
        delivery_id: str,
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT *
                FROM batch_deliveries
                WHERE delivery_id = ?
                """,
                (str(delivery_id),),
            ).fetchone()

            return dict(row) if row else None

    def request_batch_cancel(
        self,
        delivery_id: str,
        user_id: Optional[int] = None,
    ) -> bool:
        """Mark a batch as cancelled without deleting any delivered messages."""
        now = _utc_now()

        with self._lock, self._connection:
            if user_id is None:
                cursor = self._connection.execute(
                    """
                    UPDATE batch_deliveries
                    SET cancel_requested = 1,
                        updated_at = ?
                    WHERE delivery_id = ?
                      AND status = 'running'
                    """,
                    (now, str(delivery_id)),
                )
            else:
                cursor = self._connection.execute(
                    """
                    UPDATE batch_deliveries
                    SET cancel_requested = 1,
                        updated_at = ?
                    WHERE delivery_id = ?
                      AND user_id = ?
                      AND status = 'running'
                    """,
                    (now, str(delivery_id), int(user_id)),
                )

            return cursor.rowcount > 0

    def is_batch_cancel_requested(
        self,
        delivery_id: str,
    ) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT cancel_requested
                FROM batch_deliveries
                WHERE delivery_id = ?
                """,
                (str(delivery_id),),
            ).fetchone()

            return bool(row["cancel_requested"]) if row else False

    def update_batch_delivery(
        self,
        delivery_id: str,
        *,
        sent_count: Optional[int] = None,
        failed_count: Optional[int] = None,
        status: Optional[str] = None,
    ) -> None:
        allowed_statuses = {
            "running",
            "completed",
            "cancelled",
            "failed",
            "interrupted",
        }

        if status is not None and status not in allowed_statuses:
            raise ValueError("Invalid batch delivery status.")

        updates = ["updated_at = ?"]
        values: list[Any] = [_utc_now()]

        if sent_count is not None:
            updates.append("sent_count = ?")
            values.append(max(0, int(sent_count)))

        if failed_count is not None:
            updates.append("failed_count = ?")
            values.append(max(0, int(failed_count)))

        if status is not None:
            updates.append("status = ?")
            values.append(status)

        values.append(str(delivery_id))

        with self._lock, self._connection:
            self._connection.execute(
                f"""
                UPDATE batch_deliveries
                SET {", ".join(updates)}
                WHERE delivery_id = ?
                """,
                values,
            )

    # ---------------------------------------------------------
    # DATABASE LIFECYCLE
    # ---------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._connection.close()
