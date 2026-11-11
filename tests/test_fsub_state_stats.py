import os
import tempfile
import unittest

from helper.state_store import StateStore


class FakeClient:
    pass


class FsubStateStatsTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_acceptance_pending_and_clear_are_unique_and_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(FakeClient(), 0,
                               os.path.join(tmp, "aniflix.sqlite3"))
            channel_id = -1001234567890
            await store.record_fsub_join_request(channel_id, 100)
            await store.record_fsub_join_request(channel_id, 100)
            self.assertEqual(store.get_fsub_counts(channel_id)["requests"], 1)
            self.assertEqual(store.get_fsub_counts(channel_id)["pending"], 1)

            await store.record_fsub_member(channel_id, 100, accepted=True)
            await store.record_fsub_member(channel_id, 100, accepted=True)
            counts = store.get_fsub_counts(channel_id)
            self.assertEqual(counts["accepted"], 1)
            self.assertEqual(counts["pending"], 0)

            await store.record_fsub_join_request(channel_id, 200)
            self.assertEqual(store.get_fsub_counts(channel_id)["pending"], 1)
            await store.record_fsub_rejected_or_withdrawn(channel_id, 200)
            counts = store.get_fsub_counts(channel_id)
            self.assertEqual(counts["requests"], 2)
            self.assertEqual(counts["pending"], 0)
            self.assertEqual(counts["closed"], 1)

            await store.clear_fsub_counts(channel_id)
            self.assertEqual(store.get_fsub_counts(channel_id)["requests"], 0)
            self.assertTrue(store.has_fsub_join_request(channel_id, 100))
            self.assertTrue(store.has_fsub_join_request(channel_id, 200))
            await store.record_fsub_member(channel_id, 100, accepted=True)
            self.assertEqual(store.get_fsub_counts(channel_id)["accepted"], 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
