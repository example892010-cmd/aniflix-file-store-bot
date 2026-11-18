"""SQLite-configurable HTTP URL shortener helpers for ANIFLIX.

The remote provider follows the common /api?api=KEY&url=URL&alias=ALIAS
contract used by the FileStore-Yato reference project. Network calls are fully
async and always fall back by raising a clear exception to the caller; the bot
never silently exposes the original file deep link when shortening is enabled.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import string
from urllib.parse import urlparse

import aiohttp


ALIAS_CHARACTERS = string.ascii_letters + string.digits
SHORTENER_START_PREFIX = "as1_"


def normalize_shortener_domain(value: str) -> str:
    """Return a validated hostname (optionally supplied with an HTTPS scheme)."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Shortener domain cannot be empty.")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme.lower() != "https":
        raise ValueError("Shortener domain must use HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Enter a valid shortener domain, such as example.com.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Enter only the shortener domain, without a path or query.")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname):
        raise ValueError("Shortener domain contains unsupported characters.")
    if parsed.hostname.startswith(".") or parsed.hostname.endswith(".") or ".." in parsed.hostname:
        raise ValueError("Enter a valid shortener domain.")
    return parsed.hostname.lower()


def is_http_url(value: str) -> bool:
    try:
        parsed = urlparse((value or "").strip())
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
    except Exception:
        return False


def _random_alias(length: int = 8) -> str:
    return "".join(secrets.choice(ALIAS_CHARACTERS) for _ in range(length))


async def shorten_url(long_url: str, domain: str, api_key: str, *, timeout: float = 12.0) -> str:
    """Request a short URL from a provider compatible with FileStore-Yato."""
    normalized_domain = normalize_shortener_domain(domain)
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("Shortener API key is not configured.")
    if not is_http_url(long_url):
        raise ValueError("The destination URL is invalid.")

    endpoint = f"https://{normalized_domain}/api"
    params = {"api": api_key, "url": long_url, "alias": _random_alias()}
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(endpoint, params=params) as response:
                try:
                    payload = await response.json(content_type=None)
                except Exception as exc:
                    body = (await response.text())[:250]
                    raise RuntimeError(
                        f"Shortener returned an unreadable response (HTTP {response.status}): {body}"
                    ) from exc
                if response.status != 200:
                    detail = payload.get("message", "Provider request failed") if isinstance(payload, dict) else "Provider request failed"
                    raise RuntimeError(f"Shortener API HTTP {response.status}: {detail}")
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise RuntimeError(f"Could not contact the shortener API: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("status") != "success":
        detail = payload.get("message", "Provider did not return success") if isinstance(payload, dict) else "Invalid provider response"
        raise RuntimeError(str(detail))
    result = str(payload.get("shortenedUrl") or payload.get("short_url") or "").strip()
    if not is_http_url(result):
        raise RuntimeError("Shortener API did not return a valid shortened URL.")
    return result


def make_shortener_start_param(payload: str, signing_secret: str) -> str:
    """Sign a file payload so only URLs issued by this bot bypass one shortener hop."""
    payload = str(payload or "").strip()
    if not payload or not re.fullmatch(r"[A-Za-z0-9_-]+", payload):
        raise ValueError("The original Telegram start payload is invalid.")
    digest = hmac.new(
        str(signing_secret).encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha256,
    ).digest()[:9]
    # Hex alphabet contains no underscore, so the final separator remains unambiguous
    # even though ANIFLIX batch payloads themselves may contain underscores.
    signature = digest.hex()
    result = f"{SHORTENER_START_PREFIX}{payload}_{signature}"
    if len(result) > 64:
        raise ValueError("The verified Telegram start payload exceeds Telegram's 64-character limit.")
    return result


def unwrap_shortener_start_param(value: str, signing_secret: str) -> tuple[str, bool]:
    """Return (original_payload, signature_valid); ordinary payloads pass through."""
    value = str(value or "")
    if not value.startswith(SHORTENER_START_PREFIX):
        return value, False
    signed_part = value[len(SHORTENER_START_PREFIX):]
    try:
        payload, supplied_signature = signed_part.rsplit("_", 1)
        expected = make_shortener_start_param(payload, signing_secret).rsplit("_", 1)[1]
    except (ValueError, TypeError):
        return "", False
    return (payload, True) if hmac.compare_digest(supplied_signature, expected) else ("", False)
