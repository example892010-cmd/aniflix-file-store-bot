import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from helper.state_store import StateStore


class FsubAdminCountStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.temp.name)
        self.client = MagicMock()
        self.client.get_chat = AsyncMock(return_value=MagicMock(pinned_message=None))
        self.client.get_messages = AsyncMock(return_value=None)
        self.client.send_message = AsyncMock(return_value=MagicMock(id=100))
        self.client.pin_chat_message = AsyncMock()
        self.client.edit_message_text = AsyncMock()
        self.store = StateStore(self.client, archive_channel_id=0)

    async def asyncTearDown(self):
        os.chdir(self.old_cwd)
        self.temp.cleanup()

    async def test_counts_are_unique_and_separated_by_channel(self):
        await self.store.record_fsub_join_request(-1001, 10)
        await self.store.record_fsub_join_request(-1001, 10)
        await self.store.record_fsub_join_request(-1001, 11)
        await self.store.record_fsub_member(-1001, 10, accepted=True)
        await self.store.record_fsub_member(-1002, 10)
        self.assertEqual(self.store.get_fsub_counts(-1001), {"joined": 0, "requests": 2, "accepted": 1})
        self.assertEqual(self.store.get_fsub_counts(-1002), {"joined": 1, "requests": 0, "accepted": 0})

    async def test_clear_counts_does_not_remove_join_request_access(self):
        await self.store.record_fsub_join_request(-1001, 77)
        self.assertTrue(self.store.has_fsub_join_request(-1001, 77))
        await self.store.clear_fsub_counts(-1001)
        self.assertEqual(self.store.get_fsub_counts(-1001), {"joined": 0, "requests": 0, "accepted": 0})
        self.assertTrue(self.store.has_fsub_join_request(-1001, 77))

    async def test_user_profiles_are_saved_and_user_ids_remain_broadcast_compatible(self):
        await self.store.record_user(123, "Asha", "K", "asha_test")
        await self.store.record_user(123, "Asha", "K", "asha_test")
        self.assertEqual(self.store.get("users"), [123])
        self.assertEqual(self.store.get("user_profiles")["123"], {
            "first_name": "Asha", "last_name": "K", "username": "asha_test"
        })
        self.assertEqual(self.store.get_stats()["total_users"], 1)


if __name__ == "__main__":
    unittest.main()
