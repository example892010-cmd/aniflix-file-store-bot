import unittest
from pathlib import Path
from config import load_settings

ROOT = Path(__file__).resolve().parents[1]
BASE = {
    "BOT_TOKEN": "123456:TEST_TOKEN",
    "API_ID": "12345",
    "API_HASH": "test_hash",
    "OWNER_ID": "123456789",
    "DB_CHANNEL": "-1001234567890",
}


class ConfigAndDeploymentTests(unittest.TestCase):
    def test_settings_load_and_owner_is_admin(self):
        settings = load_settings(BASE)
        self.assertEqual(settings.archive_channel_id, -1001234567890)
        self.assertEqual(settings.admins, (123456789,))
        self.assertTrue(settings.protect_content)

    def test_extra_admins_are_parsed(self):
        source = {**BASE, "ADMINS": "111, 222;333"}
        self.assertEqual(load_settings(source).admins, (111, 222, 333, 123456789))

    def test_missing_token_and_invalid_channel_fail(self):
        with self.assertRaisesRegex(ValueError, "BOT_TOKEN"):
            load_settings({k: v for k, v in BASE.items() if k != "BOT_TOKEN"})
        with self.assertRaisesRegex(ValueError, "beginning with -100"):
            load_settings({**BASE, "DB_CHANNEL": "-12345"})

    def test_no_database_dependencies_or_db_uri_variables(self):
        requirements = (ROOT / "requirements.txt").read_text().lower()
        self.assertNotIn("mongo", requirements)
        self.assertNotIn("pymongo", requirements)
        self.assertNotIn("motor", requirements)
        render = (ROOT / "render.yaml").read_text()
        self.assertNotIn("DB_URI", render)
        self.assertNotIn("DB_NAME", render)

    def test_render_blueprint_is_free_web_service(self):
        render = (ROOT / "render.yaml").read_text()
        self.assertIn("type: web", render)
        self.assertIn("plan: free", render)
        self.assertIn("healthCheckPath: /health", render)
        self.assertIn("startCommand: python main.py", render)

    def test_runtime_does_not_contain_database_calls_or_auto_delete(self):
        forbidden = ("mongodb", "pymongo", "motor", "AUTO_DEL", "delete_messages")
        for relative in ("bot.py", "main.py", "config.py", "helper/storage.py"):
            text = (ROOT / relative).read_text().lower()
            for term in forbidden:
                self.assertNotIn(term.lower(), text, f"{term} found in {relative}")


if __name__ == "__main__":
    unittest.main()
