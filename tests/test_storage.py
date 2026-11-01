import asyncio
import unittest
from helper.storage import archive_and_verify, deliver_archived_message


class FakeMessage:
    def __init__(self, message_id, empty=False):
        self.id = message_id
        self.empty = empty
        self.copy_calls = []

    async def copy(self, **kwargs):
        self.copy_calls.append(kwargs)
        return FakeMessage(12345 if "disable_notification" in kwargs else 54321)


class FakeClient:
    def __init__(self, message=None):
        self.message = message
        self.lookups = []

    async def get_messages(self, **kwargs):
        self.lookups.append(kwargs)
        return self.message


class StorageTests(unittest.TestCase):
    def test_archive_copy_is_read_back_before_id_returned(self):
        async def run():
            source = FakeMessage(1)
            client = FakeClient(FakeMessage(12345))
            result = await archive_and_verify(client, source, -1001234567890)
            self.assertEqual(result, 12345)
            self.assertEqual(source.copy_calls, [{"chat_id": -1001234567890, "disable_notification": True}])
            self.assertEqual(client.lookups, [{"chat_id": -1001234567890, "message_ids": 12345}])
        asyncio.run(run())

    def test_unverified_copy_does_not_return_success(self):
        async def run():
            with self.assertRaisesRegex(RuntimeError, "could not be verified"):
                await archive_and_verify(FakeClient(FakeMessage(3)), FakeMessage(1), -1001234567890)
        asyncio.run(run())

    def test_retrieval_copies_source_and_keeps_archive_source_untouched(self):
        async def run():
            archived = FakeMessage(77)
            client = FakeClient(archived)
            delivered = await deliver_archived_message(client, -1001234567890, 77, 555, True)
            self.assertEqual(delivered.id, 54321)
            self.assertEqual(archived.copy_calls, [{"chat_id": 555, "protect_content": True}])
            self.assertEqual(client.lookups, [{"chat_id": -1001234567890, "message_ids": 77}])
        asyncio.run(run())

    def test_missing_archive_message_is_rejected(self):
        async def run():
            with self.assertRaisesRegex(LookupError, "no longer available"):
                await deliver_archived_message(FakeClient(None), -1001234567890, 77, 555)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
