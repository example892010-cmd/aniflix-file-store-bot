"""Ordering helpers for /getlink upload sessions.

Messages forwarded together into a user's private chat are separate Telegram
updates.  Their private-chat message IDs provide a stable send order, so the
bot must sort by those IDs before copying anything to the archive.
"""

from typing import Any, Dict, Iterable, List


def sort_upload_messages(messages: Iterable[Any]) -> List[Any]:
    """Return unique Telegram messages in the order they arrived in the chat."""
    by_id: Dict[int, Any] = {}
    for message in messages:
        if message is None or getattr(message, "empty", False):
            continue
        message_id = int(getattr(message, "id", 0) or 0)
        if message_id <= 0:
            continue
        by_id[message_id] = message
    return [by_id[message_id] for message_id in sorted(by_id)]


def ordered_archive_message_ids(archived_by_source_id: Dict[int, int]) -> List[int]:
    """Build archive IDs in original private-chat message order."""
    ordered_ids = []
    for source_id in sorted(archived_by_source_id):
        archive_id = int(archived_by_source_id[source_id])
        if archive_id <= 0:
            raise ValueError("Archive message IDs must be positive")
        ordered_ids.append(archive_id)
    return ordered_ids
