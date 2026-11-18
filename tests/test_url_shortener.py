import unittest
from pathlib import Path
from unittest.mock import patch

import aiohttp

from helper.url_shortener import (
    is_http_url,
    make_shortener_start_param,
    normalize_shortener_domain,
    shorten_url,
    unwrap_shortener_start_param,
)


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return {"status": "success", "shortenedUrl": "https://sho.rt/abc123"}


class FakeSession:
    requested = None

    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, params=None):
        type(self).requested = (url, dict(params or {}))
        return FakeResponse()


class UrlShortenerTests(unittest.TestCase):
    def test_domain_normalization(self):
        self.assertEqual(normalize_shortener_domain("https://Example.com/"), "example.com")
        self.assertEqual(normalize_shortener_domain("example.com"), "example.com")

    def test_domain_rejects_http_and_paths(self):
        for value in ("http://example.com", "example.com/api", "", "https:///missing"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_shortener_domain(value)

    def test_http_url_validation(self):
        self.assertTrue(is_http_url("https://t.me/example"))
        self.assertFalse(is_http_url("t.me/example"))

    def test_signed_payload_round_trip_and_tamper_detection(self):
        signed = make_shortener_start_param("batch_abc123", "test-bot-token")
        self.assertLessEqual(len(signed), 64)
        self.assertEqual(
            unwrap_shortener_start_param(signed, "test-bot-token"),
            ("batch_abc123", True),
        )
        self.assertEqual(
            unwrap_shortener_start_param(signed + "x", "test-bot-token"),
            ("", False),
        )
        self.assertEqual(
            unwrap_shortener_start_param("batch_abc123", "test-bot-token"),
            ("batch_abc123", False),
        )

    def test_rejects_payload_that_cannot_fit_telegram_start_parameter(self):
        with self.assertRaises(ValueError):
            make_shortener_start_param("x" * 60, "test-bot-token")

    def test_provider_shortens_url_using_yato_api_contract(self):
        FakeSession.requested = None
        with patch.object(aiohttp, "ClientSession", FakeSession):
            import asyncio
            result = asyncio.run(
                shorten_url("https://t.me/aniflix?start=batch_123", "example.com", "api-key")
            )
        self.assertEqual(result, "https://sho.rt/abc123")
        url, params = FakeSession.requested
        self.assertEqual(url, "https://example.com/api")
        self.assertEqual(params["api"], "api-key")
        self.assertEqual(params["url"], "https://t.me/aniflix?start=batch_123")
        self.assertRegex(params["alias"], r"^[A-Za-z0-9]{8}$")


    def test_admin_panel_wires_all_url_shortener_controls(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "bot.py").read_text(encoding="utf-8")
        for callback in (
            "adm_shortener", "adm_shortener_toggle", "adm_shortener_credentials",
            "adm_shortener_tutorial", "adm_shortener_test",
            "adm_shortener_add_bypass", "adm_shortener_remove_bypass",
        ):
            with self.subTest(callback=callback):
                self.assertIn(callback, source)
        self.assertIn('InlineKeyboardButton("🔗 URL Shortener", callback_data="adm_shortener")', source)
        self.assertIn('filters.command(["shortner", "shortener"])', source)

    def test_start_and_fsub_retry_use_the_same_shortener_gate(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "bot.py").read_text(encoding="utf-8")
        self.assertIn("return await _process_start_payload(client, user_id, message, param)", source)
        self.assertIn("return await _process_start_payload(client, user_id, query.message, param)", source)
        self.assertIn("return await _deliver_start_payload(client, user_id, message, original_param)", source)

    def test_missing_api_key_fails_before_network(self):
        import asyncio
        with self.assertRaisesRegex(ValueError, "API key"):
            asyncio.run(shorten_url("https://example.com", "example.com", ""))


if __name__ == "__main__":
    unittest.main()
