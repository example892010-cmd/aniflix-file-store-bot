import asyncio
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from pyrogram.errors import (
    FloodWait,
    UserNotParticipant,
    MessageNotModified
)

import config
from helper.state_store import StateStore
from helper.link_codec import (
    decode_batch,
    decode_batch_manifest_id,
    decode_message_id,
    encode_batch_manifest_id,
    encode_message_id,
)
from helper.storage import (
    MAX_BATCH_MESSAGES,
    Storage,
    deliver_archived_batch,
    save_batch_manifest,
    save_auto_batch_manifest,
)
from helper.upload_order import ordered_archive_message_ids, sort_upload_messages
from helper.anime_metadata import (
    detect_auto_batch_metadata,
    build_auto_batch_result,
    get_auto_batch_qualities,
    build_auto_detection_preview,
)
from helper.auto_detect_settings import (
    get_default_auto_detect_settings,
    normalize_auto_detect_settings,
    save_auto_detect_settings_to_store,
    SUPPORTED_FORMATS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ANIFLIX-FILE-STORE-BOT")

# In-memory tracking for multi-file /getlink upload sessions
USER_UPLOAD_SESSIONS: Dict[int, Dict] = {}
# In-memory tracking for /batch forwarding sessions
USER_BATCH_SESSIONS: Dict[int, Dict] = {}
# Channel-scoped /getlinkauto sessions.
AUTO_CHANNEL_SESSIONS: Dict[int, Dict] = {}
# Private-scoped /getlinkauto sessions.
AUTO_PRIVATE_SESSIONS: Dict[int, Dict] = {}

AUTO_REQUIRED_QUALITIES = frozenset({"480p", "720p", "1080p"})
AUTO_QUALITY_RE = re.compile(
    r"(?<!\d)(480|720|1080)\s*p(?![A-Za-z0-9])",
    re.IGNORECASE,
)
ADMIN_PENDING_ACTIONS: Dict[int, str] = {}
ADMIN_PENDING_DATA: Dict[int, Dict] = {}

def _get_archive_channel_id() -> int:
    return bot.settings.archive_channel_id

def _get_admins() -> List[int]:
    return list(bot.settings.admins)

class AniflixBot(Client):
    def __init__(self, settings=None):
        self.settings = settings or config.load_settings()
        super().__init__(
            name=self.settings.session_name,
            api_id=self.settings.api_id,
            api_hash=self.settings.api_hash,
            bot_token=self.settings.bot_token,
            workers=8
        )
        self.archive_channel_id = self.settings.archive_channel_id
        self.state_store = StateStore(self, self.archive_channel_id, self.settings.sqlite_path)
        self.file_storage = Storage(self, self.archive_channel_id, self.state_store.database)
        self.batch_reader = None
        if self.settings.batch_user_session_string:
            self.batch_reader = Client(
                name=f"{self.settings.session_name}_batch_reader",
                api_id=self.settings.api_id,
                api_hash=self.settings.api_hash,
                session_string=self.settings.batch_user_session_string,
                no_updates=True,
            )

    async def start(self):
        await super().start()
        try:
            if self.batch_reader:
                await self.batch_reader.start()
                logger.info("Optional MTProto batch reader started.")
            me = await self.get_me()
            self.username = me.username
            logger.info(f"Bot started as @{self.username}")
            await self.state_store.load_state()
            logger.info("Bot state initialized successfully.")
        except Exception:
            if self.batch_reader and self.batch_reader.is_connected:
                await self.batch_reader.stop()
            await super().stop()
            raise

    async def stop(self, *args):
        await self.state_store.save_state()
        if self.batch_reader and self.batch_reader.is_connected:
            await self.batch_reader.stop()
        await super().stop(*args)
        self.state_store.close()
        logger.info("Bot stopped cleanly.")

bot = AniflixBot()

@bot.on_chat_member_updated(filters.channel)
async def record_fsub_membership_update(client: Client, update):
    try:
        channel_id = int(update.chat.id)
        old_member = getattr(update, "old_chat_member", None)
        new_member = getattr(update, "new_chat_member", None)
        user = getattr(new_member, "user", None)
        if not user:
            return
        became_member = _is_joined_member(new_member) and not (
            _is_joined_member(old_member) if old_member else False
        )
        if not became_member:
            return
        for channel in bot.state_store.get_fsub_channels():
            if int(channel.get("id") or 0) != channel_id:
                continue
            is_request_channel = bool(channel.get("join_request") or channel.get("type") == "join_request")
            if is_request_channel:
                if bot.state_store.has_fsub_join_request(channel_id, int(user.id)):
                    await bot.state_store.record_fsub_member(channel_id, int(user.id), accepted=True)
            else:
                await bot.state_store.record_fsub_member(channel_id, int(user.id))
            return
    except Exception:
        logger.exception("Could not record FSUB membership update")

@bot.on_chat_join_request(filters.channel)
async def record_fsub_join_request(client: Client, request):
    try:
        channel_id = int(request.chat.id)
        user_id = int(request.from_user.id)
        invite = getattr(request, "invite_link", None)
        received_link = getattr(invite, "invite_link", None) if invite else None
        for channel in bot.state_store.get_fsub_channels():
            configured_id = int(channel.get("id") or 0)
            is_request_channel = bool(
                channel.get("join_request") or channel.get("type") == "join_request"
            )
            if configured_id != channel_id or not is_request_channel:
                continue
            if not received_link or received_link != channel.get("link"):
                return
            await bot.state_store.record_fsub_join_request(channel_id, user_id)
            return
    except Exception:
        logger.exception("Could not record incoming FSUB join request")

# ------------------- AUTO-LINK HELPERS ------------------- #

def _get_auto_detect_settings():
    saved = bot.state_store.get("auto_detect_settings", get_default_auto_detect_settings())
    return normalize_auto_detect_settings(saved)

def _auto_detect_settings_text():
    settings = _get_auto_detect_settings()
    enabled = "ON" if settings.get("enabled", True) else "OFF"
    format_name = settings.get("format", "S{season} Ep{episode}")
    title = settings.get("manual_title") or "Automatic filename detection"
    custom = "Configured" if settings.get("custom_regex") else "Not configured"
    return (
        "🧠 **AUTO DETECT SETTINGS**\n\n"
        f"Status: **{enabled}**\n"
        f"Filename format: `{format_name}`\n"
        f"Anime title: **{title}**\n"
        f"Custom regex: **{custom}**\n"
        f"Episode number padding: **{settings.get('episode_padding', 2)} digits**\n\n"
        "Choose a setting below. Changes are saved and used by the next `/getlinkauto` batch."
    )

def _auto_detect_settings_keyboard():
    settings = _get_auto_detect_settings()
    toggle_label = "⏸ Disable Detection" if settings.get("enabled", True) else "▶️ Enable Detection"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data="adm_autodetect_toggle")],
        [InlineKeyboardButton("🧾 Filename Format", callback_data="adm_autodetect_format")],
        [InlineKeyboardButton("✏️ Set Anime Title", callback_data="adm_autodetect_title")],
        [InlineKeyboardButton("🧩 Set Custom Regex", callback_data="adm_autodetect_regex")],
        [InlineKeyboardButton("🔢 Episode Padding", callback_data="adm_autodetect_padding")],
        [InlineKeyboardButton("🧪 Test Detection", callback_data="adm_autodetect_test")],
        [InlineKeyboardButton("♻️ Reset Defaults", callback_data="adm_autodetect_reset")],
        [InlineKeyboardButton("🔙 Admin Panel", callback_data="adm_back")],
    ])

