import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEST_ENV = {
    "BOT_TOKEN": "123456:TEST_TOKEN",
    "API_ID": "12345",
    "API_HASH": "test_hash",
    "OWNER_ID": "123456789",
    "DB_CHANNEL": "-1001234567890",
}


class RuntimeConfigTests(unittest.TestCase):
    def _run_python(self, code, extra_env=None):
        env = os.environ.copy()
        env.update(TEST_ENV)
        env.update(extra_env or {})
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def test_bot_receives_environment_settings(self):
        result = self._run_python(
            "import bot; "
            "assert bot.bot.api_id == 12345; "
            "assert bot.bot.settings.api_hash == 'test_hash'; "
            "assert bot.bot.archive_channel_id == -1001234567890; "
            "assert 123456789 in bot.bot.settings.admins; "
            "assert bot.bot.bot_token == '123456:TEST_TOKEN'; "
            "assert hasattr(bot.bot.storage, 'open'); "
            "assert bot.bot.file_storage is not bot.bot.storage; "
            "assert bot.bot.file_storage.archive_channel_id == -1001234567890"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_message_and_callback_handlers_register_without_network(self):
        code = """import asyncio
import bot
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
loop = bot.bot.loop
loop.run_until_complete(asyncio.sleep(0))
handlers = [handler for group in bot.bot.dispatcher.groups.values() for handler in group]
assert sum(type(handler) is MessageHandler for handler in handlers) == 10
assert sum(type(handler) is CallbackQueryHandler for handler in handlers) == 1
"""
        result = self._run_python(code)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_render_web_server_starts_when_port_is_set(self):
        code = """import asyncio
import main
async def run():
    runner = await main.start_web_server()
    assert runner.sites
    await runner.cleanup()
asyncio.run(run())
"""
        result = self._run_python(code, {"PORT": "18080"})
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
