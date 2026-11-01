"""Telegram deep-link payload helpers with backwards-compatible decoding.

Existing single-message payloads (``s`` + packed message ID) and range-based
batch payloads (``b`` + two packed IDs) remain unchanged. New ordered batches
use a compact manifest reference (``m`` + packed manifest message ID).
"""

import base64
import struct
from typing import Optional, Tuple


_MAX_U64 = (1 << 64) - 1


def _pack_id(value: int) -> bytes:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Message ID must be an integer") from exc
    if number <= 0 or number > _MAX_U64:
        raise ValueError("Message ID must be a positive 64-bit integer")
    return struct.pack(">Q", number)


def _b64_url_encode(data: bytes) -> str:
    """Encode bytes as unpadded URL-safe base64."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64_url_decode(encoded: str) -> bytes:
    """Decode unpadded URL-safe base64, rejecting malformed characters."""
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Payload must be a non-empty string")
    try:
        raw = encoded.encode("ascii")
        padded = raw + b"=" * ((-len(raw)) % 4)
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("Payload is not valid URL-safe base64") from exc


def encode_message_id(message_id: int) -> str:
    """Encode one positive archive message ID using the established format."""
    packed = _pack_id(message_id).lstrip(b"\x00") or b"\x00"
    return _b64_url_encode(b"s" + packed)


def decode_message_id(payload: str) -> int:
    """Decode current and legacy single-message payload forms."""
    if not isinstance(payload, str) or not payload:
        raise ValueError("Empty payload")
    if payload.startswith("file_"):
        payload = payload[5:]
    elif payload.startswith("msg_"):
        payload = payload[4:]

    try:
        raw = _b64_url_decode(payload)
        if raw.startswith(b"s") and 1 <= len(raw[1:]) <= 8:
            number = struct.unpack(">Q", raw[1:].rjust(8, b"\x00"))[0]
            if number > 0:
                return number
        # Older payloads encoded the packed integer without a type byte.
        if 1 <= len(raw) <= 8:
            number = struct.unpack(">Q", raw.rjust(8, b"\x00"))[0]
            if number > 0:
                return number
        # Some historic links contained a base64-encoded decimal string.
        number = int(raw.decode("utf-8").strip())
        if number > 0:
            return number
    except (ValueError, UnicodeDecodeError, struct.error):
        pass

    # Also accept historic direct numeric payloads.
    if payload.isdigit() and int(payload) > 0:
        return int(payload)
    raise ValueError("Invalid single message ID payload")


def encode_batch(first_id: int, last_id: int) -> str:
    """Encode an inclusive legacy batch range without changing its format."""
    first = int(first_id)
    last = int(last_id)
    if first <= 0 or last <= 0:
        raise ValueError("Batch message IDs must be positive")
    start_id, end_id = sorted((first, last))
    return _b64_url_encode(b"b" + _pack_id(start_id) + _pack_id(end_id))


def decode_batch(payload: str) -> Tuple[int, int]:
    """Decode legacy range payloads, including previously supported text forms."""
    if not isinstance(payload, str) or not payload:
        raise ValueError("Empty batch payload")
    if payload.startswith("batch_"):
        payload = payload[6:]

    try:
        raw = _b64_url_decode(payload)
        if raw.startswith(b"b") and len(raw) == 17:
            start_id, end_id = struct.unpack(">QQ", raw[1:])
            if start_id > 0 and end_id >= start_id:
                return start_id, end_id
        if len(raw) == 16:
            start_id, end_id = struct.unpack(">QQ", raw)
            if start_id > 0 and end_id >= start_id:
                return start_id, end_id
        text = raw.decode("utf-8").strip()
        for separator in (":", "-", "_"):
            if separator in text:
                left, right = text.split(separator, 1)
                start_id, end_id = int(left), int(right)
                if start_id > 0 and end_id > 0:
                    return tuple(sorted((start_id, end_id)))
    except (ValueError, UnicodeDecodeError, struct.error):
        pass

    for separator in ("_", "-"):
        if separator in payload:
            left, right = payload.split(separator, 1)
            if left.isdigit() and right.isdigit() and int(left) > 0 and int(right) > 0:
                return tuple(sorted((int(left), int(right))))
    raise ValueError("Invalid batch payload")


def encode_batch_manifest_id(manifest_message_id: int) -> str:
    """Encode an archive manifest message ID for a new ordered batch link."""
    packed = _pack_id(manifest_message_id).lstrip(b"\x00") or b"\x00"
    return _b64_url_encode(b"m" + packed)


def decode_batch_manifest_id(payload: str) -> Optional[int]:
    """Return a manifest ID for the new ``m`` format, or ``None`` for legacy ranges."""
    if not isinstance(payload, str) or not payload:
        raise ValueError("Empty batch payload")
    if payload.startswith("batch_"):
        payload = payload[6:]
    try:
        raw = _b64_url_decode(payload)
    except ValueError:
        return None
    if not raw.startswith(b"m"):
        return None
    encoded_id = raw[1:]
    if not 1 <= len(encoded_id) <= 8:
        raise ValueError("Invalid batch manifest reference")
    manifest_id = struct.unpack(">Q", encoded_id.rjust(8, b"\x00"))[0]
    if manifest_id <= 0:
        raise ValueError("Invalid batch manifest reference")
    return manifest_id


# Backward compatibility aliases retained for projects importing these names.
encode_link = encode_message_id
decode_link = decode_message_id
encode_payload = encode_message_id
decode_payload = decode_message_id
