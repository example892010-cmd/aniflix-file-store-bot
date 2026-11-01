import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class PyrogramStorageSeparationTests(unittest.TestCase):
    def test_archive_storage_never_overwrites_client_storage(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn(
            "self.file_storage = Storage(self, self.archive_channel_id)", source
        )
        self.assertNotIn(
            "self.storage = Storage(self, self.archive_channel_id)", source
        )
        self.assertIn("bot.file_storage.store_message", source)
        self.assertIn("bot.file_storage.deliver_message", source)

    def test_batch_message_normalization_sorts_by_original_message_id(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_normalize_messages"
        )
        isolated = ast.Module(body=[function], type_ignores=[])
        namespace = {}
        exec(compile(isolated, str(ROOT / "bot.py"), "exec"), namespace)
        messages = [
            SimpleNamespace(id=9),
            None,
            SimpleNamespace(id=2),
            SimpleNamespace(id=1, empty=True),
        ]
        result = namespace["_normalize_messages"](messages)
        self.assertEqual([item.id for item in result], [2, 9])


if __name__ == "__main__":
    unittest.main()
