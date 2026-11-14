
"""Archive-channel storage helpers and ordered batch manifests.

All delivery deletion jobs and batch cancellation records are stored in SQLite.
Archive files and manifests are never scheduled for deletion.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import List, Optional, Sequence

from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message

logger = logging.getLogger(__name__)

BATCH_MANIFEST_PREFIX = "ANIFLIX_BATCH_V1:"
AUTO_BATCH_MANIFEST_PREFIX = "ANIFLIX_AUTO_BATCH_V1:"
AUTO_MANIFEST_FANOUT = 150
MAX_BATCH_MESSAGES = 200
MAX_TELEGRAM_RETRIES = 4


def _message_from_result(result):
    if isinstance(result, (list, tuple)):
        return next(
            (
                item for item in result
                if item and not getattr(item, "empty", False)
            ),
            None,
        )

    if result and not getattr(result, "empty", False):
        return result

    return None


async def _get_by_id(client, chat_id: int, message_id: int):
    try:
        result = await client.get_messages(
            chat_id=chat_id,
            message_ids=message_id,
        )
    except TypeError:
        result = await client.get_messages(chat_id, message_id)

    return _message_from_result(result)


def _schedule_auto_delete(database, delivered_message) -> None:
    """Schedule only a successfully delivered user-chat message in SQLite."""
    if database is None or delivered_message is None:
        return

    try:
        chat = getattr(delivered_message, "chat", None)
        chat_id = getattr(chat, "id", None)
        message_id = getattr(delivered_message, "id", None)

        if chat_id is None or message_id is None:
            logger.warning(
                "Delivered message has no chat/message ID; "
                "auto-delete was not scheduled."
            )
            return

        minutes = database.get_auto_delete_minutes()
        delete_at = time.time() + (int(minutes) * 60)

        database.schedule_message_deletion(
            chat_id=int(chat_id),
            message_id=int(message_id),
            delete_at=delete_at,
        )

    except Exception:
        # Delivery has already succeeded. Do not report it as failed
        # just because scheduling encountered a database error.
        logger.exception(
            "Could not schedule auto-delete for a delivered message."
        )


async def archive_and_verify(
    client: Client,
    source_message: Message,
    archive_channel_id: int,
    *,
    return_message: bool = False,
):
    """Copy a message to the archive and verify its exact ID is readable."""
    copied = None

    for attempt in range(MAX_TELEGRAM_RETRIES):
        try:
            copied = await source_message.copy(
                chat_id=archive_channel_id,
                disable_notification=True,
            )
            break

        except FloodWait as wait:
            delay = max(1, int(getattr(wait, "value", 1) or 1))
            logger.warning(
                "Telegram FloodWait while archiving; sleeping %s seconds.",
                delay,
            )
            await asyncio.sleep(delay)

        except (TimeoutError, ConnectionError, OSError):
            if attempt + 1 >= MAX_TELEGRAM_RETRIES:
                raise

            await asyncio.sleep(min(2 ** attempt, 8))

    if copied is None:
        raise RuntimeError(
            "Archive copy did not return a message after retries."
        )

    if not copied or not getattr(copied, "id", None):
        raise RuntimeError("Archive copy returned no message ID.")

    verified = await _get_by_id(
        client,
        archive_channel_id,
        copied.id,
    )

    if (
        not verified
        or int(getattr(verified, "id", 0)) != int(copied.id)
    ):
        raise RuntimeError(
            f"Archive message {copied.id} could not be verified."
        )

    return verified if return_message else int(copied.id)


async def deliver_archived_message(
    client: Client,
    archive_channel_id: int,
    message_id: int,
    destination_chat_id: int,
    protect_content: bool = False,
    database=None,
):
    """Deliver an archive message and schedule its own deletion timer."""
    archived = await _get_by_id(
        client,
        archive_channel_id,
        message_id,
    )

    if (
        not archived
        or int(getattr(archived, "id", 0)) != int(message_id)
    ):
        raise LookupError(
            f"Archive message {message_id} is no longer available."
        )

    kwargs = {"chat_id": destination_chat_id}

    if protect_content:
        kwargs["protect_content"] = True

    for attempt in range(MAX_TELEGRAM_RETRIES):
        try:
            delivered = await archived.copy(**kwargs)

            # The timer starts when this individual message is delivered.
            _schedule_auto_delete(database, delivered)

            return delivered

        except FloodWait as wait:
            delay = max(1, int(getattr(wait, "value", 1) or 1))
            logger.warning(
                "Telegram FloodWait while delivering archive message %s; "
                "sleeping %s seconds.",
                message_id,
                delay,
            )
            await asyncio.sleep(delay)

        except (TimeoutError, ConnectionError, OSError):
            if attempt + 1 >= MAX_TELEGRAM_RETRIES:
                raise

            await asyncio.sleep(min(2 ** attempt, 8))

    raise RuntimeError(
        f"Could not deliver archive message {message_id} after retries."
    )


def encode_batch_manifest(message_ids: Sequence[int]) -> str:
    """Serialize an ordered list of archive message IDs."""
    if not message_ids:
        raise ValueError(
            "Batch manifest must contain at least one message ID."
        )

    if len(message_ids) > MAX_BATCH_MESSAGES:
        raise ValueError(
            f"Batch manifest cannot exceed {MAX_BATCH_MESSAGES} messages."
        )

    ids = [int(message_id) for message_id in message_ids]

    if any(message_id <= 0 for message_id in ids):
        raise ValueError(
            "Batch manifest message IDs must be positive."
        )

    if len(set(ids)) != len(ids):
        raise ValueError(
            "Batch manifest message IDs must be unique."
        )

    return BATCH_MANIFEST_PREFIX + json.dumps(
        ids,
        separators=(",", ":"),
    )


def decode_batch_manifest(text: str) -> List[int]:
    """Validate and parse a batch manifest record."""
    if (
        not isinstance(text, str)
        or not text.startswith(BATCH_MANIFEST_PREFIX)
    ):
        raise ValueError("Invalid batch manifest.")

    try:
        ids = json.loads(
            text[len(BATCH_MANIFEST_PREFIX):]
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid batch manifest.") from exc

    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= MAX_BATCH_MESSAGES
    ):
        raise ValueError("Invalid batch manifest size.")

    if (
        any(type(item) is not int or item <= 0 for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Invalid batch manifest message IDs.")

    return ids


async def save_batch_manifest(
    client: Client,
    archive_channel_id: int,
    message_ids: Sequence[int],
) -> int:
    """Store and verify a manifest record."""
    text = encode_batch_manifest(message_ids)

    try:
        manifest = await client.send_message(
            archive_channel_id,
            text,
            disable_notification=True,
            parse_mode=None,
        )
    except TypeError:
        manifest = await client.send_message(
            archive_channel_id,
            text,
        )

    if not manifest or not getattr(manifest, "id", None):
        raise RuntimeError(
            "Batch manifest was not saved to the archive."
        )

    verified = await _get_by_id(
        client,
        archive_channel_id,
        manifest.id,
    )

    if (
        not verified
        or int(getattr(verified, "id", 0)) != int(manifest.id)
        or getattr(verified, "text", None) != text
    ):
        raise RuntimeError(
            "Batch manifest could not be verified in the archive."
        )

    return int(manifest.id)


async def save_auto_batch_manifest(
    client: Client,
    archive_channel_id: int,
    message_ids: Sequence[int],
) -> int:
    """Save a large ordered batch as a tree of verified manifests."""
    ids = [int(item) for item in message_ids]

    if (
        not ids
        or any(item <= 0 for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError(
            "Auto batch requires unique positive archive message IDs."
        )

    level = []

    for offset in range(0, len(ids), AUTO_MANIFEST_FANOUT):
        level.append(
            await save_batch_manifest(
                client,
                archive_channel_id,
                ids[offset:offset + AUTO_MANIFEST_FANOUT],
            )
        )

    while len(level) > 1:
        next_level = []

        for offset in range(0, len(level), AUTO_MANIFEST_FANOUT):
            children = level[
                offset:offset + AUTO_MANIFEST_FANOUT
            ]

            text = AUTO_BATCH_MANIFEST_PREFIX + json.dumps(
                children,
                separators=(",", ":"),
            )

            try:
                node = await client.send_message(
                    archive_channel_id,
                    text,
                    disable_notification=True,
                    parse_mode=None,
                )
            except TypeError:
                node = await client.send_message(
                    archive_channel_id,
                    text,
                )

            if not node or not getattr(node, "id", None):
                raise RuntimeError(
                    "Auto batch index was not saved to the archive."
                )

            verified = await _get_by_id(
                client,
                archive_channel_id,
                node.id,
            )

            if (
                not verified
                or getattr(verified, "text", None) != text
            ):
                raise RuntimeError(
                    "Auto batch index could not be verified."
                )

            next_level.append(int(node.id))

        level = next_level

    return level[0]


async def _expand_auto_manifest(
    client: Client,
    archive_channel_id: int,
    manifest_id: int,
    depth: int = 0,
):
    if depth > 12:
        raise ValueError(
            "Auto batch manifest nesting is invalid."
        )

    manifest = await _get_by_id(
        client,
        archive_channel_id,
        manifest_id,
    )

    if (
        not manifest
        or int(getattr(manifest, "id", 0)) != int(manifest_id)
    ):
        raise LookupError(
            f"Batch manifest {manifest_id} is no longer available."
        )

    text = getattr(manifest, "text", None) or ""

    if text.startswith(AUTO_BATCH_MANIFEST_PREFIX):
        try:
            children = json.loads(
                text[len(AUTO_BATCH_MANIFEST_PREFIX):]
            )
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid auto batch index.") from exc

        if (
            not isinstance(children, list)
            or not children
            or len(children) > AUTO_MANIFEST_FANOUT
        ):
            raise ValueError("Invalid auto batch index.")

        expanded = []

        for child in children:
            if type(child) is not int or child <= 0:
                raise ValueError(
                    "Invalid auto batch child reference."
                )

            expanded.extend(
                await _expand_auto_manifest(
                    client,
                    archive_channel_id,
                    child,
                    depth + 1,
                )
            )

        return expanded

    return decode_batch_manifest(text)


async def deliver_archived_batch(
    client: Client,
    archive_channel_id: int,
    manifest_message_id: int,
    destination_chat_id: int,
    protect_content: bool = False,
    database=None,
    delivery_id: Optional[str] = None,
):
    """Deliver a manifest batch, optionally tracking cancellation in SQLite."""
    root = await _get_by_id(
        client,
        archive_channel_id,
        manifest_message_id,
    )

    if (
        not root
        or int(getattr(root, "id", 0)) != int(manifest_message_id)
    ):
        raise LookupError(
            f"Batch manifest {manifest_message_id} is no longer available."
        )

    root_text = getattr(root, "text", None) or ""

    if root_text.startswith(AUTO_BATCH_MANIFEST_PREFIX):
        ids = await _expand_auto_manifest(
            client,
            archive_channel_id,
            manifest_message_id,
        )
    else:
        ids = decode_batch_manifest(root_text)

    if database is not None and delivery_id is None:
        delivery_id = uuid.uuid4().hex

    if database is not None and delivery_id:
        try:
            database.start_batch_delivery(
                delivery_id=delivery_id,
                user_id=int(destination_chat_id),
                chat_id=int(destination_chat_id),
                manifest_id=int(manifest_message_id),
            )
        except Exception:
            logger.exception(
                "Could not create SQLite batch delivery record."
            )

    delivered = 0
    failed = 0
    cancelled = False

    for message_id in ids:
        # Check cancellation before beginning each next delivery.
        if database is not None and delivery_id:
            try:
                if database.is_batch_cancel_requested(delivery_id):
                    cancelled = True
                    break
            except Exception:
                logger.exception(
                    "Could not check batch cancellation state."
                )

        try:
            await deliver_archived_message(
                client,
                archive_channel_id,
                message_id,
                destination_chat_id,
                protect_content,
                database=database,
            )
            delivered += 1

        except Exception as exc:
            logger.warning(
                "Could not deliver batch archive message %s: %s",
                message_id,
                exc,
            )
            failed += 1

        if database is not None and delivery_id:
            try:
                database.update_batch_delivery(
                    delivery_id,
                    sent_count=delivered,
                    failed_count=failed,
                    status="running",
                )
            except Exception:
                logger.exception(
                    "Could not update SQLite batch delivery progress."
                )

        await asyncio.sleep(0.1)

    if database is not None and delivery_id:
        try:
            database.update_batch_delivery(
                delivery_id,
                sent_count=delivered,
                failed_count=failed,
                status="cancelled" if cancelled else "completed",
            )
        except Exception:
            logger.exception(
                "Could not finalize SQLite batch delivery record."
            )

    return delivered, failed


class Storage:
    """Archive operations with SQLite metadata and retry-safe deduplication."""

    MEDIA_FIELDS = (
        "document",
        "video",
        "audio",
        "photo",
        "animation",
        "voice",
        "video_note",
        "sticker",
    )

    def __init__(
        self,
        client: Client,
        archive_channel_id: int,
        database=None,
        max_concurrent_archives: int = 2,
    ):
        self.client = client
        self.archive_channel_id = archive_channel_id
        self.database = database

        self._archive_semaphore = asyncio.Semaphore(
            max(1, min(4, int(max_concurrent_archives)))
        )

    def _file_metadata(self, message: Message) -> dict:
        media = None
        media_type = ""

        for field in self.MEDIA_FIELDS:
            candidate = getattr(message, field, None)

            if candidate:
                media = candidate
                media_type = field
                break

        return {
            "media_type": media_type or str(
                getattr(message, "media", "") or ""
            ),
            "file_unique_id": (
                getattr(media, "file_unique_id", "")
                if media else ""
            ),
            "file_name": (
                getattr(media, "file_name", "")
                if media else ""
            ),
            "file_size": (
                getattr(media, "file_size", 0)
                if media else 0
            ),
            "caption": getattr(message, "caption", None) or "",
            "uploader_id": getattr(
                getattr(message, "from_user", None),
                "id",
                None,
            ),
        }

    async def store_message(
        self,
        message: Message,
    ) -> Optional[Message]:
        async with self._archive_semaphore:
            source_chat = getattr(message, "chat", None)
            source_chat_id = getattr(source_chat, "id", None)

            if source_chat_id is None:
                source_chat_id = getattr(
                    getattr(message, "from_user", None),
                    "id",
                    None,
                )

            source_message_id = int(
                getattr(message, "id", 0) or 0
            )

            # Reuse a previously verified archive copy on retries.
            if (
                self.database
                and source_chat_id is not None
                and source_message_id > 0
            ):
                try:
                    existing = self.database.find_file(
                        source_chat_id,
                        source_message_id,
                    )

                    if existing:
                        archived = await self.get_message(
                            int(existing["archive_message_id"])
                        )

                        if archived:
                            return archived

                except Exception:
                    logger.exception(
                        "Could not check prior archive record; "
                        "continuing with archive operation."
                    )

            try:
                archived = await archive_and_verify(
                    self.client,
                    message,
                    self.archive_channel_id,
                    return_message=True,
                )

                archive_message_id = int(archived.id)

            except Exception as exc:
                logger.error(
                    "Failed to store message in archive: %s",
                    exc,
                )
                return None

            # Index only after the Telegram archive copy is verified.
            if (
                self.database
                and source_chat_id is not None
                and source_message_id > 0
            ):
                try:
                    metadata = self._file_metadata(message)

                    self.database.record_file(
                        source_chat_id=source_chat_id,
                        source_message_id=source_message_id,
                        archive_chat_id=self.archive_channel_id,
                        archive_message_id=archive_message_id,
                        **metadata,
                    )

                except Exception:
                    logger.exception(
                        "Archived file %s but could not index its "
                        "SQLite metadata.",
                        archive_message_id,
                    )

            return archived

    async def get_message(
        self,
        message_id: int,
    ) -> Optional[Message]:
        try:
            return await _get_by_id(
                self.client,
                self.archive_channel_id,
                message_id,
            )

        except RPCError as exc:
            logger.error(
                "Failed to fetch message %s from archive: %s",
                message_id,
                exc,
            )
            return None

    async def get_messages_range(
        self,
        start_id: int,
        end_id: int,
    ) -> List[Message]:
        first, last = sorted(
            (int(start_id), int(end_id))
        )

        if (
            first <= 0
            or last - first + 1 > MAX_BATCH_MESSAGES
        ):
            raise ValueError(
                "Message range must contain at most "
                f"{MAX_BATCH_MESSAGES} positive IDs."
            )

        all_messages: List[Message] = []

        for message_id in range(first, last + 1):
            message = await self.get_message(message_id)

            if message:
                all_messages.append(message)

        return all_messages

    async def deliver_message(
        self,
        to_chat_id: int,
        message_id: int,
        protect_content: bool = False,
    ) -> bool:
        try:
            await deliver_archived_message(
                self.client,
                self.archive_channel_id,
                message_id,
                to_chat_id,
                protect_content,
                database=self.database,
            )

            return True

        except Exception as exc:
            logger.error(
                "Failed to deliver message %s to %s: %s",
                message_id,
                to_chat_id,
                exc,
            )
            return False
