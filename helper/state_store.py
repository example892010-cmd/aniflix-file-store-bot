"""Telegram-channel-backed settings and user registry (no external database)."""
from __future__ import annotations

import base64
import json
import zlib
from copy import deepcopy

STATE_PREFIX = "ANIFLIX_STATE_V1:"
USER_PREFIX = "ANIFLIX_USER_V1:"


def _encode_json(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(raw, level=9)
    return base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")


def _decode_json(value: str) -> object:
    padded = value + "=" * (-len(value) % 4)
    compressed = base64.urlsafe_b64decode(padded.encode("ascii"))
    return json.loads(zlib.decompress(compressed).decode("utf-8"))


def default_state() -> dict:
    return {
        "welcome": {"kind": "text", "text": "Welcome to ANIFLIX FILE BOT!", "photo_id": None},
        "fsub_message": {
            "kind": "text",
            "text": "Join the below channels to continue, then press TRY AGAIN.",
            "photo_id": None,
        },
        "fsub_channels": [],
        "extra_admins": [],
        "global_paused": False,
        "links_count": {"getlink": 0, "batch": 0, "messages": 0, "failed": 0},
    }


class TelegramStateStore:
    """Persists small settings and user records as private archive-channel posts.

    The configured archive channel must be private and writable by the bot. This
    keeps settings and the user registry available across Render restarts without
    MongoDB or an ephemeral local file.
    """

    def __init__(self, client, archive_channel_id: int):
        self.client = client
        self.archive_channel_id = int(archive_channel_id)
        self.state = default_state()
        self.state_message_id: int | None = None
        self.users: dict[int, dict] = {}

    async def load(self) -> None:
        latest_state = None
        latest_state_id = None
        async for message in self.client.get_chat_history(self.archive_channel_id):
            text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
            if text.startswith(STATE_PREFIX) and latest_state is None:
                try:
                    decoded = _decode_json(text[len(STATE_PREFIX):])
                    if isinstance(decoded, dict):
                        latest_state = decoded
                        latest_state_id = message.id
                except Exception:
                    continue
            elif text.startswith(USER_PREFIX):
                try:
                    record = _decode_json(text[len(USER_PREFIX):])
                    user_id = int(record["id"])
                    if user_id > 0 and user_id not in self.users:
                        self.users[user_id] = record
                except Exception:
                    continue
        if latest_state is not None:
            merged = default_state()
            merged.update(latest_state)
            self.state = merged
            self.state_message_id = latest_state_id
        else:
            await self.save()

    async def save(self) -> None:
        encoded = _encode_json(self.state)
        text = STATE_PREFIX + encoded
        if len(text) > 3900:
            raise ValueError("Admin settings are too large to persist in one Telegram message")
        sent = await self.client.send_message(
            self.archive_channel_id,
            text,
            disable_notification=True,
            parse_mode="disabled",
        )
        new_id = int(sent.id)
        old_id = self.state_message_id
        self.state_message_id = new_id
        if old_id and old_id != new_id:
            try:
                await self.client.delete_messages(self.archive_channel_id, old_id)
            except Exception:
                # The newest state record wins during the next startup scan.
                pass

    async def register_user(self, user) -> bool:
        user_id = int(getattr(user, "id", 0) or 0)
        if user_id <= 0 or user_id in self.users:
            return False
        record = {
            "id": user_id,
            "first_name": (getattr(user, "first_name", "") or "")[:80],
            "last_name": (getattr(user, "last_name", "") or "")[:80],
            "username": (getattr(user, "username", "") or "")[:64],
        }
        marker = USER_PREFIX + _encode_json(record)
        if len(marker) > 1000:
            raise ValueError("User registry record is too large")
        await self.client.send_message(
            self.archive_channel_id,
            marker,
            disable_notification=True,
            parse_mode="disabled",
        )
        self.users[user_id] = record
        return True

    def add_user_record(self, record: dict) -> None:
        """Used by tests and startup restoration."""
        user_id = int(record.get("id", 0))
        if user_id > 0:
            self.users[user_id] = deepcopy(record)

    def admins(self, owner_id: int, configured_admins) -> set[int]:
        values = {int(owner_id), *(int(x) for x in configured_admins)}
        values.update(int(x) for x in self.state.get("extra_admins", []))
        return values

    async def add_admin(self, user_id: int, owner_id: int) -> bool:
        user_id = int(user_id)
        if user_id <= 0:
            raise ValueError("Admin ID must be a positive Telegram user ID")
        extras = set(int(x) for x in self.state.get("extra_admins", []))
        if user_id == int(owner_id) or user_id in extras:
            return False
        extras.add(user_id)
        self.state["extra_admins"] = sorted(extras)
        await self.save()
        return True

    async def remove_admin(self, user_id: int, owner_id: int, configured_admins) -> bool:
        user_id = int(user_id)
        if user_id == int(owner_id) or user_id in set(map(int, configured_admins)):
            raise ValueError("The owner and environment-configured admins cannot be removed here")
        extras = set(int(x) for x in self.state.get("extra_admins", []))
        if user_id not in extras:
            return False
        extras.remove(user_id)
        self.state["extra_admins"] = sorted(extras)
        await self.save()
        return True
