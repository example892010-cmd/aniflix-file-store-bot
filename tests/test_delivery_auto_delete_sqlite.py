import time

from helper.sqlite_store import SQLiteStore


def test_scheduled_delivery_deletion_survives_database_reopen(tmp_path):
    database_path = tmp_path / "aniflix.sqlite3"
    store = SQLiteStore(str(database_path))
    deadline = time.time() + 1800
    store.schedule_message_deletion(-100123, 456, deadline)
    assert store.get_due_message_deletions(deadline - 1) == []
    store.close()

    reopened = SQLiteStore(str(database_path))
    due = reopened.get_due_message_deletions(deadline)
    assert due == [{"chat_id": -100123, "message_id": 456, "delete_at": deadline}]
    reopened.remove_scheduled_message_deletion(-100123, 456)
    assert reopened.get_due_message_deletions(deadline) == []
    reopened.close()


def test_scheduled_delivery_deletion_updates_existing_deadline(tmp_path):
    store = SQLiteStore(str(tmp_path / "aniflix.sqlite3"))
    store.schedule_message_deletion(123, 10, 1000)
    store.schedule_message_deletion(123, 10, 2000)
    due = store.get_due_message_deletions(2000)
    assert len(due) == 1
    assert due[0]["delete_at"] == 2000
    store.close()