def _auto_channel_message_filename(message) -> str:
    for field in ("document", "video", "audio", "animation"):
        media = getattr(message, field, None)
        filename = getattr(media, "file_name", None) if media else None
        if filename:
            return str(filename)
    return str(getattr(message, "caption", None) or getattr(message, "text", None) or "")

def _auto_message_quality_key(message, settings):
    filename = _auto_channel_message_filename(message)
    if not filename:
        return None

    quality_match = AUTO_QUALITY_RE.search(filename)
    if not quality_match:
        return None

    try:
        from helper.auto_detect_settings import get_auto_detect_pattern
        episode_match = get_auto_detect_pattern(settings).search(filename)
        if not episode_match:
            return None
        season = int(episode_match.group("season"))
        episode = int(episode_match.group("episode"))
    except (ImportError, AttributeError, KeyError, TypeError, ValueError, re.error):
        logger.exception("Could not parse /getlinkauto season/episode from %r", filename)
        return None

    title, detected_episodes = detect_auto_batch_metadata([message], settings=settings)
    if not detected_episodes:
        return None

    quality = f"{quality_match.group(1)}p".lower()
    return (title.casefold(), season, episode), quality

def _record_auto_quality(session: dict, message, settings):
    parsed = _auto_message_quality_key(message, settings)
    if not parsed:
        return None

    episode_key, quality = parsed
    quality_groups = session.setdefault("quality_groups", {})
    received = quality_groups.setdefault(episode_key, set())
    received.add(quality)

    if AUTO_REQUIRED_QUALITIES.issubset(received):
        return {
            "title": episode_key[0],
            "season": episode_key[1],
            "episode": episode_key[2],
            "qualities": sorted(received),
        }
    return None

async def _finish_auto_channel_batch(client: Client, channel_id: int, session: dict):
    if AUTO_CHANNEL_SESSIONS.get(channel_id) is not session or session.get("status") != "collecting":
        return
    session["status"] = "processing"
    messages = sorted(session.get("messages", {}).values(), key=lambda item: int(item.id))
    if not messages:
        AUTO_CHANNEL_SESSIONS.pop(channel_id, None)
        return
    status_message = None
    try:
        archived_ids = []
        for position, source in enumerate(messages, start=1):
            archived = await bot.file_storage.store_message(source)
            if not archived:
                raise RuntimeError(f"Could not verify source message {source.id} in the archive")
            archived_ids.append(int(archived.id))

        manifest_id = await save_auto_batch_manifest(bot, bot.archive_channel_id, archived_ids)
        code = encode_batch_manifest_id(manifest_id)
        try:
            bot.state_store.database.record_link("batch_" + code, "batch_manifest", manifest_id)
        except Exception:
            logger.exception("Could not record /getlinkauto batch link metadata")
        url = f"https://t.me/{bot.username}?start=batch_{code}"
        auto_settings = _get_auto_detect_settings()
        title, episodes = detect_auto_batch_metadata(messages, settings=auto_settings)
        result_text = build_auto_batch_result(title, episodes, url, settings=auto_settings)
        status_message = await client.send_message(channel_id, result_text, parse_mode=None)
        try:
            await bot.state_store.update_stats(total_links=1, total_files=len(archived_ids))
        except Exception:
            logger.exception("Could not update statistics after /getlinkauto success")

        source_ids = [int(item.id) for item in messages]
        if session.get("trigger_message_id"):
            source_ids.append(int(session["trigger_message_id"]))
        if session.get("prompt_message_id"):
            source_ids.append(int(session["prompt_message_id"]))
        delete_failed = []
        for offset in range(0, len(source_ids), 100):
            chunk = source_ids[offset:offset + 100]
            try:
                await client.delete_messages(channel_id, chunk)
            except Exception:
                delete_failed.extend(chunk)
        if delete_failed and status_message:
            await status_message.edit_text(
                result_text + "\n\n⚠️ Some original posts could not be deleted.",
                parse_mode=None,
            )
    except Exception as exc:
        logger.exception("/getlinkauto failed for channel %s", channel_id)
    finally:
        if AUTO_CHANNEL_SESSIONS.get(channel_id) is session:
            AUTO_CHANNEL_SESSIONS.pop(channel_id, None)

