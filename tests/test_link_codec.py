import unittest

from helper.link_codec import (
    decode_batch,
    decode_batch_manifest_id,
    decode_message_id,
    encode_batch,
    encode_batch_manifest_id,
    encode_message_id,
)


class LinkCodecTests(unittest.TestCase):
    def test_single_message_round_trip_and_existing_prefixes(self):
        for message_id in (1, 987654321, 2**63 + 17):
            with self.subTest(message_id=message_id):
                payload = encode_message_id(message_id)
                self.assertEqual(decode_message_id(payload), message_id)
                self.assertEqual(decode_message_id(f"file_{payload}"), message_id)
                self.assertEqual(decode_message_id(f"msg_{payload}"), message_id)

    def test_batch_range_round_trip_preserves_old_format(self):
        payload = encode_batch(98765, 123)
        self.assertEqual(decode_batch(payload), (123, 98765))
        self.assertEqual(decode_batch(f"batch_{payload}"), (123, 98765))

    def test_batch_manifest_reference_is_distinct_from_legacy_range(self):
        payload = encode_batch_manifest_id(987654321)
        self.assertLessEqual(len(payload), 64)
        self.assertEqual(decode_batch_manifest_id(payload), 987654321)
        with self.assertRaises(ValueError):
            decode_batch(payload)

    def test_invalid_inputs_are_rejected(self):
        for payload in ("", "!", "====", "x" * 100):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                decode_message_id(payload)
        for message_id in (0, -1, 2**64):
            with self.subTest(message_id=message_id), self.assertRaises(ValueError):
                encode_message_id(message_id)
        for ids in ((0, 1), (1, -1)):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                encode_batch(*ids)
        with self.assertRaises(ValueError):
            decode_batch("not-a-batch")

    def test_numeric_legacy_single_link_remains_supported(self):
        self.assertEqual(decode_message_id("987654321"), 987654321)


if __name__ == "__main__":
    unittest.main()
