"""SQLite persistence for ANIFLIX.

The Telegram archive channel remains the file store and legacy state recovery source.
SQLite stores a versioned state snapshot plus verified archive metadata. Existing
archive messages and deep-link IDs are never modified by this module.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1


class SQLiteStore:
    def __init__(self, path: str = "data/aniflix.sqlite3"):
        self.path = str(path or "data/aniflix.sqlite3")
        Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = self._connect()
        try:
            self._migrate()
        except Exception:
            self._connection.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=20, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 20000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _migrate(self) -> None:
        with self._lock:
            integrity = self._connection.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError(f"SQLite integrity check failed: {integrity}")
            current = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema {current} is newer than this bot supports ({SCHEMA_VERSION})."
                )
            existing_file = os.path.exists(self.path) and os.path.getsize(self.path) > 0
            if existing_file and current < SCHEMA_VERSION:
                backup_path = self.path + ".pre-migration.bak"
                if not os.path.exists(backup_path):
                    backup = sqlite3.connect(backup_path)
                    try:
                        self._connection.backup(backup)
                    finally:
                        backup.close()
                    logger.info("Created pre-migration SQLite backup.")

            existing_tables = {
                row[0] for row in self._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            known_tables = {"state_snapshot", "file_records", "link_records"}
            user_tables = existing_tables - {"sqlite_sequence"}
            if user_tables and not (user_tables & known_tables):
                backup_path = self.path + ".pre-migration.bak"
                if existing_file and not os.path.exists(backup_path):
                    backup = sqlite3.connect(backup_path)
                    try:
                        self._connection.backup(backup)
                    finally:
                        backup.close()
                raise RuntimeError(
                    "Existing SQLite file has an unrecognized schema. A backup was created; "
                    "refusing to modify it until the database is inspected."
                )

            expected_columns = {
                "state_snapshot": {"singleton_id", "revision", "payload", "updated_at"},
                "file_records": {
                    "id", "source_chat_id", "source_message_id", "archive_chat_id",
                    "archive_message_id", "media_type", "file_unique_id", "file_name",
                    "file_size", "caption", "uploader_id", "created_at",
                },
                "link_records": {"id", "payload", "link_type", "archive_message_id", "created_at"},
            }
            for table_name in user_tables & known_tables:
                columns = {
                    row[1] for row in self._connection.execute(
                        f"PRAGMA table_info({table_name})"
                    )
                }
                if not expected_columns[table_name].issubset(columns):
                    backup_path = self.path + ".pre-migration.bak"
                    if existing_file and not os.path.exists(backup_path):
                        backup = sqlite3.connect(backup_path)
                        try:
                            self._connection.backup(backup)
                        finally:
                            backup.close()
                    raise RuntimeError(
                        f"Existing SQLite table {table_name!r} has an incompatible schema. "
                        "A backup was created; refusing to modify it."
                    )

            self._connection.executescript(
                """
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
                """
            )
            self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._connection.commit()

    def load_state(self) -> Optional[tuple[dict[str, Any], int]]:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload, revision FROM state_snapshot WHERE singleton_id = 1"
            ).fetchone()
            if not row:
                return None
            data = json.loads(row["payload"])
            if not isinstance(data, dict):
                raise ValueError("SQLite state snapshot is not a JSON object")
            return data, int(row["revision"])

    def save_state(self, state: dict[str, Any], revision: int) -> None:
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO state_snapshot(singleton_id, revision, payload, updated_at)
                VALUES(1, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    revision=excluded.revision,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (int(revision), payload, now),
            )

    def find_file(self, source_chat_id: int, source_message_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM file_records
                   WHERE source_chat_id=? AND source_message_id=?""",
                (int(source_chat_id), int(source_message_id)),
            ).fetchone()
            return dict(row) if row else None

    def record_file(
        self, *, source_chat_id: int, source_message_id: int,
        archive_chat_id: int, archive_message_id: int,
        media_type: str = "", file_unique_id: str = "", file_name: str = "",
        file_size: int = 0, caption: str = "", uploader_id: Optional[int] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO file_records(
                    source_chat_id, source_message_id, archive_chat_id,
                    archive_message_id, media_type, file_unique_id, file_name,
                    file_size, caption, uploader_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_chat_id, source_message_id) DO UPDATE SET
                    archive_chat_id=excluded.archive_chat_id,
                    archive_message_id=excluded.archive_message_id,
                    media_type=excluded.media_type,
                    file_unique_id=excluded.file_unique_id,
                    file_name=excluded.file_name,
                    file_size=excluded.file_size,
                    caption=excluded.caption,
                    uploader_id=excluded.uploader_id
                """,
                (
                    int(source_chat_id), int(source_message_id), int(archive_chat_id),
                    int(archive_message_id), str(media_type or ""), str(file_unique_id or ""),
                    str(file_name or ""), max(0, int(file_size or 0)), str(caption or ""),
                    int(uploader_id) if uploader_id else None, now,
                ),
            )

    def record_link(self, payload: str, link_type: str, archive_message_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT OR IGNORE INTO link_records(payload, link_type, archive_message_id, created_at)
                   VALUES(?, ?, ?, ?)""",
                (str(payload), str(link_type), int(archive_message_id), now),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