async def _finish_auto_private_batch(client: Client, user_id: int, session: dict):
    if AUTO_PRIVATE_SESSIONS.get(user_id) is not session or session.get("status") != "collecting":
        return
    session["status"] = "processing"
    messages = sorted(session.get("messages", {}).values(), key=lambda item: int(item.id))
    if not messages:
        AUTO_PRIVATE_SESSIONS.pop(user_id, None)
        return
    try:
        archived_ids = []
        for source in messages:
            archived = await bot.file_storage.store_message(source)
            if not archived:
                raise RuntimeError(f"Could not verify source message {source.id} in the archive")
            archived_ids.append(int(archived.id))

        manifest_id = await save_auto_batch_manifest(bot, bot.archive_channel_id, archived_ids)
        code = encode_batch_manifest_id(manifest_id)
        try:
            bot.state_store.database.record_link("batch_" + code, "batch_manifest", manifest_id)
        except Exception:
            logger.exception("Could not record private /getlinkauto batch link metadata")
        
        url = f"https://t.me/{bot.username}?start=batch_{code}"
        auto_settings = _get_auto_detect_settings()
        title, episodes = detect_auto_batch_metadata(messages, settings=auto_settings)
        result_text = build_auto_batch_result(title, episodes, url, settings=auto_settings)
        
        await client.send_message(user_id, "🎉 **All 3 Qualities Received! Combined Batch Link Generated:**\n\n" + result_text, parse_mode=None)
        try:
            await bot.state_store.update_stats(total_links=1, total_files=len(archived_ids))
        except Exception:
            logger.exception("Could not update statistics after private /getlinkauto success")
    except Exception as exc:
        logger.exception("Private /getlinkauto failed for user %s", user_id)
        await client.send_message(user_id, f"❌ Auto batch could not be completed: {exc}")
    finally:
        if AUTO_PRIVATE_SESSIONS.get(user_id) is session:
            AUTO_PRIVATE_SESSIONS.pop(user_id, None)

# ------------------- HELPER FUNCTIONS ------------------- #

def is_admin(user_id: int) -> bool:
    if user_id in _get_admins():
        return True
    admins = bot.state_store.get_admins()
    return user_id in admins

def is_owner(user_id: int) -> bool:
    return user_id == bot.settings.owner_id

def _member_status_name(member) -> str:
    return str(getattr(member, "status", "")).upper().split(".")[-1]

def _is_joined_member(member) -> bool:
    status = _member_status_name(member)
    if status in {"MEMBER", "ADMINISTRATOR", "OWNER", "CREATOR"}:
        return True
    if status == "RESTRICTED":
        return bool(getattr(member, "is_member", False))
    return False

def _forwarded_channel(message: Message):
    chat = getattr(message, "forward_from_chat", None)
    if chat is not None:
        return chat
    origin = getattr(message, "forward_origin", None)
    return getattr(origin, "chat", None) if origin is not None else None

async def _verify_bot_can_manage_invites(client: Client, channel_id: int):
    bot_identity = await client.get_me()
    bot_member = await client.get_chat_member(channel_id, bot_identity.id)
    status = _member_status_name(bot_member)
    if status not in {"ADMINISTRATOR", "OWNER"}:
        raise PermissionError("I am not an administrator in that channel. Promote me first.")
    if status != "OWNER":
        privileges = getattr(bot_member, "privileges", None)
        if not privileges or not getattr(privileges, "can_invite_users", False):
            raise PermissionError("I need the 'Invite Users via Link' permission.")

async def check_fsub(client: Client, user_id: int) -> List[Dict]:
    fsub_channels = bot.state_store.get_fsub_channels()
    unjoined = []
    for ch in fsub_channels:
        chat_identifier = ch.get("id") or ch.get("username")
        is_request_channel = bool(ch.get("join_request") or ch.get("type") == "join_request")
        has_request = is_request_channel and bot.state_store.has_fsub_join_request(
            int(ch.get("id") or 0), user_id
        )
        try:
            member = await client.get_chat_member(chat_identifier, user_id)
            if not _is_joined_member(member):
                unjoined.append(ch)
            elif is_request_channel and has_request:
                await bot.state_store.record_fsub_member(
                    int(ch.get("id") or 0), user_id, accepted=True
                )
            elif not is_request_channel:
                await bot.state_store.record_fsub_member(int(ch.get("id") or 0), user_id)
        except UserNotParticipant:
            unjoined.append(ch)
        except Exception as e:
            logger.warning("FSUB membership check failed for %s: %s", chat_identifier, e)
            unjoined.append(ch)
    return unjoined

