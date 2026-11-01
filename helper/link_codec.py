"""Stateless Telegram start-payload encoder/decoder for single and batch links."""
from __future__ import annotations

import base64
import re

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _base36(value: int) -> str:
    if value < 0:
        raise ValueError("Base36 values must be non-negative")
    if value == 0:
        return "0"
    result = []
    while value:
        value, remainder = divmod(value, 36)
        result.append(_ALPHABET[remainder])
    return "".join(reversed(result))


def _unbase36(value: str) -> int:
    value = value.lower()
    if not value or any(char not in _ALPHABET for char in value):
        raise ValueError("Invalid base36 value")
    result = 0
    for char in value:
        result = result * 36 + _ALPHABET.index(char)
    return result


def encode_text(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("ascii")).decode("ascii").rstrip("=")


def decode_text(payload: str) -> str:
    if not isinstance(payload, str) or not _PAYLOAD_RE.fullmatch(payload):
        raise ValueError("Invalid Telegram start payload")
    padded = payload + "=" * (-len(payload) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
    except Exception as exc:
        raise ValueError("Invalid encoded payload") from exc


def _validate_channel(channel_id: int) -> int:
    channel_id = int(channel_id)
    if channel_id >= 0 or not str(channel_id).startswith("-100"):
        raise ValueError("Archive channel ID must begin with -100")
    return channel_id


def encode_storage_payload(channel_id: int, message_id: int) -> str:
    channel_id = _validate_channel(channel_id)
    message_id = int(message_id)
    if message_id <= 0:
        raise ValueError("Message ID must be positive")
    payload = encode_text(f"v1.{_base36(abs(channel_id))}.{_base36(message_id)}")
    if len(payload) > 64:
        raise ValueError("Generated link exceeds Telegram's 64-character start-payload limit")
    return payload


def encode_batch_payload(channel_id: int, manifest_message_id: int) -> str:
    channel_id = _validate_channel(channel_id)
    manifest_message_id = int(manifest_message_id)
    if manifest_message_id <= 0:
        raise ValueError("Manifest message ID must be positive")
    payload = encode_text(f"b1.{_base36(abs(channel_id))}.{_base36(manifest_message_id)}")
    if len(payload) > 64:
        raise ValueError("Generated batch link exceeds Telegram's 64-character start-payload limit")
    return payload


def parse_link_payload(decoded: str, known_channel_id: int) -> tuple[str, int, int]:
    """Return (kind, channel_id, message_id); kind is 'single' or 'batch'."""
    known_channel_id = _validate_channel(known_channel_id)
    if decoded.startswith(("v1.", "b1.")):
        parts = decoded.split(".")
        if len(parts) != 3:
            raise ValueError("Malformed archive payload")
        kind = "batch" if parts[0] == "b1" else "single"
        channel_id = -_unbase36(parts[1])
        message_id = _unbase36(parts[2])
        if channel_id != known_channel_id:
            raise ValueError("This link does not belong to the configured archive channel")
    else:
        parts = decoded.split("-")
        if len(parts) != 2 or parts[0] != "get":
            raise ValueError("Unsupported archive link")
        try:
            encoded_id = int(parts[1])
        except ValueError as exc:
            raise ValueError("Malformed legacy archive link") from exc
        multiplier = abs(known_channel_id)
        if multiplier <= 0 or encoded_id % multiplier:
            raise ValueError("Legacy link does not match the configured archive channel")
        message_id = encoded_id // multiplier
        channel_id = known_channel_id
        kind = "single"
    if message_id <= 0:
        raise ValueError("Invalid archive message ID")
    return kind, channel_id, message_id


def parse_storage_payload(decoded: str, known_channel_id: int) -> tuple[int, int]:
    """Backward-compatible parser for single-file v1 and legacy links."""
    kind, channel_id, message_id = parse_link_payload(decoded, known_channel_id)
    if kind != "single":
        raise ValueError("This is a batch link, not a single-message link")
    return channel_id, message_id
