"""Static/regression tests for the interactive ANIFLIX FSUB workflow.

These tests do not claim live Telegram verification. They run without Telegram
credentials and verify the local workflow contract and membership gate.
"""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BOT_SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
TREE = ast.parse(BOT_SOURCE)


def _function_node(name):
    return next(
        node for node in TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


class ForceSubscriptionWorkflowTests(unittest.TestCase):
    def test_bot_module_compiles(self):
        compile(BOT_SOURCE, str(ROOT / "bot.py"), "exec")

    def test_admin_add_flow_requires_forwarded_channel(self):
        self.assertIn('ADMIN_PENDING_ACTIONS[user_id] = "fsub_add_forward"', BOT_SOURCE)
        self.assertIn("_forwarded_channel(message)", BOT_SOURCE)
        self.assertIn("adm_fsub_method_request", BOT_SOURCE)
        self.assertIn("adm_fsub_method_normal", BOT_SOURCE)

    def test_bot_admin_invite_permission_is_checked(self):
        self.assertIn("_verify_bot_can_manage_invites(client, int(channel.id))", BOT_SOURCE)
        self.assertIn("can_invite_users", BOT_SOURCE)
        self.assertIn("get_chat_member(channel_id, bot_identity.id)", BOT_SOURCE)

    def test_invite_link_is_created_using_selected_method(self):
        tree = _function_node("callback_router")
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_chat_invite_link"
        ]
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(
            keyword.arg == "creates_join_request"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "join_request"
            for keyword in calls[0].keywords
        ))

    def test_pending_join_request_is_not_membership(self):
        helper_nodes = [_function_node("_member_status_name"), _function_node("_is_joined_member")]
        module = ast.Module(body=helper_nodes, type_ignores=[])
        namespace = {}
        exec(compile(module, "<fsub-membership-helpers>", "exec"), namespace)
        is_joined = namespace["_is_joined_member"]

        self.assertTrue(is_joined(SimpleNamespace(status="ChatMemberStatus.MEMBER")))
        self.assertTrue(is_joined(SimpleNamespace(status="ChatMemberStatus.ADMINISTRATOR")))
        self.assertTrue(is_joined(SimpleNamespace(status="ChatMemberStatus.OWNER")))
        self.assertFalse(is_joined(SimpleNamespace(status="ChatMemberStatus.LEFT")))
        self.assertFalse(is_joined(SimpleNamespace(status="ChatMemberStatus.BANNED")))

    def test_forwarded_channel_supports_old_and_new_telegram_metadata(self):
        helper = _function_node("_forwarded_channel")
        namespace = {"Message": object}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), "<forward-helper>", "exec"), namespace)
        forwarded = SimpleNamespace(id=-100123, title="Test Channel")
        legacy = SimpleNamespace(forward_from_chat=forwarded, forward_origin=None)
        modern = SimpleNamespace(
            forward_from_chat=None,
            forward_origin=SimpleNamespace(chat=forwarded),
        )
        self.assertIs(namespace["_forwarded_channel"](legacy), forwarded)
        self.assertIs(namespace["_forwarded_channel"](modern), forwarded)

    def test_admin_invite_permission_validation(self):
        helper = _function_node("_verify_bot_can_manage_invites")
        status_helper = _function_node("_member_status_name")
        namespace = {"PermissionError": PermissionError, "Client": object}
        exec(compile(ast.Module(body=[status_helper, helper], type_ignores=[]), "<admin-check>", "exec"), namespace)
        verify = namespace["_verify_bot_can_manage_invites"]

        class FakeClient:
            async def get_me(self):
                return SimpleNamespace(id=999)

            async def get_chat_member(self, chat_id, user_id):
                self.asserted = (chat_id, user_id)
                return SimpleNamespace(
                    status="ChatMemberStatus.ADMINISTRATOR",
                    privileges=SimpleNamespace(can_invite_users=True),
                )

        import asyncio
        client = FakeClient()
        asyncio.run(verify(client, -100123))
        self.assertEqual(client.asserted, (-100123, 999))

        class NoInviteClient(FakeClient):
            async def get_chat_member(self, chat_id, user_id):
                return SimpleNamespace(
                    status="ChatMemberStatus.ADMINISTRATOR",
                    privileges=SimpleNamespace(can_invite_users=False),
                )

        with self.assertRaises(PermissionError):
            asyncio.run(verify(NoInviteClient(), -100123))

    def test_membership_check_fails_closed_for_non_members(self):
        check_node = _function_node("check_fsub")
        source = ast.get_source_segment(BOT_SOURCE, check_node)
        self.assertIn("if not _is_joined_member(member):", source)
        self.assertIn("unjoined.append(ch)", source)
        self.assertIn("except Exception as e:", source)

    def test_no_automatic_join_request_approval_api(self):
        forbidden_names = {
            "approve_chat_join_request",
            "approveChatJoinRequest",
            "approve_all_join_requests",
        }
        found = {
            node.func.attr for node in ast.walk(TREE)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertFalse(forbidden_names & found)

    def test_added_channel_persists_invite_type_and_link(self):
        self.assertIn('"link": invite.invite_link', BOT_SOURCE)
        self.assertIn('"type": "join_request" if join_request else "normal"', BOT_SOURCE)
        self.assertIn('"join_request": join_request', BOT_SOURCE)
        self.assertIn("await bot.state_store.set_fsub_channels(channels)", BOT_SOURCE)

    def test_ten_channel_limit_is_preserved(self):
        self.assertGreaterEqual(BOT_SOURCE.count(">= 10"), 2)


if __name__ == "__main__":
    unittest.main()