async def _refresh_fsub_request_stats(client: Client, channel: Dict) -> None:
    channel_id = int(channel.get("id") or 0)
    if not channel_id or not hasattr(client, "get_chat_join_requests"):
        return
    try:
        pending_ids = set()
        pending_iter = client.get_chat_join_requests(channel_id)
        if hasattr(pending_iter, "__aiter__"):
            async for joiner in pending_iter:
                user = getattr(joiner, "user", None)
                if user and getattr(user, "id", None):
                    pending_ids.add(int(user.id))
        else:
            for joiner in pending_iter:
                user = getattr(joiner, "user", None)
                if user and getattr(user, "id", None):
                    pending_ids.add(int(user.id))
    except Exception as exc:
        logger.warning("Could not refresh pending FSUB requests for %s: %s", channel_id, exc)
        return

    saved_requests = bot.state_store.get("fsub_join_requests", {}).get(str(channel_id), [])
    for requested_user_id in saved_requests:
        requested_user_id = int(requested_user_id)
        if requested_user_id in pending_ids:
            await bot.state_store.record_fsub_pending(channel_id, requested_user_id)
            continue
        try:
            member = await client.get_chat_member(channel_id, requested_user_id)
            if _is_joined_member(member):
                await bot.state_store.record_fsub_member(
                    channel_id, requested_user_id, accepted=True
                )
            else:
                await bot.state_store.record_fsub_rejected_or_withdrawn(
                    channel_id, requested_user_id
                )
        except UserNotParticipant:
            await bot.state_store.record_fsub_rejected_or_withdrawn(
                channel_id, requested_user_id
            )
        except Exception as exc:
            logger.debug("Could not resolve FSUB request status: %s", exc)

def get_fsub_keyboard(unjoined: List[Dict], redirect_data: Optional[str] = None) -> InlineKeyboardMarkup:
    buttons = []
    for ch in unjoined:
        name = ch.get("name", "Join Channel")
        link = ch.get("link") or f"https://t.me/{str(ch.get('username', '')).lstrip('@')}"
        buttons.append([InlineKeyboardButton(text=name, url=link)])

    try_again_data = f"try_again_{redirect_data}" if redirect_data else "try_again"
    buttons.append([InlineKeyboardButton(text="🔄 Try Again", callback_data=try_again_data)])
    return InlineKeyboardMarkup(buttons)

# ------------------- COMMAND: /START ------------------- #

async def _deliver_start_payload(client: Client, user_id: int, message: Message, param: str):
    try:
        if param.startswith("batch_"):
            payload = param[6:]
            manifest_id = decode_batch_manifest_id(payload)
            if manifest_id is not None:
                sent_count, failed_count = await deliver_archived_batch(
                    client,
                    bot.archive_channel_id,
                    manifest_id,
                    user_id,
                    bot.settings.protect_content,
                )
                await bot.state_store.update_stats(total_views=1)
                if sent_count == 0:
                    return await message.reply_text("❌ No files in this batch are available.")
                if failed_count:
                    await message.reply_text(
                        f"⚠️ Batch delivered: `{sent_count}` files; `{failed_count}` unavailable."
                    )
                return

            start_id, end_id = decode_batch(payload)
            if end_id - start_id + 1 > MAX_BATCH_MESSAGES:
                return await message.reply_text(
                    f"❌ This legacy batch exceeds the {MAX_BATCH_MESSAGES}-message safety limit."
                )
            sent_count = failed_count = 0
            for message_id in range(start_id, end_id + 1):
                if await bot.file_storage.deliver_message(
                    user_id, message_id, bot.settings.protect_content
                ):
                    sent_count += 1
                else:
                    failed_count += 1
                await asyncio.sleep(0.1)
            await bot.state_store.update_stats(total_views=1)
            if sent_count == 0:
                return await message.reply_text("❌ No files in this batch are available.")
            return

        message_id = decode_message_id(param)
        if not await bot.file_storage.deliver_message(
            user_id, message_id, bot.settings.protect_content
        ):
            return await message.reply_text("❌ This file is no longer available in the archive.")
        await bot.state_store.update_stats(total_views=1)
    except Exception as exc:
        logger.warning("Start payload could not be delivered: %s", exc)
        await message.reply_text("❌ This link is corrupted, invalid, or expired.")

async def _reply_configured_message(
    message: Message,
    content,
    *,
    default_text: str,
    reply_markup=None,
    replacements: Optional[Dict[str, str]] = None,
):
    if not content:
        content = default_text

    if isinstance(content, dict) and content.get("type") == "photo" and content.get("file_id"):
        caption = content.get("caption") or ""
        for key, value in (replacements or {}).items():
            caption = caption.replace("{" + key + "}", value)
        return await message.reply_photo(
            photo=content["file_id"],
            caption=caption or None,
            reply_markup=reply_markup,
        )

    text = content if isinstance(content, str) else default_text
    for key, value in (replacements or {}).items():
        text = text.replace("{" + key + "}", value)
    return await message.reply_text(text, reply_markup=reply_markup)

def _admin_message_payload(message: Message):
    photo = getattr(message, "photo", None)
    if photo:
        if isinstance(photo, (list, tuple)):
            photo = next(
                (item for item in reversed(photo) if getattr(item, "file_id", None)),
                None,
            )
        file_id = getattr(photo, "file_id", None) if photo is not None else None
        if file_id:
            return {
                "type": "photo",
                "file_id": file_id,
                "caption": (getattr(message, "caption", None) or "").strip(),
            }

    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    return text or None

