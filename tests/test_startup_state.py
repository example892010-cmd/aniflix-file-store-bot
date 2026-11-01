import unittest
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


if __name__ == "__main__":
    unittest.main()
