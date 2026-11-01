"""Telegram archive operations, including compact ordered batch manifests."""
from __future__ import annotations

import base64
import json

BATCH_MANIFEST_PREFIX = "ANIFLIX_BATCH_V1:"
MAX_BATCH_MESSAGES = 200


def encode_batch_manifest(message_ids) -> str:
    ids = [int(value) for value in message_ids]
    if not ids or len(ids) > MAX_BATCH_MESSAGES:
        raise ValueError(f"A batch must contain between 1 and {MAX_BATCH_MESSAGES} messages")
    if any(value <= 0 for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Batch message IDs must be unique positive integers")
    if ids != sorted(ids):
        raise ValueError("Batch message IDs must be in original order")
    data = json.dumps(ids, separators=(",", ":")).encode("ascii")
    encoded = base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
    text = BATCH_MANIFEST_PREFIX + encoded
    if len(text) > 3900:
        raise ValueError("Batch manifest is too large for Telegram")
    return text


def decode_batch_manifest(text: str) -> list[int]:
    if not isinstance(text, str) or not text.startswith(BATCH_MANIFEST_PREFIX):
        raise ValueError("This archive message is not a valid batch manifest")
    encoded = text[len(BATCH_MANIFEST_PREFIX):]
    if not encoded or len(encoded) > 3900:
        raise ValueError("Invalid batch manifest size")
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii"))
        ids = [int(value) for value in values]
    except Exception as exc:
        raise ValueError("The batch manifest is damaged") from exc
    if not ids or len(ids) > MAX_BATCH_MESSAGES:
        raise ValueError("The batch manifest has an invalid message count")
    if any(value <= 0 for value in ids) or len(set(ids)) != len(ids) or ids != sorted(ids):
        raise ValueError("The batch manifest contains invalid message IDs")
    return ids


async def archive_and_verify(client, source_message, archive_channel_id: int) -> int:
    stored = await source_message.copy(
        chat_id=archive_channel_id,
        disable_notification=True,
    )
    stored_id = getattr(stored, "id", None)
    if not stored_id:
        raise RuntimeError("Telegram did not return an archive message ID")

    verified = await client.get_messages(
        chat_id=archive_channel_id,
        message_ids=stored_id,
    )
    if not verified or getattr(verified, "empty", False) or getattr(verified, "id", None) != stored_id:
        raise RuntimeError("The copied archive message could not be verified")
    return int(stored_id)


async def save_batch_manifest(client, archive_channel_id: int, archived_message_ids) -> int:
    text = encode_batch_manifest(archived_message_ids)
    sent = await client.send_message(
        archive_channel_id,
        text,
        disable_notification=True,
        parse_mode=None,
    )
    manifest_id = int(sent.id)
    verified = await client.get_messages(archive_channel_id, manifest_id)
    verified_text = getattr(verified, "text", None) or ""
    if getattr(verified, "empty", False) or verified_text != text:
        raise RuntimeError("The batch manifest could not be read back and verified")
    decode_batch_manifest(verified_text)
    return manifest_id


async def deliver_archived_message(
    client,
    archive_channel_id: int,
    message_id: int,
    destination_chat_id: int,
    protect_content: bool = True,
):
    archived = await client.get_messages(
        chat_id=archive_channel_id,
        message_ids=message_id,
    )
    if not archived or getattr(archived, "empty", False) or getattr(archived, "id", None) != message_id:
        raise LookupError("That message is no longer available in the archive")
    return await archived.copy(
        chat_id=destination_chat_id,
        protect_content=protect_content,
    )


async def deliver_archived_batch(
    client,
    archive_channel_id: int,
    manifest_message_id: int,
    destination_chat_id: int,
    protect_content: bool = True,
) -> tuple[int, int]:
    manifest = await client.get_messages(archive_channel_id, manifest_message_id)
    if not manifest or getattr(manifest, "empty", False):
        raise LookupError("This batch link is no longer available")
    text = getattr(manifest, "text", None) or ""
    message_ids = decode_batch_manifest(text)
    delivered = 0
    missing = 0
    for archived_id in message_ids:
        try:
            await deliver_archived_message(
                client,
                archive_channel_id,
                archived_id,
                destination_chat_id,
                protect_content,
            )
            delivered += 1
        except LookupError:
            missing += 1
    return delivered, missing
