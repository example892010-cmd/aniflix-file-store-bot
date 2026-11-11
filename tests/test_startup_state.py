import unittest
import os
import tempfile
import json
from unittest.mock import AsyncMock, MagicMock
from helper.state_store import StateStore, STATE_TAG


class TestStartupState(unittest.IsolatedAsyncioTestCase):
    async def test_startup_imports_existing_pinned_state_without_history(self):
        mock_client = MagicMock()
        mock_client.get_chat_history = MagicMock(side_effect=Exception("BOT_METHOD_INVALID"))

        pinned_msg = MagicMock()
        pinned_msg.id = 1234
        pinned_msg.text = f"{STATE_TAG}\n" + json.dumps({"admins": [999], "stats": {"total_users": 10}})

        chat_obj = MagicMock()
        chat_obj.pinned_message = pinned_msg
        mock_client.get_chat = AsyncMock(return_value=chat_obj)
        mock_client.get_messages = AsyncMock(return_value=None)
        mock_client.send_message = AsyncMock()
        mock_client.pin_chat_message = AsyncMock()

        with tempfile.TemporaryDirectory() as workdir:
            old_cwd = os.getcwd()
            try:
                os.chdir(workdir)
                store = StateStore(mock_client, archive_channel_id=-1001234567890)
                await store.load_state()
                self.assertEqual(store.get("admins"), [999])
                self.assertEqual(store.get_stats()["total_users"], 10)
                self.assertEqual(store.state_message_id, 1234)
                self.assertFalse(mock_client.send_message.await_count)
                store.close()
            finally:
                os.chdir(old_cwd)

        mock_client.get_chat_history.assert_not_called()

    async def test_missing_state_snapshot_fails_closed_without_creating_empty_state(self):
        mock_client = MagicMock()
        mock_client.get_chat = AsyncMock(return_value=MagicMock(pinned_message=None))
        mock_client.get_messages = AsyncMock(return_value=None)
        mock_client.send_message = AsyncMock()
        mock_client.pin_chat_message = AsyncMock()
        mock_client.get_chat_history = MagicMock(side_effect=AssertionError("history is unsupported"))

        with tempfile.TemporaryDirectory() as workdir:
            old_cwd = os.getcwd()
            try:
                os.chdir(workdir)
                store = StateStore(mock_client, archive_channel_id=-1001234567890)
                with self.assertRaisesRegex(RuntimeError, "Refusing to initialize empty state"):
                    await store.load_state()
                mock_client.send_message.assert_not_awaited()
                mock_client.pin_chat_message.assert_not_awaited()
                store.close()
            finally:
                os.chdir(old_cwd)

        mock_client.get_chat_history.assert_not_called()


if __name__ == "__main__":
    unittest.main()
