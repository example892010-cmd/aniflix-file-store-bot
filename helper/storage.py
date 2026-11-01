"""Archive-channel storage helpers and ordered batch manifests.

No helper in this module scans Telegram chat history. Retrieval uses explicit
message IDs only; this avoids the bot-forbidden ``messages.GetHistory`` API.
"""

import asyncio
import json
import logging
from typing import List, Optional, Sequence

from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message

logger = logging.getLogger(__name__)

BATCH_MANIFEST_PREFIX = "ANIFLIX_BATCH_V1:"
MAX_BATCH_MESSAGES = 200


def _message_from_result(result):
    if isinstance(result, (list, tuple)):
        return next((item for item in result if item and not getattr(item, "empty", False)), None)
    if result and not getattr(result, "empty", False):
        return result
    return None


async def _get_by_id(client, chat_id: int, message_id: int):
    try:
        result = await client.get_messages(chat_id=chat_id, message_ids=message_id)
    except TypeError:
        # Accommodate simple Pyrogram-compatible adapters using positional args.
        result = await client.get_messages(chat_id, message_id)
    return _message_from_result(result)


async def archive_and_verify(client: Client, source_message: Message, archive_channel_id: int) -> int:
    """Copy a message to the archive and verify that its exact ID is readable."""
    try:
        copied = await source_message.copy(chat_id=archive_channel_id, disable_notification=True)
    except FloodWait as wait:
        await asyncio.sleep(wait.value)
        copied = await source_message.copy(chat_id=archive_channel_id, disable_notification=True)
    if not copied or not getattr(copied, "id", None):
        raise RuntimeError("Archive copy returned no message ID")
    verified = await _get_by_id(client, archive_channel_id, copied.id)
    if not verified or int(getattr(verified, "id", 0)) != int(copied.id):
        raise RuntimeError(f"Archive message {copied.id} could not be verified")
    return int(copied.id)


async def deliver_archived_message(
    client: Client,
    archive_channel_id: int,
    message_id: int,
    destination_chat_id: int,
    protect_content: bool = False,
):
    """Read an explicit archive message ID and copy it to a destination chat."""
    archived = await _get_by_id(client, archive_channel_id, message_id)
    if not archived or int(getattr(archived, "id", 0)) != int(message_id):
        raise LookupError(f"Archive message {message_id} is no longer available")
    kwargs = {"chat_id": destination_chat_id}
    if protect_content:
        kwargs["protect_content"] = True
    try:
        return await archived.copy(**kwargs)
    except FloodWait as wait:
        await asyncio.sleep(wait.value)
        return await archived.copy(**kwargs)


def encode_batch_manifest(message_ids: Sequence[int]) -> str:
    """Serialize an ordered list of archive message IDs for a private archive record."""
    if not message_ids:
        raise ValueError("Batch manifest must contain at least one message ID")
    if len(message_ids) > MAX_BATCH_MESSAGES:
        raise ValueError(f"Batch manifest cannot exceed {MAX_BATCH_MESSAGES} messages")
    ids = [int(message_id) for message_id in message_ids]
    if any(message_id <= 0 for message_id in ids):
        raise ValueError("Batch manifest message IDs must be positive")
    if len(set(ids)) != len(ids):
        raise ValueError("Batch manifest message IDs must be unique")
    return BATCH_MANIFEST_PREFIX + json.dumps(ids, separators=(",", ":"))


def decode_batch_manifest(text: str) -> List[int]:
    """Validate and parse a batch manifest record."""
    if not isinstance(text, str) or not text.startswith(BATCH_MANIFEST_PREFIX):
        raise ValueError("Invalid batch manifest")
    try:
        ids = json.loads(text[len(BATCH_MANIFEST_PREFIX):])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid batch manifest") from exc
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_BATCH_MESSAGES:
        raise ValueError("Invalid batch manifest size")
    if any(type(item) is not int or item <= 0 for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("Invalid batch manifest message IDs")
    return ids


async def save_batch_manifest(client: Client, archive_channel_id: int, message_ids: Sequence[int]) -> int:
    """Store and verify a manifest record, returning its archive message ID."""
    text = encode_batch_manifest(message_ids)
    try:
        manifest = await client.send_message(
            archive_channel_id, text, disable_notification=True, parse_mode=None
        )
    except TypeError:
        manifest = await client.send_message(archive_channel_id, text)
    if not manifest or not getattr(manifest, "id", None):
        raise RuntimeError("Batch manifest was not saved to the archive")
    verified = await _get_by_id(client, archive_channel_id, manifest.id)
    if (
        not verified
        or int(getattr(verified, "id", 0)) != int(manifest.id)
        or getattr(verified, "text", None) != text
    ):
        raise RuntimeError("Batch manifest could not be verified in the archive")
    return int(manifest.id)


async def deliver_archived_batch(
    client: Client,
    archive_channel_id: int,
    manifest_message_id: int,
    destination_chat_id: int,
    protect_content: bool = False,
):
    """Deliver only the ordered IDs named by a verified manifest; return (sent, failed)."""
    manifest = await _get_by_id(client, archive_channel_id, manifest_message_id)
    if not manifest or int(getattr(manifest, "id", 0)) != int(manifest_message_id):
        raise LookupError(f"Batch manifest {manifest_message_id} is no longer available")
    ids = decode_batch_manifest(getattr(manifest, "text", None) or "")
    delivered = failed = 0
    for message_id in ids:
        try:
            await deliver_archived_message(
                client, archive_channel_id, message_id, destination_chat_id, protect_content
            )
            delivered += 1
        except Exception as exc:
            logger.warning("Could not deliver batch archive message %s: %s", message_id, exc)
            failed += 1
    return delivered, failed


class Storage:
    """Small object wrapper retained for existing bot call sites."""

    def __init__(self, client: Client, archive_channel_id: int):
        self.client = client
        self.archive_channel_id = archive_channel_id

    async def store_message(self, message: Message) -> Optional[Message]:
        try:
            message_id = await archive_and_verify(self.client, message, self.archive_channel_id)
            return await self.get_message(message_id)
        except Exception as exc:
            logger.error("Failed to store message in archive: %s", exc)
            return None

    async def get_message(self, message_id: int) -> Optional[Message]:
        try:
            return await _get_by_id(self.client, self.archive_channel_id, message_id)
        except RPCError as exc:
            logger.error("Failed to fetch message %s from archive: %s", message_id, exc)
            return None

    async def get_messages_range(self, start_id: int, end_id: int) -> List[Message]:
        first, last = sorted((int(start_id), int(end_id)))
        if first <= 0 or last - first + 1 > MAX_BATCH_MESSAGES:
            raise ValueError(f"Message range must contain at most {MAX_BATCH_MESSAGES} positive IDs")
        all_messages: List[Message] = []
        for message_id in range(first, last + 1):
            message = await self.get_message(message_id)
            if message:
                all_messages.append(message)
        return all_messages

    async def deliver_message(self, to_chat_id: int, message_id: int, protect_content: bool = False) -> bool:
        try:
            await deliver_archived_message(
                self.client, self.archive_channel_id, message_id, to_chat_id, protect_content
            )
            return True
        except Exception as exc:
            logger.error("Failed to deliver message %s to %s: %s", message_id, to_chat_id, exc)
            return False
