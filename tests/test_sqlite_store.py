import os
import sqlite3
import tempfile
import unittest

from helper.sqlite_store import SQLiteStore


class SQLiteStoreTests(unittest.TestCase):
    def test_snapshot_round_trip_and_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "aniflix.sqlite3")
            db = SQLiteStore(path)
            db.save_state({"users": [10, 20], "welcome_message": "Hello"}, 7)
            data, revision = db.load_state()
            self.assertEqual(data["users"], [10, 20])
            self.assertEqual(revision, 7)
            db.close()

    def test_file_records_are_idempotent_for_source_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = SQLiteStore(os.path.join(tmp, "aniflix.sqlite3"))
            values = dict(
                source_chat_id=100, source_message_id=5,
                archive_chat_id=-100123, archive_message_id=501,
                media_type="document", file_unique_id="uniq", file_name="x.pdf",
                file_size=123, caption="doc", uploader_id=100,
            )
            db.record_file(**values)
            db.record_file(**{**values, "archive_message_id": 502})
            record = db.find_file(100, 5)
            self.assertEqual(record["archive_message_id"], 502)
            db.close()

    def test_known_existing_state_schema_is_backed_up_and_extended(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "legacy.sqlite3")
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE state_snapshot(singleton_id INTEGER PRIMARY KEY, "
                "revision INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO state_snapshot VALUES(1, 3, ?, ?)",
                ('{"users":[11]}', "old"),
            )
            conn.commit()
            conn.close()
            db = SQLiteStore(path)
            backup = path + ".pre-migration.bak"
            self.assertTrue(os.path.exists(backup))
            data, revision = db.load_state()
            self.assertEqual(data["users"], [11])
            self.assertEqual(revision, 3)
            tables = {
                row[0] for row in db._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("file_records", tables)
            self.assertIn("link_records", tables)
            db.close()

    def test_unrecognized_database_is_backed_up_and_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "foreign.sqlite3")
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE unrelated(value TEXT)")
            conn.execute("INSERT INTO unrelated VALUES('keep-me')")
            conn.commit()
            conn.close()
            with self.assertRaisesRegex(RuntimeError, "unrecognized schema"):
                SQLiteStore(path)
            self.assertTrue(os.path.exists(path + ".pre-migration.bak"))
            conn = sqlite3.connect(path)
            self.assertEqual(
                conn.execute("SELECT value FROM unrelated").fetchone()[0],
                "keep-me",
            )
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name='file_records'"
            ).fetchone())
            conn.close()

    def test_unknown_future_schema_is_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "future.sqlite3")
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA user_version=99")
            conn.commit()
            conn.close()
            with self.assertRaisesRegex(RuntimeError, "newer than this bot supports"):
                SQLiteStore(path)


if __name__ == "__main__":
    unittest.main()
