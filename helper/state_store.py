
"""ANIFLIX state management using SQLite only.

All persistent bot state is stored in the SQLite database.
No JSON files, Telegram state messages, MongoDB or Redis are used.

JSON serialization is used only inside SQLite TEXT columns.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from pyrogram import Client

from helper.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

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
        "total_views": 0,
    },
    "users": [],
}


class StateStore:
    def __init__(
        self,
        client: Client,
        archive_channel_id: int,
        sqlite_path: str = "data/aniflix.sqlite3",
    ):
        # Retained for compatibility with the existing bot constructor.
        # State is NOT stored in Telegram.
        self.client = client
        self.archive_channel_id = archive_channel_id

        self.database = SQLiteStore(sqlite_path)
        self.state: Dict[str, Any] = json.loads(
            json.dumps(DEFAULT_STATE)
        )
        self.state_message_id: Optional[int] = None
        self.revision = 0

    def _merge_defaults(
        self,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = json.loads(json.dumps(DEFAULT_STATE))

        if isinstance(data, dict):
            clean = dict(data)
            clean.pop("_revision", None)
            clean.pop("_state_message_id", None)

            for key, value in clean.items():
                if key == "stats" and isinstance(value, dict):
                    result["stats"].update(value)
                else:
                    result[key] = value

        return result

    async def load_state(self) -> None:
        """Load state exclusively from SQLite."""
        try:
            snapshot = self.database.load_state()

            if snapshot:
                data, revision = snapshot

                if not isinstance(data, dict):
                    raise RuntimeError(
                        "SQLite state snapshot is invalid."
                    )

                self.state = self._merge_defaults(data)
                self.revision = int(revision)

                logger.info(
                    "Loaded ANIFLIX state from SQLite (revision %s).",
                    self.revision,
                )
                return

            # Do not silently create empty state if an existing installation
            # has not yet been migrated into SQLite.
            raise RuntimeError(
                "No ANIFLIX state snapshot exists in SQLite. "
                "Startup stopped to prevent losing existing bot settings. "
                "Import the existing state into SQLite before starting."
            )

        except Exception:
            logger.exception(
                "Could not load ANIFLIX state from SQLite."
            )
            raise

    async def save_state(self) -> None:
        """Save the complete bot state to SQLite."""
        new_revision = self.revision + 1

        try:
            self.database.save_state(
                self.state,
                new_revision,
            )
            self.revision = new_revision

        except Exception:
            logger.exception(
                "Could not save ANIFLIX state to SQLite."
            )
            raise

    def get(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        return self.state.get(key, default)

    async def set(
        self,
        key: str,
        value: Any,
    ) -> None:
        self.state[key] = value
        await self.save_state()

    def get_stats(self) -> Dict[str, int]:
        return self.state.get(
            "stats",
            dict(DEFAULT_STATE["stats"]),
        )

    async def update_stats(self, **kwargs) -> None:
        stats = self.state.setdefault(
            "stats",
            dict(DEFAULT_STATE["stats"]),
        )

        for key, value in kwargs.items():
            stats[key] = stats.get(key, 0) + value

        await self.save_state()

    def get_admins(self) -> list:
        return self.state.get("admins", [])

    async def add_admin(self, user_id: int) -> None:
        admins = {
            int(item)
            for item in self.state.get("admins", [])
        }

        admins.add(int(user_id))
        self.state["admins"] = sorted(admins)

        await self.save_state()

    async def remove_admin(self, user_id: int) -> None:
        admins = {
            int(item)
            for item in self.state.get("admins", [])
        }

        admins.discard(int(user_id))
        self.state["admins"] = sorted(admins)

        await self.save_state()

    def get_fsub_channels(self) -> list:
        return self.state.get("fsub_channels", [])

    async def set_fsub_channels(self, channels: list) -> None:
        self.state["fsub_channels"] = list(channels)[:10]
        await self.save_state()

    async def remove_fsub_channel(
        self,
        channel_id: int,
    ) -> None:
        key = str(int(channel_id))

        self.state["fsub_channels"] = [
            channel
            for channel in self.state.get("fsub_channels", [])
            if int(channel.get("id") or 0) != int(channel_id)
        ]

        self.state.setdefault(
            "fsub_join_requests", {}
        ).pop(key, None)

        self.state.setdefault(
            "fsub_stats", {}
        ).pop(key, None)

        await self.save_state()

    def has_fsub_join_request(
        self,
        channel_id: int,
        user_id: int,
    ) -> bool:
        requests = self.state.get(
            "fsub_join_requests",
            {},
        )

        users = (
            requests.get(str(int(channel_id)), [])
            if isinstance(requests, dict)
            else []
        )

        return int(user_id) in {
            int(item) for item in users
        }

    def _fsub_stat_users(
        self,
        channel_id: int,
        field: str,
    ) -> set:
        stats = self.state.setdefault(
            "fsub_stats",
            {},
        )

        channel_stats = stats.setdefault(
            str(int(channel_id)),
            {},
        )

        return {
            int(item)
            for item in channel_stats.get(field, [])
        }

    async def record_fsub_join_request(
        self,
        channel_id: int,
        user_id: int,
    ) -> None:
        channel_id = int(channel_id)
        user_id = int(user_id)
        key = str(channel_id)

        requests = self.state.setdefault(
            "fsub_join_requests",
            {},
        )

        access_users = {
            int(item)
            for item in requests.get(key, [])
        }

        stat_users = self._fsub_stat_users(
            channel_id,
            "requests",
        )

        changed = False

        if user_id not in access_users:
            access_users.add(user_id)
            requests[key] = sorted(access_users)
            changed = True

        if user_id not in stat_users:
            stat_users.add(user_id)

            self.state.setdefault(
                "fsub_stats",
                {},
            ).setdefault(key, {})["requests"] = sorted(
                stat_users
            )

            changed = True

        channel_stats = self.state.setdefault(
            "fsub_stats",
            {},
        ).setdefault(key, {})

        pending = {
            int(item)
            for item in channel_stats.get("pending", [])
        }

        if user_id not in pending:
            pending.add(user_id)
            channel_stats["pending"] = sorted(pending)
            changed = True

        closed = {
            int(item)
            for item in channel_stats.get("closed", [])
        }

        if user_id in closed:
            closed.discard(user_id)
            channel_stats["closed"] = sorted(closed)
            changed = True

        if changed:
            await self.save_state()

    async def record_fsub_pending(
        self,
        channel_id: int,
        user_id: int,
    ) -> None:
        """Backfill pending statistics for older request records."""
        key = str(int(channel_id))
        user_id = int(user_id)

        channel_stats = self.state.setdefault(
            "fsub_stats",
            {},
        ).setdefault(key, {})

        requests = {
            int(item)
            for item in channel_stats.get("requests", [])
        }

        if user_id not in requests:
            return

        pending = {
            int(item)
            for item in channel_stats.get("pending", [])
        }

        accepted = {
            int(item)
            for item in channel_stats.get("accepted", [])
        }

        if user_id not in pending and user_id not in accepted:
            pending.add(user_id)
            channel_stats["pending"] = sorted(pending)
            await self.save_state()

    async def record_fsub_rejected_or_withdrawn(
        self,
        channel_id: int,
        user_id: int,
    ) -> None:
        """Record a request that is no longer pending."""
        key = str(int(channel_id))
        user_id = int(user_id)

        channel_stats = self.state.setdefault(
            "fsub_stats",
            {},
        ).setdefault(key, {})

        requests = {
            int(item)
            for item in channel_stats.get("requests", [])
        }

        if user_id not in requests:
            return

        pending = {
            int(item)
            for item in channel_stats.get("pending", [])
        }

        resolved = {
            int(item)
            for item in channel_stats.get("closed", [])
        }

        changed = (
            user_id in pending
            or user_id not in resolved
        )

        pending.discard(user_id)
        resolved.add(user_id)

        channel_stats["pending"] = sorted(pending)
        channel_stats["closed"] = sorted(resolved)

        if changed:
            await self.save_state()

    async def record_fsub_member(
        self,
        channel_id: int,
        user_id: int,
        *,
        accepted: bool = False,
    ) -> None:
        channel_id = int(channel_id)
        user_id = int(user_id)
        key = str(channel_id)

        channel_stats = self.state.setdefault(
            "fsub_stats",
            {},
        ).setdefault(key, {})

        field = "accepted" if accepted else "joined"

        if accepted and user_id not in {
            int(item)
            for item in channel_stats.get("requests", [])
        }:
            return

        users = {
            int(item)
            for item in channel_stats.get(field, [])
        }

        changed = False

        if user_id not in users:
            users.add(user_id)
            channel_stats[field] = sorted(users)
            changed = True

        if accepted:
            pending = {
                int(item)
                for item in channel_stats.get("pending", [])
            }

            if user_id in pending:
                pending.discard(user_id)
                channel_stats["pending"] = sorted(pending)
                changed = True

            closed = {
                int(item)
                for item in channel_stats.get("closed", [])
            }

            if user_id in closed:
                closed.discard(user_id)
                channel_stats["closed"] = sorted(closed)
                changed = True

        if changed:
            await self.save_state()

    def get_fsub_counts(
        self,
        channel_id: int,
    ) -> Dict[str, int]:
        key = str(int(channel_id))

        stats = self.state.get(
            "fsub_stats",
            {},
        ).get(key, {})

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
            "pending": len(
                (unique_ids("pending") & requests) - accepted
            ),
            "closed": len(
                (unique_ids("closed") & requests) - accepted
            ),
        }

    async def clear_fsub_counts(
        self,
        channel_id: int,
    ) -> None:
        """Clear display statistics without removing verification evidence."""
        self.state.setdefault(
            "fsub_stats",
            {},
        ).pop(str(int(channel_id)), None)

        await self.save_state()

    async def record_user(
        self,
        user_id: int,
        first_name: str = "",
        last_name: str = "",
        username: str = "",
    ) -> None:
        user_id = int(user_id)

        users = {
            int(item)
            for item in self.state.get("users", [])
        }

        is_new = user_id not in users
        users.add(user_id)

        self.state["users"] = sorted(users)

        profiles = self.state.setdefault(
            "user_profiles",
            {},
        )

        profile = {
            "first_name": str(first_name or ""),
            "last_name": str(last_name or ""),
            "username": str(username or ""),
        }

        if profiles.get(str(user_id)) != profile or is_new:
            profiles[str(user_id)] = profile

            stats = self.state.setdefault(
                "stats",
                dict(DEFAULT_STATE["stats"]),
            )

            stats["total_users"] = len(users)

            await self.save_state()

    def close(self) -> None:
        self.database.close()
