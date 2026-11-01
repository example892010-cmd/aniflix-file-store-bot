"""Regression checks for Pyrogram's reserved Client.storage attribute."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PyrogramStorageSeparationTests(unittest.TestCase):
    def test_archive_storage_never_overwrites_client_storage(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                        if target.value.id == "self" and target.attr == "storage":
                            assignments.append(target.lineno)
        self.assertEqual(assignments, [], "Do not overwrite Pyrogram Client.storage")
        self.assertIn("self.file_storage = Storage(self, self.archive_channel_id)", source)
        self.assertIn("bot.file_storage.store_message", source)
        self.assertIn("bot.file_storage.deliver_message", source)

    def test_batch_message_normalization_sorts_by_original_message_id(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("return sorted(messages, key=lambda item: int(item.id))", source)


if __name__ == "__main__":
    unittest.main()
