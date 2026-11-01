import base64
import struct
from typing import Tuple, Union


def _b64_url_encode(data: bytes) -> str:
    """Encodes bytes to an unpadded URL-safe base64 string."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64_url_decode(encoded: str) -> bytes:
    """Decodes an unpadded URL-safe base64 string."""
    padding = len(encoded) % 4
    if padding:
        encoded += "=" * (4 - padding)
    return base64.urlsafe_b64decode(encoded.encode("ascii"))


def encode_message_id(message_id: int) -> str:
    """
    Encodes a single archive message ID into a Telegram-safe URL payload.
    Supports both integer and stringified integer inputs.
    """
    mid = int(message_id)
    # Pack as unsigned 64-bit big-endian integer
    packed = struct.pack(">Q", mid).lstrip(b"\x00") or b"\x00"
    return _b64_url_encode(b"s" + packed)


def decode_message_id(payload: str) -> int:
    """
    Decodes a single archive message ID payload.
    Supports modern format, legacy prefixed formats, and direct base64 strings.
    """
    if not payload:
        raise ValueError("Empty payload")

    # Strip deep-link prefix if present
    if payload.startswith("file_"):
        payload = payload[5:]
    elif payload.startswith("msg_"):
        payload = payload[4:]

    try:
        raw = _b64_url_decode(payload)
        if raw.startswith(b"s"):
            raw_bytes = raw[1:]
            raw_padded = raw_bytes.rjust(8, b"\x00")
            return struct.unpack(">Q", raw_padded)[0]
        
        # Fallback 1: Direct big-endian packed bytes (up to 8 bytes)
        if len(raw) <= 8:
            return struct.unpack(">Q", raw.rjust(8, b"\x00"))[0]

        # Fallback 2: UTF-8 decoded integer string
        return int(raw.decode("utf-8").strip())
    except Exception:
        pass

    # Fallback 3: Direct numeric string
    if payload.isdigit():
        return int(payload)

    raise ValueError(f"Invalid single message ID payload: {payload}")


def encode_batch(first_id: int, last_id: int) -> str:
    """
    Encodes a start and end message ID into a Telegram-safe batch payload.
    Preserves start <= end ordering.
    """
    f_id = int(first_id)
    l_id = int(last_id)
    start_id = min(f_id, l_id)
    end_id = max(f_id, l_id)

    # Pack both IDs as unsigned 64-bit big-endian integers
    packed = struct.pack(">QQ", start_id, end_id)
    return _b64_url_encode(b"b" + packed)


def decode_batch(payload: str) -> Tuple[int, int]:
    """
    Decodes a batch link payload into (start_id, end_id).
    Supports modern batch format, colon/hyphen separated payloads, and legacy structures.
    """
    if not payload:
        raise ValueError("Empty batch payload")

    # Strip batch deep-link prefix if present
    if payload.startswith("batch_"):
        payload = payload[6:]

    try:
        raw = _b64_url_decode(payload)

        # Standard binary format prefixed with 'b'
        if raw.startswith(b"b") and len(raw) == 17:
            start_id, end_id = struct.unpack(">QQ", raw[1:])
            return start_id, end_id

        # Plain packed 16-byte binary format
        if len(raw) == 16:
            start_id, end_id = struct.unpack(">QQ", raw)
            return start_id, end_id

        # Text-based separated strings inside base64: "123:456" or "123-456"
        text = raw.decode("utf-8").strip()
        for sep in (":", "-", "_"):
            if sep in text:
                parts = text.split(sep, 1)
                return int(parts[0]), int(parts[1])
    except Exception:
        pass

    # Direct text separation fallback: "123_456" or "123-456"
    for sep in ("_", "-"):
        if sep in payload:
            parts = payload.split(sep, 1)
            if parts[0].isdigit() and parts[1].isdigit():
                return int(parts[0]), int(parts[1])

    raise ValueError(f"Invalid batch payload: {payload}")


# Backward compatibility aliases for existing codebase
encode_link = encode_message_id
decode_link = decode_message_id
encode_payload = encode_message_id
decode_payload = decode_message_id
