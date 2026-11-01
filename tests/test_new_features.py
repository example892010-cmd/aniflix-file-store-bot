import unittest

from helper.state_store import DEFAULT_STATE, StateStore
from helper.storage import (
    BATCH_MANIFEST_PREFIX,
    MAX_BATCH_MESSAGES,
    decode_batch_manifest,
    deliver_archived_batch,
    encode_batch_manifest,
    save_batch_manifest,
)


class StoredMessage:
    def __init__(self, message_id, text=None):
        self.id = message_id
        self.text = text
        self.empty = False
        self.deliveries = []

    async def copy(self, **kwargs):
        self.deliveries.append(kwargs["chat_id"])
        return self


class FakeTelegram:
    def __init__(self, messages=None):
        self.messages = dict(messages or {})
        self.next_id = max(self.messages, default=0) + 1

    async def send_message(self, chat_id, text, **_kwargs):
        msg = StoredMessage(self.next_id, text)
        self.messages[msg.id] = msg
        self.next_id += 1
        return msg

    async def get_messages(self, chat_id=None, message_ids=None, **kwargs):
        message_id = message_ids if message_ids is not None else kwargs.get("message_id")
        if isinstance(message_id, (list, tuple)):
            return [self.messages.get(item) for item in message_id]
        return self.messages.get(message_id)


class NewFeatureTests(unittest.IsolatedAsyncioTestCase):
    CHANNEL = -1001234567890

    def test_manifest_round_trip_and_validation(self):
        manifest = encode_batch_manifest([11, 12, 13, 27])
        self.assertTrue(manifest.startswith(BATCH_MANIFEST_PREFIX))
        self.assertEqual(decode_batch_manifest(manifest), [11, 12, 13, 27])
        for invalid in ([11, 13, 12, 12], [0, 12], list(range(1, MAX_BATCH_MESSAGES + 2))):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                encode_batch_manifest(invalid)
        for invalid_text in ("bad manifest", BATCH_MANIFEST_PREFIX + "{}"):
            with self.subTest(text=invalid_text), self.assertRaises(ValueError):
                decode_batch_manifest(invalid_text)

    async def test_manifest_save_is_verified(self):
        client = FakeTelegram()
        manifest_id = await save_batch_manifest(client, self.CHANNEL, [51, 52, 53])
        self.assertEqual(manifest_id, 1)
        self.assertEqual(decode_batch_manifest(client.messages[manifest_id].text), [51, 52, 53])

    async def test_batch_delivery_uses_manifest_order_not_archive_id_range(self):
        manifest_text = encode_batch_manifest([11, 14, 19])
        client = FakeTelegram({
            80: StoredMessage(80, manifest_text),
            11: StoredMessage(11),
            12: StoredMessage(12),  # unrelated archive message, intentionally omitted
            14: StoredMessage(14),
            19: StoredMessage(19),
        })
        result = await deliver_archived_batch(client, self.CHANNEL, 80, 777, True)
        self.assertEqual(result, (3, 0))
        self.assertEqual([client.messages[x].deliveries for x in (11, 14, 19)], [[777], [777], [777]])
        self.assertEqual(client.messages[12].deliveries, [])

    def test_state_default_shape_and_merge(self):
        store = StateStore(FakeTelegram(), self.CHANNEL)
        merged = store._merge_defaults({"admins": [999], "stats": {"total_users": 10}})
        self.assertEqual(merged["admins"], [999])
        self.assertEqual(merged["stats"]["total_users"], 10)
        self.assertEqual(merged["stats"]["total_links"], DEFAULT_STATE["stats"]["total_links"])
        self.assertEqual(merged["fsub_channels"], [])


if __name__ == "__main__":
    unittest.main()