async def _reply_welcome(message: Message, first_name: str = "there"):
    welcome = bot.state_store.get("welcome_message")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("ONGOING ANIME", url=bot.settings.ongoing_url)],
        [InlineKeyboardButton("ANIME REQUEST GROUP", url=bot.settings.support_url)],
    ])
    default_text = (
        f"👋 Hello, **{first_name}**!\n\n"
        "Welcome to **ANIFLIX FILE STORE BOT**.\n"
        "Send me media or use `/getlink`, `/getlinkauto`, and `/batch` to store and generate sharing links."
    )
    return await _reply_configured_message(
        message,
        welcome,
        default_text=default_text,
        reply_markup=kb,
        replacements={"user": first_name},
    )

async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    await bot.state_store.record_user(
        user_id,
        first_name=getattr(message.from_user, "first_name", "") or "",
        last_name=getattr(message.from_user, "last_name", "") or "",
        username=getattr(message.from_user, "username", "") or "",
    )
    args = getattr(message, "command", []) or []
    if len(args) > 1:
        param = args[1]
        unjoined = await check_fsub(client, user_id)
        if unjoined:
            fsub_text = bot.state_store.get("fsub_message")
            default_fsub_text = (
                "⚠️ **Access Denied!**\n\n"
                "You must join our official channels to access files stored here.\n"
                "Join each channel below and tap **Try Again**."
            )
            return await _reply_configured_message(
                message,
                fsub_text,
                default_text=default_fsub_text,
                reply_markup=get_fsub_keyboard(unjoined, redirect_data=param),
            )
        return await _deliver_start_payload(client, user_id, message, param)
    return await _reply_welcome(message, message.from_user.first_name)

bot.on_message(filters.command("start") & filters.private)(start_handler)

# ------------------- PRIVATE COMMAND: /GETLINKAUTO ------------------- #

@bot.on_message(filters.command("getlinkauto") & filters.private)
async def getlinkauto_private_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in USER_UPLOAD_SESSIONS:
        return await message.reply_text("⚠️ You already have an active upload session. Finish or cancel it first.")
    
    AUTO_PRIVATE_SESSIONS[user_id] = {
        "status": "collecting",
        "messages": {},
        "quality_groups": {},
        "completion_lock": asyncio.Lock(),
    }
    await message.reply_text(
        "🧠 **Auto-Batch Collection Started (Private Mode)**\n\n"
        "Send the 3 quality files (`480p`, `720p`, `1080p`) for your anime episode "
        "(e.g., `@ANIFLIXANIMETAMIL - OverGeared S01 Ep07 [480p] [...]`).\n\n"
        "As soon as all 3 qualities are received, I will automatically archive them and send you **one combined batch link**!\n\n"
        "Send `/cancel` to abort.",
        parse_mode=None
    )

# ------------------- CHANNEL COMMAND: /GETLINKAUTO ------------------- #

@bot.on_message(filters.command("getlinkauto") & filters.channel)
async def getlinkauto_channel_handler(client: Client, message: Message):
    channel_id = int(message.chat.id)
    if message.from_user and not is_admin(int(message.from_user.id)):
        return
    try:
        me = await client.get_me()
        member = await client.get_chat_member(channel_id, me.id)
        status = _member_status_name(member)
        privileges = getattr(member, "privileges", None)
        owner_status = status in {"OWNER", "CREATOR"}
        if status != "ADMINISTRATOR" and not owner_status:
            return await client.send_message(
                channel_id,
                "⛔ Promote me as a channel administrator with **Post Messages** and **Delete Messages** permissions to use /getlinkauto.",
            )
    except Exception as exc:
        logger.exception("Could not verify channel permissions for /getlinkauto")
        return await client.send_message(channel_id, f"❌ Could not verify my channel permissions: {exc}")

    existing = AUTO_CHANNEL_SESSIONS.get(channel_id)
    if existing and existing.get("status") in {"collecting", "processing"}:
        return await client.send_message(channel_id, "⚠️ An automatic batch is already running in this channel.")
    
    session = {
        "status": "collecting",
        "messages": {},
        "quality_groups": {},
        "completion_lock": asyncio.Lock(),
        "trigger_message_id": int(message.id),
    }
    AUTO_CHANNEL_SESSIONS[channel_id] = session
    prompt = await client.send_message(
        channel_id,
        "📥 **Auto batch collection started.** Send the episode in `480p`, `720p`, and `1080p` quality. "
        "I will detect the labels from each filename and automatically create one combined batch link "
        "when all three qualities arrive.",
        parse_mode=None,
    )
    session["prompt_message_id"] = int(prompt.id)

@bot.on_message(filters.channel & ~filters.command(["getlinkauto"]))
async def getlinkauto_collect_channel_messages(client: Client, message: Message):
    channel_id = int(message.chat.id)
    session = AUTO_CHANNEL_SESSIONS.get(channel_id)
    if not session or session.get("status") != "collecting":
        return
    if message.from_user and getattr(message.from_user, "is_bot", False):
        return
    if int(message.id) in {
        int(session.get("trigger_message_id") or 0),
        int(session.get("prompt_message_id") or 0),
    }:
        return
    
    lock = session.setdefault("completion_lock", asyncio.Lock())
    async with lock:
        if AUTO_CHANNEL_SESSIONS.get(channel_id) is not session or session.get("status") != "collecting":
            return

        session.setdefault("messages", {})[int(message.id)] = message
        settings = _get_auto_detect_settings()
        completion = _record_auto_quality(session, message, settings)

        if completion:
            await _finish_auto_channel_batch(client, channel_id, session)

