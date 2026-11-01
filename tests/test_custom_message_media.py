"""Regression checks for photo+caption welcome and FSUB messages."""
import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def get_node(name):
    return next(
        node for node in TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


class CustomMessageMediaTests(unittest.TestCase):
    def _payload_helper(self):
        node = get_node("_admin_message_payload")
        namespace = {"Message": object}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<payload>", "exec"), namespace)
        return namespace["_admin_message_payload"]

    def test_bot_compiles(self):
        compile(SOURCE, str(ROOT / "bot.py"), "exec")

    def test_pyrogram_photo_object_keeps_file_id_and_caption(self):
        # Pyrogram/Pyrofork Message.photo is a single Photo object, not a list.
        payload = self._payload_helper()(SimpleNamespace(
            photo=SimpleNamespace(file_id="telegram-photo-file-id"),
            caption="Hello {user}",
            text=None,
        ))
        self.assertEqual(payload, {
            "type": "photo",
            "file_id": "telegram-photo-file-id",
            "caption": "Hello {user}",
        })

    def test_photo_size_list_is_still_supported(self):
        payload = self._payload_helper()(SimpleNamespace(
            photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")],
            caption="Hello {user}",
            text=None,
        ))
        self.assertEqual(payload, {
            "type": "photo", "file_id": "large", "caption": "Hello {user}"
        })

    def test_text_payload_remains_supported(self):
        self.assertEqual(self._payload_helper()(
            SimpleNamespace(photo=None, text="Welcome {user}", caption=None)
        ), "Welcome {user}")

    def test_caption_only_fallback_remains_supported(self):
        self.assertEqual(self._payload_helper()(
            SimpleNamespace(photo=None, text=None, caption="Caption text")
        ), "Caption text")

    def test_welcome_and_fsub_use_same_configured_media_sender(self):
        # Both entry points must route through the shared photo-aware helper.
        welcome = get_node("_reply_welcome")
        start = get_node("start_handler")
        self.assertIn("_reply_configured_message", {n.func.id for n in ast.walk(welcome) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)})
        self.assertIn("_reply_configured_message", {n.func.id for n in ast.walk(start) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)})

    def test_reply_helper_sends_photo_and_caption(self):
        node = get_node("_reply_configured_message")
        namespace = {"Message": object, "Optional": Optional, "Dict": Dict}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<reply>", "exec"), namespace)

        class FakeMessage:
            async def reply_photo(self, **kwargs):
                self.result = kwargs
                return kwargs

            async def reply_text(self, *args, **kwargs):
                self.result = (args, kwargs)
                return self.result

        message = FakeMessage()
        asyncio.run(namespace["_reply_configured_message"](
            message,
            {"type": "photo", "file_id": "photo-file", "caption": "Hi {user}!"},
            default_text="default",
            reply_markup="keyboard",
            replacements={"user": "Alex"},
        ))
        self.assertEqual(message.result["photo"], "photo-file")
        self.assertEqual(message.result["caption"], "Hi Alex!")
        self.assertEqual(message.result["reply_markup"], "keyboard")

    def test_text_welcome_replaces_user_placeholder(self):
        node = get_node("_reply_configured_message")
        namespace = {"Message": object, "Optional": Optional, "Dict": Dict}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<reply>", "exec"), namespace)

        class FakeMessage:
            async def reply_text(self, text, **kwargs):
                self.result = (text, kwargs)
                return self.result

        message = FakeMessage()
        asyncio.run(namespace["_reply_configured_message"](
            message, "Welcome, {user}", default_text="default",
            replacements={"user": "Alex"},
        ))
        self.assertEqual(message.result[0], "Welcome, Alex")


if __name__ == "__main__":
    unittest.main()
