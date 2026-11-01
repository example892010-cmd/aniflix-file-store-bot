import unittest
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock
from helper.state_store import StateStore, STATE_TAG


class TestStartupState(unittest.IsolatedAsyncioTestCase):
    async def test_startup_does_not_call_get_chat_history(self):
        mock_client = MagicMock()
        mock_client.get_chat_history = MagicMock(side_effect=Exception("BOT_METHOD_INVALID"))
        
        pinned_msg = MagicMock()
        pinned_msg.id = 1234
        pinned_msg.text = f"{STATE_TAG}\n{{\"admins\": [999], \"stats\": {{\"total_users\": 10}}}}"
        
        chat_obj = MagicMock()
        chat_obj.pinned_message = pinned_msg
        mock_client.get_chat = AsyncMock(return_value=chat_obj)
        mock_client.send_message = AsyncMock()
        mock_client.pin_chat_message = AsyncMock()

        store = StateStore(mock_client, archive_channel_id=-1001234567890)
        await store.load_state()

        # Confirms get_chat_history is never called on startup
        mock_client.get_chat_history.assert_not_called()
        self.assertEqual(store.get("admins"), [999])
        self.assertEqual(store.get_stats()["total_users"], 10)

    async def test_state_is_created_pinned_and_updated_without_history(self):
        mock_client = MagicMock()
        mock_client.get_chat = AsyncMock(return_value=MagicMock(pinned_message=None))
        mock_client.get_messages = AsyncMock(return_value=None)
        mock_client.send_message = AsyncMock(return_value=MagicMock(id=4321))
        mock_client.edit_message_text = AsyncMock()
        mock_client.pin_chat_message = AsyncMock()
        mock_client.get_chat_history = MagicMock(side_effect=AssertionError("history is unsupported"))
        store = StateStore(mock_client, archive_channel_id=-1001234567890)

        with tempfile.TemporaryDirectory() as workdir:
            old_cwd = os.getcwd()
            try:
                os.chdir(workdir)
                await store.load_state()
                self.assertEqual(store.state_message_id, 4321)
                mock_client.send_message.assert_awaited_once()
                self.assertIsNone(mock_client.send_message.await_args.kwargs["parse_mode"])
                mock_client.pin_chat_message.assert_awaited_once()

                await store.set("welcome_message", "Welcome _everyone_")
                mock_client.edit_message_text.assert_awaited_once()
                self.assertIsNone(mock_client.edit_message_text.await_args.kwargs["parse_mode"])
            finally:
                os.chdir(old_cwd)
        mock_client.get_chat_history.assert_not_called()


if __name__ == "__main__":
    unittest.main()
