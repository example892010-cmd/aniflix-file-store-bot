"""Regression tests for fully removing admin-removed FSUB channels."""
import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BOT_SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
STATE_SOURCE = (ROOT / "helper" / "state_store.py").read_text(encoding="utf-8")


class FsubRemovalTests(unittest.TestCase):
    def test_empty_saved_fsub_list_does_not_restore_config_channels(self):
        tree = ast.parse(BOT_SOURCE)
        check = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "check_fsub")
        source = ast.get_source_segment(BOT_SOURCE, check)
        self.assertIn("bot.state_store.get_fsub_channels()", source)
        self.assertNotIn("_get_fsub_channels", source)
        self.assertNotIn("if not fsub_channels", source)

    def test_remove_channel_clears_config_requests_and_stats(self):
        tree = ast.parse(STATE_SOURCE)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "StateStore")
        method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "remove_fsub_channel")
        namespace = {}
        module = ast.Module(body=[method], type_ignores=[])
        exec(compile(module, "<remove-fsub-channel>", "exec"), namespace)
        remove = namespace["remove_fsub_channel"]

        class FakeStore:
            def __init__(self):
                self.state = {
                    "fsub_channels": [{"id": -1001}, {"id": -1002}],
                    "fsub_join_requests": {"-1001": [10, 11], "-1002": [12]},
                    "fsub_stats": {"-1001": {"requests": [10]}, "-1002": {"joined": [12]}},
                }
                self.saved = False

            async def save_state(self):
                self.saved = True

        store = FakeStore()
        asyncio.run(remove(store, -1001))
        self.assertEqual(store.state["fsub_channels"], [{"id": -1002}])
        self.assertNotIn("-1001", store.state["fsub_join_requests"])
        self.assertNotIn("-1001", store.state["fsub_stats"])
        self.assertIn("-1002", store.state["fsub_join_requests"])
        self.assertTrue(store.saved)

    def test_remove_handler_calls_full_removal_method(self):
        start = BOT_SOURCE.index('if action == "fsub_remove":')
        end = BOT_SOURCE.index("\n    if message.text", start)
        removal_block = BOT_SOURCE[start:end]
        self.assertIn("await bot.state_store.remove_fsub_channel", removal_block)
        self.assertNotIn("await bot.state_store.set_fsub_channels(channels)", removal_block)


if __name__ == "__main__":
    unittest.main()
