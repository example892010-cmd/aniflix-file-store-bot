import unittest
from pathlib import Path

from helper.force_sub import (
    collect_unjoined_channels,
    is_channel_admin,
    is_channel_member,
    status_name,
)


class FakeMember:
    def __init__(self, status, is_member=False):
        self.status = status
        self.is_member = is_member


class FakeClient:
    def __init__(self, results):
        self.results = results

    async def get_chat_member(self, channel, user_id):
        value = self.results[channel]
        if isinstance(value, Exception):
            raise value
        return value


class ForceSubLogicTests(unittest.IsolatedAsyncioTestCase):
    def test_member_statuses(self):
        for status in ("member", "administrator", "owner", "creator"):
            with self.subTest(status=status):
                self.assertTrue(is_channel_member(FakeMember(status)))
        self.assertTrue(is_channel_member(FakeMember("restricted", is_member=True)))
        self.assertFalse(is_channel_member(FakeMember("restricted", is_member=False)))
        self.assertFalse(is_channel_member(FakeMember("left")))
        self.assertFalse(is_channel_member(FakeMember("kicked")))

    def test_enum_like_status_normalization(self):
        class Status:
            value = "administrator"
        class Member:
            status = Status()
        self.assertEqual(status_name(Member()), "administrator")
        self.assertTrue(is_channel_admin(Member()))

    async def test_collect_unjoined_keeps_only_unverified_channels(self):
        channels = [
            {"id": -1001, "name": "Joined"},
            {"id": -1002, "name": "Left"},
            {"id": -1003, "name": "Restricted"},
        ]
        client = FakeClient({
            -1001: FakeMember("member"),
            -1002: FakeMember("left"),
            -1003: FakeMember("restricted", is_member=False),
        })
        result = await collect_unjoined_channels(client, 77, channels)
        self.assertEqual([item["id"] for item in result], [-1002, -1003])

    async def test_api_errors_fail_closed(self):
        channels = [{"id": -1004, "name": "No permissions"}]
        client = FakeClient({
            -1004: RuntimeError("CHAT_ADMIN_REQUIRED"),
        })
        result = await collect_unjoined_channels(client, 77, channels)
        self.assertEqual(result, channels)

    async def test_missing_channel_identifier_fails_closed(self):
        channels = [{"name": "Invalid config"}]
        result = await collect_unjoined_channels(FakeClient({}), 77, channels)
        self.assertEqual(result, channels)


class ForceSubIntegrationSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")

    def test_start_checks_fsub_before_welcome_or_payload_delivery(self):
        start = self.source.index("async def start_handler(")
        end = self.source.index("\nbot.on_message(filters.command(\"start\")", start)
        body = self.source[start:end]
        self.assertIn("unjoined = await check_fsub(client, user_id)", body)
        self.assertIn("reply_markup=get_fsub_keyboard", body)

    def test_retry_callback_keeps_deep_link_payload(self):
        callback = self.source.index('if data.startswith("try_again"):')
        section = self.source[callback:callback + 1600]
        self.assertIn('data.split("try_again_", 1)[1]', section)
        self.assertIn("_deliver_start_payload(client, user_id, query.message, param)", section)

    def test_old_and_new_admin_input_formats_are_retained(self):
        self.assertIn("Forward any message from that channel here.", self.source)
        self.assertIn("adm_fsub_method_request", self.source)
        self.assertIn("adm_fsub_method_normal", self.source)


if __name__ == "__main__":
    unittest.main()
