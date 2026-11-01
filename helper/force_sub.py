"""Force-subscription membership helpers (kept independent of Pyrogram imports)."""
from __future__ import annotations

from typing import Any, Iterable


def status_name(member_or_status: Any) -> str:
    """Normalize Pyrogram enums and test doubles to a lowercase status name."""
    status = getattr(member_or_status, "status", member_or_status)
    value = getattr(status, "value", status)
    name = getattr(status, "name", None)
    normalized = str(value if value is not None else name or "").lower()
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[-1]
    return normalized


def is_channel_member(member: Any) -> bool:
    """Return True only for confirmed members, admins, owners, or restricted members."""
    status = status_name(member)
    if status in {"member", "administrator", "owner", "creator"}:
        return True
    if status == "restricted":
        return bool(getattr(member, "is_member", False))
    return False


def is_channel_admin(member: Any) -> bool:
    return status_name(member) in {"administrator", "owner", "creator"}


async def collect_unjoined_channels(client: Any, user_id: int,
                                    channels: Iterable[dict],
                                    logger: Any = None) -> list[dict]:
    """Return channels not confirmed joined; errors fail closed rather than bypassing FSUB."""
    unjoined = []
    for channel in channels:
        identifier = channel.get("id") or channel.get("username")
        if identifier is None:
            unjoined.append(channel)
            continue
        try:
            member = await client.get_chat_member(identifier, user_id)
            if not is_channel_member(member):
                unjoined.append(channel)
        except Exception as exc:
            # A missing admin permission or transient API error must never bypass FSUB.
            unjoined.append(channel)
            if logger:
                logger.warning("FSUB membership check failed for %s: %s", identifier, exc)
    return unjoined
