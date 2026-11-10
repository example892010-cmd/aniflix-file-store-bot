import unittest
from pathlib import Path
from types import SimpleNamespace

from helper.upload_order import ordered_archive_message_ids, sort_upload_messages

ROOT = Path(__file__).resolve().parents[1]


class UploadOrderTests(unittest.TestCase):
    def test_forwarded_messages_are_sorted_by_private_chat_message_id(self):
        messages = [
            SimpleNamespace(id=44),
            SimpleNamespace(id=41),
            SimpleNamespace(id=43),
            SimpleNamespace(id=42),
        ]
        self.assertEqual(
            [message.id for message in sort_upload_messages(messages)],
            [41, 42, 43, 44],
        )

    def test_empty_and_duplicate_message_updates_are_ignored(self):
        first = SimpleNamespace(id=10)
        duplicate = SimpleNamespace(id=10)
        messages = [None, SimpleNamespace(id=0), SimpleNamespace(id=9, empty=True), first, duplicate]
        result = sort_upload_messages(messages)
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], duplicate)

    def test_archive_ids_follow_original_source_message_order(self):
        archived = {
            104: 9004,
            101: 9001,
            103: 9003,
            102: 9002,
        }
        self.assertEqual(
            ordered_archive_message_ids(archived),
            [9001, 9002, 9003, 9004],
        )

    def test_non_positive_archive_id_is_rejected(self):
        with self.assertRaises(ValueError):
            ordered_archive_message_ids({1: 0})

    def test_getlink_flow_buffers_then_archives_in_sorted_order(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn('source_messages[source_message_id] = message', source)
        self.assertIn('sort_upload_messages(session.get("source_messages", {}).values())', source)
        self.assertIn('for source_message in source_messages:', source)
        self.assertIn('await bot.file_storage.store_message(source_message)', source)
        self.assertIn('ordered_archive_message_ids(archived_by_source_id)', source)


if __name__ == "__main__":
    unittest.main()