# ------------------- COMMAND: /GETLINK ------------------- #

@bot.on_message(filters.command("getlink") & filters.private)
async def getlink_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in USER_UPLOAD_SESSIONS:
        return await message.reply_text(
            "⚠️ You already have an upload session. Finish, resume, or cancel it first."
        )
    buttons = [
        [InlineKeyboardButton("✅ GENERATE LINK", callback_data="gen_link_finish")],
        [InlineKeyboardButton("⏸ PAUSE", callback_data="gen_link_pause"),
         InlineKeyboardButton("❌ CANCEL", callback_data="gen_link_cancel")]
    ]
    control_message = await message.reply_text(
        "📥 **Multi-Message Upload Session Started**\n\n"
        "Forward all the messages/files together. I will keep their original order.\n"
        "Queued: **0**\n\n"
        "Tap **GENERATE LINK** after forwarding is complete.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    USER_UPLOAD_SESSIONS[user_id] = {
        "status": "recording",
        "source_messages": {},
        "archived": {},
        "message_ids": [],
        "control_message": control_message,
        "status_lock": asyncio.Lock(),
        "finish_lock": asyncio.Lock(),
    }

# ------------------- COMMAND: /BATCH ------------------- #

@bot.on_message(filters.command("batch") & filters.private)
async def batch_cmd_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("⛔ This command is restricted to administrators.")

    USER_BATCH_SESSIONS[message.from_user.id] = {"step": "await_first"}
    await message.reply_text(
        "📦 **Batch File Link Generator**\n\n"
        "1. Forward the **FIRST** message from the source channel or send its Telegram post link.\n"
        "Send /cancel to abort at any time."
    )

# ------------------- COMMAND: /ADMIN & ADMIN FEATURES ------------------- #

@bot.on_message(filters.command("admin") & filters.private)
async def admin_panel(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("⛔ Unauthorized.")

    stats = bot.state_store.get_stats()
    text = (
        "⚙️ **ANIFLIX Admin Dashboard**\n\n"
        f"👥 Total Users: `{stats.get('total_users', 0)}`\n"
        f"🔗 Generated Links: `{stats.get('total_links', 0)}`\n"
        f"📁 Stored Files: `{stats.get('total_files', 0)}`\n"
        f"👁️ Views/Retrievals: `{stats.get('total_views', 0)}`\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 FSUB Settings", callback_data="adm_fsub"),
         InlineKeyboardButton("📊 Bot Status", callback_data="adm_status")],
        [InlineKeyboardButton("📝 Edit Welcome", callback_data="adm_edit_welcome"),
         InlineKeyboardButton("📬 Broadcast", callback_data="adm_broadcast")],
        [InlineKeyboardButton("🧠 Auto Detect Settings", callback_data="adm_autodetect")]
    ])
    await message.reply_text(text, reply_markup=kb)

@bot.on_message(filters.command("broadcast") & filters.private)
async def broadcast_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message:
        return await message.reply_text("⚠️ Reply to a message with `/broadcast` to send it to all users.")

    users = bot.state_store.get("users", [])
    status_msg = await message.reply_text(f"🚀 Broadcasting to `{len(users)}` users...")
    success = failed = 0

    for uid in users:
        try:
            await message.reply_to_message.copy(chat_id=uid)
            success += 1
            await asyncio.sleep(0.05)
        except FloodWait as f:
            await asyncio.sleep(f.value)
            try:
                await message.reply_to_message.copy(chat_id=uid)
                success += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1

    await status_msg.edit_text(f"✅ **Broadcast Completed**\n• Successful: `{success}`\n• Failed: `{failed}`")

# ------------------- PRIVATE MESSAGE INTERCEPTOR ------------------- #

async def _batch_endpoint(client: Client, message: Message):
    forwarded_chat = getattr(message, "forward_from_chat", None)
    forwarded_id = getattr(message, "forward_from_message_id", None)
    if forwarded_chat and forwarded_id:
        return int(forwarded_chat.id), int(forwarded_id)

    text = getattr(message, "text", None) or ""
    match = re.search(r"https?://(?:www\.)?(?:t\.me|telegram\.me)/[^\s]+", text)
    if not match:
        return None
    parsed = urlparse(match.group(0).rstrip(".,);]"))
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    try:
        message_id = int(parts[-1])
    except ValueError:
        return None

    if parts[0] == "c" and len(parts) >= 3 and parts[1].isdigit():
        channel_id = int(f"-100{parts[1]}")
    else:
        username = parts[0]
        if username in {"s", "share"} or username.startswith("+"):
            return None
        channel_id = username if not username.lstrip("-").isdigit() else int(username)
    try:
        resolver = bot.batch_reader or client
        chat = await resolver.get_chat(channel_id)
        return int(chat.id), message_id
    except Exception as exc:
        logger.info("Could not resolve batch source %s: %s", channel_id, exc)
        return None

def _normalize_messages(result):
    if isinstance(result, (list, tuple)):
        messages = [item for item in result if item and not getattr(item, "empty", False)]
    elif result is None or getattr(result, "empty", False):
        messages = []
    else:
        messages = [result]
    return sorted(messages, key=lambda item: int(item.id))

