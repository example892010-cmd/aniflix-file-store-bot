import unittest
from unittest.mock import AsyncMock, MagicMock

from helper.state_store import StateStore


class FsubStatisticsTests(unittest.IsolatedAsyncioTestCase):
    def make_store(self):
        client = MagicMock()
        client.edit_message_text = AsyncMock()
        client.send_message = AsyncMock()
        client.pin_chat_message = AsyncMock()
        return StateStore(client, archive_channel_id=0)

    async def test_request_counts_are_unique_and_channel_specific(self):
        store = self.make_store()
        store.save_state = AsyncMock()

        await store.record_fsub_join_request(-1001, 10)
        await store.record_fsub_join_request(-1001, 10)
        await store.record_fsub_join_request(-1001, 11)
        await store.record_fsub_join_request(-1002, 10)

        self.assertEqual(store.get_fsub_channel_stats(-1001)["requests"], 2)
        self.assertEqual(store.get_fsub_channel_stats(-1002)["requests"], 1)

    async def test_approval_requires_a_previously_recorded_request(self):
        store = self.make_store()
        store.save_state = AsyncMock()

        self.assertFalse(await store.record_fsub_request_approved(-1001, 99))
        await store.record_fsub_join_request(-1001, 10)
        self.assertTrue(await store.record_fsub_request_approved(-1001, 10))
        self.assertFalse(await store.record_fsub_request_approved(-1001, 10))

        counts = store.get_fsub_channel_stats(-1001)
        self.assertEqual(counts["requests"], 1)
        self.assertEqual(counts["approved"], 1)

    async def test_normal_join_counts_are_unique_and_separate(self):
        store = self.make_store()
        store.save_state = AsyncMock()

        await store.record_fsub_verified_join(-1001, 10)
        await store.record_fsub_verified_join(-1001, 10)
        await store.record_fsub_verified_join(-1001, 12)

        self.assertEqual(store.get_fsub_channel_stats(-1001)["joined"], 2)
        self.assertEqual(store.get_fsub_channel_stats(-1002)["joined"], 0)


if __name__ == "__main__":
    unittest.main()
