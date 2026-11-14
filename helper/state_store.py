"""Backward-compatible ANIFLIX state storage with a SQLite primary snapshot.

The existing pinned archive-channel state message remains a recovery/compatibility
copy. It is only edited or appended as a settings record; file archive messages
are never touched.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from pyrogram import Client
from pyrogram.errors import RPCError

from helper.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

STATE_TAG = "#ANIFLIX_STATE_STORE_V1#"
LOCAL_STATE_FILE = "bot_state_data.json"

DEFAULT_STATE: Dict[str, Any] = {
    "admins": [],
    "fsub_channels": [],
    "fsub_join_requests": {},
    "fsub_stats": {},
    "user_profiles": {},
    "fsub_message": None,
    "welcome_message": None,
    "stats": {
        "total_users": 0,
        "total_links": 0,
        "total_files": 0,
        "total_views": 0
    },
    "users": []
}


class StateStore:
    def __init__(self, client: Client, archive_channel_id: int,
                 sqlite_path: str = "data/aniflix.sqlite3"):
        self.client = client
        self.archive_channel_id = archive_channel_id
        self.database = SQLiteStore(sqlite_path)
        self.state: Dict[str, Any] = json.loads(json.dumps(DEFAULT_STATE))
        self.state_message_id: Optional[int] = None
        self.revision = 0

    def _merge_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        result = json.loads(json.dumps(DEFAULT_STATE))
        if isinstance(data, dict):
            clean = dict(data)
            clean.pop("_revision", None)
            for key, value in clean.items():
                if key == "stats" and isinstance(value, dict):
                    result["stats"].update(value)
                else:
                    result[key] = value
        return result

    def _load_local_state(self) -> bool:
        if not os.path.exists(LOCAL_STATE_FILE):
            return False
        try:
            with open(LOCAL_STATE_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.state = self._merge_defaults(data)
            self.state_message_id = data.get("_state_message_id")
            self.revision = max(self.revision, int(data.get("_revision", 0) or 0))
            logger.info("Loaded state from legacy local JSON cache.")
            return True
        except Exception as exc:
            logger.warning("Failed to read local state cache: %s", exc)
            return False

    def _save_local_state(self) -> None:
        try:
            to_save = dict(self.state)
            to_save["_state_message_id"] = self.state_message_id
            to_save["_revision"] = self.revision
            temporary_path = LOCAL_STATE_FILE + ".tmp"
            with open(temporary_path, "w", encoding="utf-8") as handle:
                json.dump(to_save, handle, ensure_ascii=False, indent=2)
            os.replace(temporary_path, LOCAL_STATE_FILE)
        except Exception as exc:
            logger.warning("Failed to write local state cache: %s", exc)

    async def load_state(self) -> None:
        """Load the newest known snapshot; old pinned state is imported once."""
        local_loaded = self._load_local_state()
        sqlite_loaded = False
        try:
            snapshot = self.database.load_state()
            if snapshot:
                data, revision = snapshot
                if not local_loaded or int(revision) >= self.revision:
                    self.state = self._merge_defaults(data)
                    self.revision = int(revision)
                sqlite_loaded = True
                logger.info("Loaded ANIFLIX state snapshot from SQLite.")
        except Exception:
            logger.exception("Could not load SQLite state snapshot.")

        if not self.archive_channel_id:
            logger.warning("No archive channel configured; using local/SQLite state only.")
            return

        remote_state = None
        remote_revision = -1
        try:
            chat = await self.client.get_chat(self.archive_channel_id)
            pinned = getattr(chat, "pinned_message", None)
            candidates = []
            if pinned and getattr(pinned, "text", None) and STATE_TAG in pinned.text:
                candidates.append(pinned)
            if self.state_message_id:
                try:
                    tracked = await self.client.get_messages(
                        self.archive_channel_id, self.state_message_id
                    )
                    if tracked and getattr(tracked, "text", None) and STATE_TAG in tracked.text:
                        if all(getattr(item, "id", None) != getattr(tracked, "id", None)
                               for item in candidates):
                            candidates.append(tracked)
                except Exception as exc:
                    logger.debug("Could not read tracked state message: %s", exc)

            for candidate in candidates:
                try:
                    raw = candidate.text.split(STATE_TAG, 1)[1].strip()
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        candidate_revision = int(data.get("_revision", 0) or 0)
                        if candidate_revision >= remote_revision:
                            remote_state = data
                            remote_revision = candidate_revision
                            self.state_message_id = candidate.id
                except (ValueError, TypeError, json.JSONDecodeError):
                    logger.warning("Ignoring malformed ANIFLIX state message %s.",
                                   getattr(candidate, "id", "?"))

        except RPCError as exc:
            logger.error("Telegram RPC error while loading archive state: %s", exc)
        except Exception:
            logger.exception("Unexpected error while loading archive state.")

        if remote_state is not None and remote_revision >= self.revision:
            self.state = self._merge_defaults(remote_state)
            self.revision = remote_revision
            logger.info("Loaded the newest state from the archive channel.")
            self._persist_snapshot()
        elif sqlite_loaded or local_loaded:
            logger.info("Keeping newer local/SQLite state; archive state is older or unavailable.")
            await self.save_state()
        else:
            raise RuntimeError(
                "No existing ANIFLIX state snapshot was found in SQLite, the legacy cache, "
                "or the archive channel. Refusing to initialize empty state because that "
                "could hide existing bot settings and user records. Restore the pinned "
                "ANIFLIX state message or provide its verified backup before starting."
            )

        self._save_local_state()

    def _persist_snapshot(self) -> None:
        self.database.save_state(self.state, self.revision)
        self._save_local_state()

    async def save_state(self) -> None:
        """Persist SQLite first, then local cache and the legacy Telegram state record."""
        self.revision += 1
        try:
            self._persist_snapshot()
        except Exception:
            logger.exception("Could not persist ANIFLIX state in SQLite.")
            self.revision -= 1
            return

        if not self.archive_channel_id:
            return

        remote_data = dict(self.state)
        remote_data["_revision"] = self.revision
        content = f"{STATE_TAG}\n" + json.dumps(remote_data, ensure_ascii=False, separators=(",", ":"))
        if len(content) > 4000:
            logger.error("State too large for one Telegram message; SQLite copy is preserved.")
            return

        try:
            if self.state_message_id:
                try:
                    await self.client.edit_message_text(
                        chat_id=self.archive_channel_id,
                        message_id=self.state_message_id,
                        text=content,
                        parse_mode=None,
                    )
                    return
                except RPCError as exc:
                    logger.warning("Could not edit tracked state message %s: %s",
                                   self.state_message_id, exc)

            message = await self.client.send_message(
                chat_id=self.archive_channel_id,
                text=content,
                parse_mode=None,
            )
            if not message or not getattr(message, "id", None):
                raise RuntimeError("Telegram did not return a state message ID")
            self.state_message_id = int(message.id)
            self._persist_snapshot()
            try:
                await self.client.pin_chat_message(
                    chat_id=self.archive_channel_id,
                    message_id=message.id,
                    both_sides=True,
                )
            except Exception as exc:
                logger.warning("Could not pin state message; check pin permissions: %s", exc)
        except Exception:
            logger.exception("Could not update Telegram's state recovery message.")

    def get(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        self.state[key] = value
        await self.save_state()

    def get_stats(self) -> Dict[str, int]:
        return self.state.get("stats", DEFAULT_STATE["stats"])

    async def update_stats(self, **kwargs) -> None:
        stats = self.state.setdefault("stats", dict(DEFAULT_STATE["stats"]))
        for key, value in kwargs.items():
            stats[key] = stats.get(key, 0) + value
        await self.save_state()

    def get_admins(self) -> list:
        return self.state.get("admins", [])

    async def add_admin(self, user_id: int) -> None:
        admins = {int(item) for item in self.state.get("admins", [])}
        admins.add(int(user_id))
        self.state["admins"] = sorted(admins)
        await self.save_state()

    async def remove_admin(self, user_id: int) -> None:
        admins = {int(item) for item in self.state.get("admins", [])}
        admins.discard(int(user_id))
        self.state["admins"] = sorted(admins)
        await self.save_state()

    def get_fsub_channels(self) -> list:
        return self.state.get("fsub_channels", [])

    async def set_fsub_channels(self, channels: list) -> None:
        self.state["fsub_channels"] = list(channels)[:10]
        await self.save_state()

    async def remove_fsub_channel(self, channel_id: int) -> None:
        """Remove only this channel's active config and channel-specific records."""
        key = str(int(channel_id))
        self.state["fsub_channels"] = [
            channel for channel in self.state.get("fsub_channels", [])
            if int(channel.get("id") or 0) != int(channel_id)
        ]
        self.state.setdefault("fsub_join_requests", {}).pop(key, None)
        self.state.setdefault("fsub_stats", {}).pop(key, None)
        await self.save_state()

    def has_fsub_join_request(self, channel_id: int, user_id: int) -> bool:
        requests = self.state.get("fsub_join_requests", {})
        users = requests.get(str(int(channel_id)), []) if isinstance(requests, dict) else []
        return int(user_id) in {int(item) for item in users}

    def _fsub_stat_users(self, channel_id: int, field: str) -> set:
        stats = self.state.setdefault("fsub_stats", {})
        channel_stats = stats.setdefault(str(int(channel_id)), {})
        return {int(item) for item in channel_stats.get(field, [])}

    async def record_fsub_join_request(self, channel_id: int, user_id: int) -> None:
        channel_id, user_id = int(channel_id), int(user_id)
        key = str(channel_id)
        requests = self.state.setdefault("fsub_join_requests", {})
        access_users = {int(item) for item in requests.get(key, [])}
        stat_users = self._fsub_stat_users(channel_id, "requests")
        changed = False
        if user_id not in access_users:
            access_users.add(user_id)
            requests[key] = sorted(access_users)
            changed = True
        if user_id not in stat_users:
            stat_users.add(user_id)
            self.state.setdefault("fsub_stats", {}).setdefault(key, {})["requests"] = sorted(stat_users)
            changed = True
        channel_stats = self.state.setdefault("fsub_stats", {}).setdefault(key, {})
        pending = {int(item) for item in channel_stats.get("pending", [])}
        if user_id not in pending:
            pending.add(user_id)
            channel_stats["pending"] = sorted(pending)
            changed = True
        closed = {int(item) for item in channel_stats.get("closed", [])}
        if user_id in closed:
            closed.discard(user_id)
            channel_stats["closed"] = sorted(closed)
            changed = True
        if changed:
            await self.save_state()

    async def record_fsub_pending(self, channel_id: int, user_id: int) -> None:
        """Backfill a pending statistic for requests imported from older state formats."""
        key = str(int(channel_id))
        user_id = int(user_id)
        channel_stats = self.state.setdefault("fsub_stats", {}).setdefault(key, {})
        requests = {int(item) for item in channel_stats.get("requests", [])}
        if user_id not in requests:
            return
        pending = {int(item) for item in channel_stats.get("pending", [])}
        accepted = {int(item) for item in channel_stats.get("accepted", [])}
        if user_id not in pending and user_id not in accepted:
            pending.add(user_id)
            channel_stats["pending"] = sorted(pending)
            await self.save_state()

    async def record_fsub_rejected_or_withdrawn(self, channel_id: int, user_id: int) -> None:
        """Record a request no longer pending and not accepted; never approve/decline it."""
        key = str(int(channel_id))
        user_id = int(user_id)
        channel_stats = self.state.setdefault("fsub_stats", {}).setdefault(key, {})
        requests = {int(item) for item in channel_stats.get("requests", [])}
        if user_id not in requests:
            return
        pending = {int(item) for item in channel_stats.get("pending", [])}
        resolved = {int(item) for item in channel_stats.get("closed", [])}
        changed = user_id in pending or user_id not in resolved
        pending.discard(user_id)
        resolved.add(user_id)
        channel_stats["pending"] = sorted(pending)
        channel_stats["closed"] = sorted(resolved)
        if changed:
            await self.save_state()

    async def record_fsub_member(self, channel_id: int, user_id: int, *, accepted: bool = False) -> None:
        channel_id, user_id = int(channel_id), int(user_id)
        key = str(channel_id)
        channel_stats = self.state.setdefault("fsub_stats", {}).setdefault(key, {})
        field = "accepted" if accepted else "joined"
        if accepted and user_id not in {int(item) for item in channel_stats.get("requests", [])}:
            return
        users = {int(item) for item in channel_stats.get(field, [])}
        changed = False
        if user_id not in users:
            users.add(user_id)
            channel_stats[field] = sorted(users)
            changed = True
        if accepted:
            pending = {int(item) for item in channel_stats.get("pending", [])}
            if user_id in pending:
                pending.discard(user_id)
                channel_stats["pending"] = sorted(pending)
                changed = True
            closed = {int(item) for item in channel_stats.get("closed", [])}
            if user_id in closed:
                closed.discard(user_id)
                channel_stats["closed"] = sorted(closed)
                changed = True
        if changed:
            await self.save_state()

    def get_fsub_counts(self, channel_id: int) -> Dict[str, int]:
        key = str(int(channel_id))
        stats = self.state.get("fsub_stats", {}).get(key, {})

        def unique_ids(field: str) -> set[int]:
            result = set()
            for item in stats.get(field, []):
                try:
                    result.add(int(item))
                except (TypeError, ValueError):
                    continue
            return result

        requests = unique_ids("requests")
        accepted = unique_ids("accepted") & requests
        return {
            "joined": len(unique_ids("joined")),
            "requests": len(requests),
            "accepted": len(accepted),
            "pending": len((unique_ids("pending") & requests) - accepted),
            "closed": len((unique_ids("closed") & requests) - accepted),
        }

    async def clear_fsub_counts(self, channel_id: int) -> None:
        """Clear display statistics only; keep request evidence used for verification."""
        self.state.setdefault("fsub_stats", {}).pop(str(int(channel_id)), None)
        await self.save_state()

    async def record_user(self, user_id: int, first_name: str = "",
                          last_name: str = "", username: str = "") -> None:
        user_id = int(user_id)
        users = {int(item) for item in self.state.get("users", [])}
        is_new = user_id not in users
        users.add(user_id)
        self.state["users"] = sorted(users)
        profiles = self.state.setdefault("user_profiles", {})
        profile = {
            "first_name": str(first_name or ""),
            "last_name": str(last_name or ""),
            "username": str(username or ""),
        }
        if profiles.get(str(user_id)) != profile or is_new:
            profiles[str(user_id)] = profile
            stats = self.state.setdefault("stats", dict(DEFAULT_STATE["stats"]))
            stats["total_users"] = len(users)
            await self.save_state()

    def close(self) -> None:
        self.database.close()
