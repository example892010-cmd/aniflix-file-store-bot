import unittest
from helper.link_codec import decode_text, encode_storage_payload, parse_storage_payload


class LinkCodecTests(unittest.TestCase):
    CHANNEL = -1001234567890

    def test_round_trip_and_payload_limit(self):
        payload = encode_storage_payload(self.CHANNEL, 987654321)
        self.assertLessEqual(len(payload), 64)
        self.assertEqual(parse_storage_payload(decode_text(payload), self.CHANNEL), (self.CHANNEL, 987654321))

    def test_rejects_wrong_channel(self):
        payload = encode_storage_payload(self.CHANNEL, 55)
        with self.assertRaisesRegex(ValueError, "does not belong"):
            parse_storage_payload(decode_text(payload), -1009876543210)

    def test_rejects_bad_payloads(self):
        for payload in ("", "bad!payload", "x" * 65):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                decode_text(payload)

    def test_rejects_non_channel_and_non_positive_message(self):
        with self.assertRaises(ValueError):
            encode_storage_payload(-12345, 1)
        with self.assertRaises(ValueError):
            encode_storage_payload(self.CHANNEL, 0)

    def test_legacy_link_compatibility(self):
        old_payload = f"get-{987654321 * abs(self.CHANNEL)}"
        from helper.link_codec import encode_text
        self.assertEqual(parse_storage_payload(decode_text(encode_text(old_payload)), self.CHANNEL), (self.CHANNEL, 987654321))


if __name__ == "__main__":
    unittest.main()