@bot.on_message(filters.private & ~filters.command(["start", "admin", "getlink", "batch", "broadcast", "users", "status", "getlinkauto"]))
async def incoming_private_messages(client: Client, message: Message):
    user_id = message.from_user.id

    # Handle /cancel command
    if message.text and message.text.split(maxsplit=1)[0].split("@")[0] == "/cancel":
        if user_id in AUTO_PRIVATE_SESSIONS:
            AUTO_PRIVATE_SESSIONS.pop(user_id, None)
            return await message.reply_text("❌ Auto-batch collection session cancelled.")
        if USER_BATCH_SESSIONS.pop(user_id, None) is not None:
            return await message.reply_text("❌ Batch process cancelled.")
        if USER_UPLOAD_SESSIONS.pop(user_id, None) is not None:
            return await message.reply_text("❌ Upload session cancelled.")

    # Handle Private /getlinkauto session
    if user_id in AUTO_PRIVATE_SESSIONS:
        session = AUTO_PRIVATE_SESSIONS[user_id]
        if session.get("status") == "collecting":
            filename = _auto_channel_message_filename(message)
            if not filename and not message.media:
                return await message.reply_text("⚠️ Please send the quality media files (480p, 720p, 1080p) with their filenames.")
            
            lock = session.setdefault("completion_lock", asyncio.Lock())
            async with lock:
                if AUTO_PRIVATE_SESSIONS.get(user_id) is not session or session.get("status") != "collecting":
                    return

                session.setdefault("messages", {})[int(message.id)] = message
                settings = _get_auto_detect_settings()
                completion = _record_auto_quality(session, message, settings)

                if completion:
                    await message.reply_text(
                        f"✅ Received all 3 qualities (`480p`, `720p`, `1080p`) for **{completion['title']}** S{completion['season']:02d} Ep{completion['episode']:02d}! Generating combined batch link..."
                    )
                    await _finish_auto_private_batch(client, user_id, session)
                else:
                    parsed = _auto_message_quality_key(message, settings)
                    if parsed:
                        _, quality = parsed
                        received = session["quality_groups"].get(parsed[0], set())
                        await message.reply_text(
                            f"📥 Received **{quality.upper()}** quality file.\n"
                            f"Qualities collected for this episode: `{', '.join(sorted(received))}` / `480p, 720p, 1080p`"
                        )
                    else:
                        await message.reply_text(
                            "⚠️ File received, but could not detect resolution (`480p`, `720p`, or `1080p`) or episode number from filename. Make sure your filename follows the format (e.g. `... S01 Ep07 [720p] ...`)."
                        )
            return

    # Handle Admin Actions
    action = ADMIN_PENDING_ACTIONS.get(user_id)
    if action:
        if not is_admin(user_id):
            ADMIN_PENDING_ACTIONS.pop(user_id, None)
            ADMIN_PENDING_DATA.pop(user_id, None)
            return await message.reply_text("⛔ Unauthorized.")
        if action == "autodetect_title":
            title_value = (message.text or message.caption or "").strip()
            if not title_value:
                return await message.reply_text("Send a title or `auto`.")
            settings = _get_auto_detect_settings()
            settings["manual_title"] = "" if title_value.lower() == "auto" else title_value[:150]
            await save_auto_detect_settings_to_store(bot.state_store, settings)
            ADMIN_PENDING_ACTIONS.pop(user_id, None)
            return await message.reply_text("✅ Anime title override saved.")

    # Handle /batch session
    if user_id in USER_BATCH_SESSIONS:
        step = USER_BATCH_SESSIONS[user_id].get("step")
        if step == "await_first":
            endpoint = await _batch_endpoint(client, message)
            if not endpoint:
                return await message.reply_text("⚠️ Please forward a message from a channel or supply a valid message link.")
            f_chat_id, f_msg_id = endpoint
            USER_BATCH_SESSIONS[user_id]["chat_id"] = f_chat_id
            USER_BATCH_SESSIONS[user_id]["first_id"] = f_msg_id
            USER_BATCH_SESSIONS[user_id]["step"] = "await_last"
            return await message.reply_text(f"✅ First message ID: `{f_msg_id}`\n\nNow forward the **LAST** message from the same channel.")

        elif step == "await_last":
            endpoint = await _batch_endpoint(client, message)
            if not endpoint:
                return await message.reply_text("⚠️ Please forward the last message.")
            l_chat_id, l_msg_id = endpoint
            first_id = USER_BATCH_SESSIONS[user_id]["first_id"]
            chat_id = USER_BATCH_SESSIONS[user_id]["chat_id"]
            if int(l_chat_id) != int(chat_id):
                return await message.reply_text("⚠️ The last message must come from the same channel as the first.")
            del USER_BATCH_SESSIONS[user_id]

            start_id = min(first_id, l_msg_id)
            end_id = max(first_id, l_msg_id)
            status_msg = await message.reply_text(f"⏳ Processing `{end_id - start_id + 1}` messages into Archive...")
            
            archived_ids = []
            stored_count = 0
            all_ids = list(range(start_id, end_id + 1))
            for i in range(0, len(all_ids), MAX_BATCH_MESSAGES):
                chunk = all_ids[i:i + MAX_BATCH_MESSAGES]
                source_reader = bot.batch_reader or client
                msgs = await source_reader.get_messages(chat_id, chunk)
                for src_msg in _normalize_messages(msgs):
                    if not src_msg or src_msg.empty:
                        continue
                    copied = await bot.file_storage.store_message(src_msg)
                    if copied:
                        archived_ids.append(copied.id)
                        stored_count += 1
                        await asyncio.sleep(0.2)

            manifest_id = await save_batch_manifest(bot, bot.archive_channel_id, archived_ids)
            batch_hash = encode_batch_manifest_id(manifest_id)
            bot.state_store.database.record_link("batch_" + batch_hash, "batch_manifest", manifest_id)
            batch_url = f"https://t.me/{bot.username}?start=batch_{batch_hash}"
            await bot.state_store.update_stats(total_links=1, total_files=stored_count)
            return await status_msg.edit_text(f"🎉 **Batch Link Generated!**\n• Total Files: `{stored_count}`\n• Link: {batch_url}")

    # Handle /getlink upload sessions
    if user_id in USER_UPLOAD_SESSIONS:
        session = USER_UPLOAD_SESSIONS[user_id]
        if session.get("status") == "paused":
            return await message.reply_text("⏸ This upload session is paused. Tap RESUME or CANCEL.")
        if session.get("status") != "recording":
            return
        source_messages = session.setdefault("source_messages", {})
        source_message_id = int(getattr(message, "id", 0) or 0)
        source_messages[source_message_id] = message
        return

    # Single-file direct upload
    if message.media or message.document:
        copied = await bot.file_storage.store_message(message)
        if not copied:
            return await message.reply_text("❌ File could not be verified in the archive.")
        link_hash = encode_message_id(copied.id)
        bot.state_store.database.record_link(link_hash, "single", copied.id)
        file_url = f"https://t.me/{bot.username}?start={link_hash}"
        await bot.state_store.update_stats(total_links=1, total_files=1)
        return await message.reply_text(f"✅ **File Stored Permanently!**\n\n🔗 Access Link:\n{file_url}")

