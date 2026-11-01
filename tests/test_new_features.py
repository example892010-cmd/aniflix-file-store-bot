import unittest
from helper.link_codec import (
    decode_text,
    encode_batch_payload,
    encode_storage_payload,
    parse_link_payload,
)
from helper.state_store import TelegramStateStore, default_state, USER_PREFIX, _encode_json
from helper.storage import (
    BATCH_MANIFEST_PREFIX,
    decode_batch_manifest,
    deliver_archived_batch,
    encode_batch_manifest,
    save_batch_manifest,
)


class NewFeatureTests(unittest.TestCase):
    CHANNEL = -1001234567890

    def test_batch_payload_round_trip(self):
        payload = encode_batch_payload(self.CHANNEL, 98765)
        self.assertLessEqual(len(payload), 64)
        self.assertEqual(parse_link_payload(decode_text(payload), self.CHANNEL), ("batch", self.CHANNEL, 98765))

    def test_single_payload_still_parses(self):
        payload = encode_storage_payload(self.CHANNEL, 123)
        self.assertEqual(parse_link_payload(decode_text(payload), self.CHANNEL), ("single", self.CHANNEL, 123))

    def test_batch_manifest_preserves_order_and_validates(self):
        text = encode_batch_manifest([11, 12, 13, 27])
        self.assertTrue(text.startswith(BATCH_MANIFEST_PREFIX))
        self.assertEqual(decode_batch_manifest(text), [11, 12, 13, 27])
        with self.assertRaises(ValueError):
            encode_batch_manifest([11, 13, 12])
        with self.assertRaises(ValueError):
            decode_batch_manifest("bad manifest")

    def test_default_state_contains_requested_settings(self):
        state = default_state()
        self.assertEqual(state["fsub_channels"], [])
        self.assertEqual(state["links_count"]["getlink"], 0)
        self.assertEqual(state["links_count"]["batch"], 0)
        self.assertIn("welcome", state)
        self.assertIn("fsub_message", state)

    def test_batch_manifest_is_saved_and_verified(self):
        import asyncio

        class FakeMessage:
            def __init__(self, message_id, text=""):
                self.id = message_id
                self.text = text
                self.empty = False

        class FakeClient:
            def __init__(self):
                self.sent = None
                self.message = None

            async def send_message(self, _channel, text, **_kwargs):
                self.sent = FakeMessage(900, text)
                return self.sent

            async def get_messages(self, _channel, message_id):
                self.asserted_id = message_id
                return self.sent

        async def run():
            client = FakeClient()
            manifest_id = await save_batch_manifest(client, self.CHANNEL, [51, 52, 53])
            self.assertEqual(manifest_id, 900)
            self.assertEqual(decode_batch_manifest(client.sent.text), [51, 52, 53])

        asyncio.run(run())

    def test_batch_delivery_uses_manifest_order(self):
        import asyncio

        class Archived:
            def __init__(self, message_id, text=None):
                self.id = message_id
                self.text = text
                self.empty = False
                self.deliveries = []

            async def copy(self, **kwargs):
                self.deliveries.append(kwargs["chat_id"])
                return self

        class FakeClient:
            def __init__(self):
                self.messages = {80: Archived(80, encode_batch_manifest([11, 12, 13]))}
                self.messages.update({value: Archived(value) for value in [11, 12, 13]})
                self.delivered = []

            async def get_messages(self, chat_id=None, message_ids=None, **kwargs):
                message_id = message_ids if message_ids is not None else kwargs.get("message_id")
                return self.messages.get(message_id)

        async def run():
            client = FakeClient()
            result = await deliver_archived_batch(client, self.CHANNEL, 80, 777, True)
            self.assertEqual(result, (3, 0))
            self.assertEqual([client.messages[x].deliveries for x in (11, 12, 13)], [[777], [777], [777]])

        asyncio.run(run())

    def test_user_marker_prefix_is_unique(self):
        marker = USER_PREFIX + _encode_json({"id": 77, "first_name": "Test"})
        self.assertTrue(marker.startswith("ANIFLIX_USER_V1:"))
        self.assertLess(len(marker), 1000)

    def test_admin_permissions_keep_owner(self):
        class FakeClient:
            pass
        store = TelegramStateStore(FakeClient(), self.CHANNEL)
        store.state["extra_admins"] = [456]
        self.assertEqual(store.admins(123, [234]), {123, 234, 456})

    def test_state_and_user_registry_restore_from_channel_records(self):
        import asyncio

        class StoredMessage:
            def __init__(self, message_id, text):
                self.id = message_id
                self.text = text
                self.caption = None

        class FakeTelegram:
            def __init__(self):
                self.messages = []
                self.next_id = 1

            async def get_chat_history(self, _chat_id):
                for item in reversed(self.messages):
                    yield item

            async def send_message(self, _chat_id, text, **_kwargs):
                item = StoredMessage(self.next_id, text)
                self.next_id += 1
                self.messages.append(item)
                return item

            async def delete_messages(self, _chat_id, message_id):
                self.messages = [item for item in self.messages if item.id != message_id]

        class User:
            id = 777
            first_name = "Test"
            last_name = "Member"
            username = "test_member"

        async def run():
            client = FakeTelegram()
            first = TelegramStateStore(client, self.CHANNEL)
            await first.load()
            first.state["extra_admins"] = [888]
            await first.save()
            self.assertTrue(await first.register_user(User()))

            restored = TelegramStateStore(client, self.CHANNEL)
            await restored.load()
            self.assertEqual(restored.state["extra_admins"], [888])
            self.assertEqual(restored.users[777]["username"], "test_member")
            self.assertEqual(restored.admins(123, []), {123, 888})

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
