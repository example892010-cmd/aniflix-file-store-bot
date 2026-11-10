import json
import os
import logging
from typing import Dict, Any, Optional
from pyrogram import Client
from pyrogram.errors import RPCError

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
    def __init__(self, client: Client, archive_channel_id: int):
        self.client = client
        self.archive_channel_id = archive_channel_id
        self.state: Dict[str, Any] = json.loads(json.dumps(DEFAULT_STATE))
        self.state_message_id: Optional[int] = None

    def _merge_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        result = json.loads(json.dumps(DEFAULT_STATE))
        if isinstance(data, dict):
            for k, v in data.items():
                if k == "stats" and isinstance(v, dict):
                    result["stats"].update(v)
                else:
                    result[k] = v
        return result

    def _load_local_state(self) -> bool:
        if os.path.exists(LOCAL_STATE_FILE):
            try:
                with open(LOCAL_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.state = self._merge_defaults(data)
                    self.state_message_id = data.get("_state_message_id")
                    self.state.pop("_state_message_id", None)
                    logger.info("Loaded state from local cache.")
                    return True
            except Exception as e:
                logger.warning(f"Failed to read local state file: {e}")
        return False

    def _save_local_state(self):
        try:
            to_save = dict(self.state)
            to_save["_state_message_id"] = self.state_message_id
            temporary_path = LOCAL_STATE_FILE + ".tmp"
            with open(temporary_path, "w", encoding="utf-8") as f:
                json.dump(to_save, f, ensure_ascii=False, indent=2)
            os.replace(temporary_path, LOCAL_STATE_FILE)
        except Exception as e:
            logger.warning(f"Failed to write local state file: {e}")

    async def load_state(self):
        """Loads state using pinned channel message and local cache without get_chat_history."""
        self._load_local_state()

        if not self.archive_channel_id:
            logger.warning("No archive_channel_id configured; relying solely on memory/local state.")
            return

        try:
            chat = await self.client.get_chat(self.archive_channel_id)
            pinned = getattr(chat, "pinned_message", None)

            if pinned and pinned.text and STATE_TAG in pinned.text:
                self.state_message_id = pinned.id
                raw_json = pinned.text.split(STATE_TAG, 1)[1].strip()
                data = json.loads(raw_json)
                self.state = self._merge_defaults(data)
                self._save_local_state()
                logger.info("Successfully loaded state from pinned archive message.")
                return

            if self.state_message_id:
                try:
                    msg = await self.client.get_messages(self.archive_channel_id, self.state_message_id)
                    if msg and msg.text and STATE_TAG in msg.text:
                        raw_json = msg.text.split(STATE_TAG, 1)[1].strip()
                        data = json.loads(raw_json)
                        self.state = self._merge_defaults(data)
                        self._save_local_state()
                        logger.info("Successfully loaded state from tracked state_message_id.")
                        return
                except Exception as inner_e:
                    logger.debug(f"Could not load state by stored message ID: {inner_e}")

            logger.info("No remote state message found. Creating initial pinned state message...")
            await self.save_state()

        except RPCError as e:
            logger.error(f"Pyrogram RPC error while loading state from archive channel: {e}")
        except Exception as e:
            logger.error(f"Unexpected error loading state: {e}")

    async def save_state(self):
        """Saves current state by updating or creating the pinned archive message."""
        self._save_local_state()

        if not self.archive_channel_id:
            return

        content = f"{STATE_TAG}\n" + json.dumps(self.state, ensure_ascii=False, indent=2)
        if len(content) > 4000:
            logger.error(
                "State is too large for one Telegram text message (%s characters); "
                "remote state was not updated. Local cache is not durable on Render Free.",
                len(content),
            )
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
                except RPCError as e:
                    logger.warning(f"Failed to edit existing state message {self.state_message_id}: {e}. Creating new one.")

            msg = await self.client.send_message(
                chat_id=self.archive_channel_id,
                text=content,
                parse_mode=None,
            )
            self.state_message_id = msg.id
            self._save_local_state()

            try:
                await self.client.pin_chat_message(
                    chat_id=self.archive_channel_id,
                    message_id=msg.id,
                    both_sides=True
                )
            except Exception as pin_err:
                logger.warning(f"Could not pin state message (check bot pin permissions): {pin_err}")

        except Exception as e:
            logger.error(f"Failed to save state to archive channel: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    async def set(self, key: str, value: Any):
        self.state[key] = value
        await self.save_state()

    def get_stats(self) -> Dict[str, int]:
        return self.state.get("stats", DEFAULT_STATE["stats"])

    async def update_stats(self, **kwargs):
        stats = self.state.setdefault("stats", dict(DEFAULT_STATE["stats"]))
        for k, v in kwargs.items():
            if k in stats:
                stats[k] += v
            else:
                stats[k] = v
        await self.save_state()

    def get_admins(self) -> list:
        return self.state.get("admins", [])

    async def add_admin(self, user_id: int):
        admins = set(self.state.get("admins", []))
        admins.add(user_id)
        self.state["admins"] = list(admins)
        await self.save_state()

    async def remove_admin(self, user_id: int):
        admins = set(self.state.get("admins", []))
        admins.discard(user_id)
        self.state["admins"] = list(admins)
        await self.save_state()

    def get_fsub_channels(self) -> list:
        return self.state.get("fsub_channels", [])

    async def set_fsub_channels(self, channels: list):
        self.state["fsub_channels"] = channels[:10]
        await self.save_state()

    async def remove_fsub_channel(self, channel_id: int):
        """Remove a channel from FSUB and erase its channel-specific records.

        Old join-request records must not unlock access if the same channel is
        added again later. Statistics for a removed channel are removed too.
        """
        key = str(int(channel_id))
        self.state["fsub_channels"] = [
            channel for channel in self.state.get("fsub_channels", [])
            if int(channel.get("id") or 0) != int(channel_id)
        ]
        self.state.setdefault("fsub_join_requests", {}).pop(key, None)
        self.state.setdefault("fsub_stats", {}).pop(key, None)
        await self.save_state()

    def has_fsub_join_request(self, channel_id: int, user_id: int) -> bool:
        """Whether Telegram has confirmed this user's request for this channel."""
        requests = self.state.get("fsub_join_requests", {})
        users = requests.get(str(int(channel_id)), []) if isinstance(requests, dict) else []
        return int(user_id) in {int(item) for item in users}

    def _fsub_stat_users(self, channel_id: int, field: str) -> set:
        stats = self.state.setdefault("fsub_stats", {})
        channel_stats = stats.setdefault(str(int(channel_id)), {})
        return {int(item) for item in channel_stats.get(field, [])}

    async def record_fsub_join_request(self, channel_id: int, user_id: int):
        """Record the access-qualifying request and a separate unique statistics event."""
        channel_id, user_id = int(channel_id), int(user_id)
        key = str(channel_id)
        requests = self.state.setdefault("fsub_join_requests", {})
        access_users = {int(item) for item in requests.get(key, [])}
        changed = False
        if user_id not in access_users:
            access_users.add(user_id)
            requests[key] = sorted(access_users)
            changed = True

        stat_users = self._fsub_stat_users(channel_id, "requests")
        if user_id not in stat_users:
            stat_users.add(user_id)
            self.state.setdefault("fsub_stats", {}).setdefault(key, {})["requests"] = sorted(stat_users)
            changed = True
        if changed:
            await self.save_state()

    async def record_fsub_member(self, channel_id: int, user_id: int, *, accepted: bool = False):
        """Record a Telegram-confirmed member; accepted is for previously recorded requests."""
        channel_id, user_id = int(channel_id), int(user_id)
        key = str(channel_id)
        channel_stats = self.state.setdefault("fsub_stats", {}).setdefault(key, {})
        field = "accepted" if accepted else "joined"
        users = {int(item) for item in channel_stats.get(field, [])}
        if user_id not in users:
            users.add(user_id)
            channel_stats[field] = sorted(users)
            await self.save_state()

    def get_fsub_counts(self, channel_id: int) -> Dict[str, int]:
        key = str(int(channel_id))
        channel_stats = self.state.get("fsub_stats", {}).get(key, {})
        return {
            "joined": len(set(channel_stats.get("joined", []))),
            "requests": len(set(channel_stats.get("requests", []))),
            "accepted": len(set(channel_stats.get("accepted", []))),
        }

    async def clear_fsub_counts(self, channel_id: int):
        """Clear display statistics only; preserve join-request access records."""
        self.state.setdefault("fsub_stats", {}).pop(str(int(channel_id)), None)
        await self.save_state()

    async def record_user(self, user_id: int, first_name: str = "", last_name: str = "", username: str = ""):
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