# ------------------- CALLBACK QUERY HANDLERS ------------------- #

@bot.on_callback_query()
async def callback_router(client: Client, query: CallbackQuery):
    data = query.data
    user_id = query.from_user.id

    if data.startswith("adm_") and not is_admin(user_id):
        return await query.answer("⛔ Unauthorized.", show_alert=True)

    if data == "gen_link_finish":
        session = USER_UPLOAD_SESSIONS.get(user_id)
        if not session or not session.get("source_messages"):
            return await query.answer("No messages received yet!", show_alert=True)

        finish_lock = session.setdefault("finish_lock", asyncio.Lock())
        async with finish_lock:
            session["status"] = "processing"
            source_messages = sort_upload_messages(session.get("source_messages", {}).values())
            archived_by_source_id = session.setdefault("archived", {})
            
            for source_message in source_messages:
                source_id = int(source_message.id)
                if source_id in archived_by_source_id:
                    continue
                copied = await bot.file_storage.store_message(source_message)
                if copied:
                    archived_by_source_id[source_id] = int(copied.id)

            mids = ordered_archive_message_ids(archived_by_source_id)
            if len(mids) == 1:
                code = encode_message_id(mids[0])
                bot.state_store.database.record_link(code, "single", mids[0])
                url = f"https://t.me/{bot.username}?start={code}"
            else:
                manifest_id = await save_batch_manifest(bot, bot.archive_channel_id, mids)
                code = encode_batch_manifest_id(manifest_id)
                bot.state_store.database.record_link("batch_" + code, "batch_manifest", manifest_id)
                url = f"https://t.me/{bot.username}?start=batch_{code}"

            del USER_UPLOAD_SESSIONS[user_id]
            await bot.state_store.update_stats(total_links=1, total_files=len(mids))
            await query.answer("Batch link generated successfully!")
            await query.message.edit_text(f"🎉 **Batch Generated!**\n• Count: `{len(mids)}` messages\n• Shareable Link: {url}")

    elif data == "adm_autodetect":
        await query.answer()
        await query.message.edit_text(_auto_detect_settings_text(), reply_markup=_auto_detect_settings_keyboard())

    elif data == "adm_autodetect_toggle":
        settings = _get_auto_detect_settings()
        settings["enabled"] = not settings.get("enabled", True)
        await save_auto_detect_settings_to_store(bot.state_store, settings)
        await query.answer("Auto detection updated.")
        await query.message.edit_text(_auto_detect_settings_text(), reply_markup=_auto_detect_settings_keyboard())

    elif data == "adm_back":
        await query.answer()
        stats = bot.state_store.get_stats()
        text = "⚙️ **ANIFLIX Admin Dashboard**\n\n" \
               f"👥 Total Users: `{stats.get('total_users', 0)}`\n" \
               f"🔗 Generated Links: `{stats.get('total_links', 0)}`\n" \
               f"📁 Stored Files: `{stats.get('total_files', 0)}`\n" \
               f"👁️ Views/Retrievals: `{stats.get('total_views', 0)}`\n"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 FSUB Settings", callback_data="adm_fsub"), InlineKeyboardButton("📊 Bot Status", callback_data="adm_status")],
            [InlineKeyboardButton("🧠 Auto Detect Settings", callback_data="adm_autodetect")]
        ])
        await query.message.edit_text(text, reply_markup=kb)

def register_handlers(app: Client):
    """Compatibility export required by main.py."""
    if app is not bot:
        raise ValueError("ANIFLIX handlers are bound to the configured bot client.")
    return True

if __name__ == "__main__":
    bot.run()
