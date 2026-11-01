"""ANIFLIX FILE BOT: ordered message links, batches and Telegram-backed admin tools."""
from __future__ import annotations

import asyncio
import html
import logging

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from helper.link_codec import (
    decode_text,
    encode_batch_payload,
    encode_storage_payload,
    parse_link_payload,
)
from helper.state_store import TelegramStateStore
from helper.storage import (
    MAX_BATCH_MESSAGES,
    archive_and_verify,
    deliver_archived_batch,
    deliver_archived_message,
    save_batch_manifest,
)

MAX_SESSION_MESSAGES = 200
ABOUT_TEXT = (
    "✨ <b>ABOUT ME</b>\n\n"
    "<blockquote>"
    "✰ MY NAME: <i>ANIFLIX FILE BOT</i>\n\n"
    "✰ CLONE OF: <i>Md File Store Bot</i>\n\n"
    "✰ MY OWNER: <i>Tanjiro</i>\n\n"
    '✰ UPDATES: <a href="https://t.me/+98iAagsDsjI5NDk1">ANIFLIX NETWORK TAMIL</a>\n\n'
    '✰ SUPPORT: <a href="https://t.me/+1jDuhUQ41hA1YmVl">ANIFLIX SUPPORT GROUP</a>\n\n'
    "✰ VERSION: <code>0.8.12 [V1.10]</code>"
    "</blockquote>"
)


def _status_value(member) -> str:
    status = getattr(member, "status", "")
    return str(getattr(status, "value", status)).lower()


def _is_admin_status(member) -> bool:
    value = _status_value(member)
    return any(word in value for word in ("administrator", "owner", "creator"))


def _get_forwarded_channel_ref(message):
    channel = getattr(message, "forward_from_chat", None)
    message_id = getattr(message, "forward_from_message_id", None)
    if channel is not None and message_id:
        return int(channel.id), int(message_id), channel
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        origin_chat = getattr(origin, "chat", None)
        origin_id = getattr(origin, "message_id", None)
        if origin_chat is not None and origin_id:
            return int(origin_chat.id), int(origin_id), origin_chat
    return None


def _user_label(record: dict) -> str:
    name = " ".join(x for x in (record.get("first_name", ""), record.get("last_name", "")) if x).strip()
    name = name or "Unknown user"
    username = record.get("username")
    suffix = f" (@{username})" if username else ""
    return f"{html.escape(name)}{html.escape(suffix)} — <code>{int(record['id'])}</code>"


class AniflixBot(Client):
    def __init__(self, settings):
        super().__init__(
            name=settings.session_name,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            bot_token=settings.bot_token,
            in_memory=True,
            workers=1,
            max_concurrent_transmissions=1,
        )
        self.settings = settings
        self.archive_channel_id = settings.archive_channel_id
        self.admins = set(settings.admins)
        self.archive_lock = asyncio.Lock()
        self.username = ""
        self.LOGGER = logging.getLogger("aniflix")
        self.state_store = TelegramStateStore(self, self.archive_channel_id)
        self.getlink_sessions: dict[int, dict] = {}
        self.batch_sessions: dict[int, dict] = {}
        self.admin_inputs: dict[int, str] = {}
        self.pending_broadcasts: dict[int, tuple[int, int]] = {}
        self.pending_retrievals: dict[int, str] = {}
        self.global_paused = False

    def is_admin(self, user_id: int | None) -> bool:
        return bool(user_id and int(user_id) in self.state_store.admins(self.settings.owner_id, self.admins))

    async def verify_archive_access(self):
        me = await self.get_me()
        self.username = me.username or ""
        self.me_id = int(me.id)
        if not self.username:
            raise RuntimeError("The bot account has no username. Set one in BotFather.")
        chat = await self.get_chat(self.archive_channel_id)
        member = await self.get_chat_member(self.archive_channel_id, me.id)
        status = _status_value(member)
        if not ("administrator" in status or "owner" in status or "creator" in status):
            raise RuntimeError("Make the bot an administrator in the archive channel before starting.")
        privileges = getattr(member, "privileges", None)
        if "administrator" in status and privileges is not None and not getattr(privileges, "can_post_messages", False):
            raise RuntimeError("The bot administrator must have permission to post messages in the archive channel.")
        self.LOGGER.info("Archive channel access verified: %s (%s)", chat.title, self.archive_channel_id)
        await self.state_store.load()
        self.global_paused = bool(self.state_store.state.get("global_paused", False))

    async def save_state(self):
        await self.state_store.save()

    async def check_fsub(self, user_id: int) -> tuple[bool, list[dict]]:
        missing = []
        for channel in self.state_store.state.get("fsub_channels", []):
            try:
                member = await self.get_chat_member(int(channel["chat_id"]), int(user_id))
                value = _status_value(member)
                is_member = any(word in value for word in ("member", "administrator", "owner", "creator"))
                if "restricted" in value:
                    is_member = bool(getattr(member, "is_member", False))
                if not is_member:
                    missing.append(channel)
            except Exception:
                missing.append(channel)
        return not missing, missing

    def fsub_keyboard(self, channels: list[dict]) -> InlineKeyboardMarkup:
        rows = []
        for channel in channels:
            rows.append([InlineKeyboardButton(
                channel.get("label") or channel.get("title") or "JOIN CHANNEL",
                url=channel["invite_url"],
            )])
        rows.append([InlineKeyboardButton("✅ TRY AGAIN", callback_data="fsub:check")])
        return InlineKeyboardMarkup(rows)

    async def send_saved_content(self, chat_id: int, value: dict, reply_markup=None):
        if value.get("kind") == "photo" and value.get("photo_id"):
            return await self.send_photo(
                chat_id,
                value["photo_id"],
                caption=value.get("text") or "",
                reply_markup=reply_markup,
                parse_mode="disabled",
            )
        return await self.send_message(
            chat_id,
            value.get("text") or "Welcome to ANIFLIX FILE BOT!",
            reply_markup=reply_markup,
            parse_mode="disabled",
            disable_web_page_preview=True,
        )

    async def send_welcome(self, chat_id: int):
        return await self.send_saved_content(chat_id, self.state_store.state.get("welcome", {}))

    async def send_fsub_prompt(self, chat_id: int, channels: list[dict]):
        value = self.state_store.state.get("fsub_message", {})
        return await self.send_saved_content(chat_id, value, self.fsub_keyboard(channels))

    async def is_user_subscribed(self, user_id: int) -> bool:
        ok, _ = await self.check_fsub(user_id)
        return ok


def register_handlers(bot: AniflixBot):
    def admin_only(message) -> bool:
        return bool(message.from_user and bot.is_admin(message.from_user.id))

    def admin_keyboard() -> InlineKeyboardMarkup:
        paused = bool(bot.state_store.state.get("global_paused", False))
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼 Welcome Message", callback_data="admin:welcome"),
             InlineKeyboardButton("📝 FSUB Message", callback_data="admin:fsubmsg")],
            [InlineKeyboardButton("📢 FSUB Channels", callback_data="admin:fsub")],
            [InlineKeyboardButton("👥 Users", callback_data="admin:users") ,
             InlineKeyboardButton("🔗 Links Count", callback_data="admin:links")],
            [InlineKeyboardButton("📊 Bot Statistics", callback_data="admin:stats"),
             InlineKeyboardButton("🗄 Storage Info", callback_data="admin:storage")],
            [InlineKeyboardButton("➕ Add Admin", callback_data="admin:addadmin"),
             InlineKeyboardButton("➖ Remove Admin", callback_data="admin:removeadmin")],
            [InlineKeyboardButton("⏸ Pause All" if not paused else "▶️ Resume All", callback_data="admin:pause")],
        ])

    async def update_getlink_panel(user_id: int):
        session = bot.getlink_sessions.get(user_id)
        if not session:
            return
        count = len(session["items"])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 GENERATE LINK", callback_data="getlink:generate")],
            [InlineKeyboardButton("❌ CANCEL", callback_data="getlink:cancel"),
             InlineKeyboardButton("⏸ PAUSE" if not session["paused"] else "▶️ RESUME", callback_data="getlink:pause")],
        ])
        text = (
            f"<b>Stored Messages: {count}</b>\n\n"
            "Want to add more messages? Just send them here!\n\n"
            "Send files, text, photos, videos, documents, stickers, audio or other supported messages."
        )
        try:
            await bot.edit_message_text(user_id, session["panel_id"], text, reply_markup=keyboard)
        except Exception:
            sent = await bot.send_message(user_id, text, reply_markup=keyboard)
            session["panel_id"] = sent.id

    async def show_users_page(chat_id: int, page: int, edit_message=None):
        records = sorted(bot.state_store.users.values(), key=lambda x: int(x["id"]))
        page_size = 15
        total = len(records)
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, pages - 1))
        selected = records[page * page_size:(page + 1) * page_size]
        lines = [f"<b>Registered Users: {total}</b>", f"Page {page + 1}/{pages}", ""]
        lines.extend(f"{page * page_size + index + 1}. {_user_label(record)}" for index, record in enumerate(selected))
        rows = []
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"users:{page - 1}"))
        if page + 1 < pages:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"users:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"users:{page}"),
                     InlineKeyboardButton("⬅️ Admin", callback_data="admin:home")])
        markup = InlineKeyboardMarkup(rows)
        text = "\n".join(lines)
        if edit_message is not None:
            await edit_message.edit_text(text, reply_markup=markup, parse_mode="html")
        else:
            await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="html")

    async def increment_links(kind: str, messages_count: int):
        counts = bot.state_store.state.setdefault("links_count", {})
        counts[kind] = int(counts.get(kind, 0)) + 1
        counts["messages"] = int(counts.get("messages", 0)) + int(messages_count)
        await bot.save_state()

    async def increment_failed():
        counts = bot.state_store.state.setdefault("links_count", {})
        counts["failed"] = int(counts.get("failed", 0)) + 1
        try:
            await bot.save_state()
        except Exception:
            bot.LOGGER.exception("Could not persist failed-operation counter")

    async def build_result_link(archive_ids: list[int]) -> str:
        if len(archive_ids) == 1:
            payload = encode_storage_payload(bot.archive_channel_id, archive_ids[0])
        else:
            manifest_id = await save_batch_manifest(bot, bot.archive_channel_id, archive_ids)
            payload = encode_batch_payload(bot.archive_channel_id, manifest_id)
        return f"https://t.me/{bot.username}?start={payload}"

    async def process_batch_range(admin_id: int, first: dict, last_ref: tuple[int, int, object]):
        source_chat_id, last_message_id, source_chat = last_ref
        first_message_id = int(first["message_id"])
        if source_chat_id != int(first["chat_id"]):
            raise ValueError("Both forwarded messages must come from the same channel.")
        if last_message_id < first_message_id:
            raise ValueError("The last forwarded message must be after the first message.")
        range_count = last_message_id - first_message_id + 1
        if range_count > MAX_BATCH_MESSAGES:
            raise ValueError(f"This batch is too large. The maximum range is {MAX_BATCH_MESSAGES} message IDs.")
        member = await bot.get_chat_member(source_chat_id, bot.me_id)
        if not _is_admin_status(member):
            raise ValueError(
                "To archive a channel range, add this bot as an administrator to the source channel. "
                "Only archive messages you own or have permission to republish."
            )
        source_messages = await bot.get_messages(
            source_chat_id,
            message_ids=list(range(first_message_id, last_message_id + 1)),
        )
        if not isinstance(source_messages, list):
            source_messages = [source_messages]
        source_messages = sorted(
            [item for item in source_messages if item and not getattr(item, "empty", False)],
            key=lambda item: int(item.id),
        )
        if not source_messages:
            raise ValueError("No accessible messages were found in that range.")

        archived_ids = []
        async with bot.archive_lock:
            for index, source_message in enumerate(source_messages, start=1):
                if getattr(source_message, "service", None):
                    continue
                archived_ids.append(await archive_and_verify(bot, source_message, bot.archive_channel_id))
        if not archived_ids:
            raise ValueError("There were no supported messages to archive in that range.")
        link = await build_result_link(archived_ids)
        await increment_links("batch", len(archived_ids))
        return link, len(archived_ids), range_count

    @bot.on_message(filters.private & filters.command("start"))
    async def start_handler(client: AniflixBot, message):
        if message.from_user:
            try:
                await client.state_store.register_user(message.from_user)
            except Exception:
                client.LOGGER.exception("Could not register user %s", message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        payload_arg = parts[1].strip() if len(parts) > 1 else ""
        if payload_arg and not client.is_admin(message.from_user.id if message.from_user else None):
            subscribed, missing = await client.check_fsub(message.from_user.id)
            if not subscribed:
                client.pending_retrievals[message.from_user.id] = payload_arg
                await client.send_fsub_prompt(message.chat.id, missing)
                return
        if not payload_arg:
            if message.from_user and not client.is_admin(message.from_user.id):
                subscribed, missing = await client.check_fsub(message.from_user.id)
                if not subscribed:
                    await client.send_fsub_prompt(message.chat.id, missing)
                    return
            await client.send_welcome(message.chat.id)
            return

        try:
            decoded = decode_text(payload_arg)
            kind, channel_id, message_id = parse_link_payload(decoded, client.archive_channel_id)
        except ValueError:
            await message.reply_text("❌ This link is invalid or does not belong to this archive.")
            return

        status = await message.reply_text("⏳ Checking the archive and preparing your messages...")
        try:
            if kind == "single":
                await deliver_archived_message(
                    client, channel_id, message_id, message.chat.id, client.settings.protect_content
                )
                await status.delete()
            else:
                delivered, missing = await deliver_archived_batch(
                    client, channel_id, message_id, message.chat.id, client.settings.protect_content
                )
                result = f"✅ Delivered {delivered} message(s) in original order."
                if missing:
                    result += f"\n⚠️ {missing} archived message(s) were unavailable."
                await status.edit_text(result)
        except FloodWait as exc:
            wait_seconds = int(getattr(exc, "value", getattr(exc, "x", 1)))
            await status.edit_text(f"Telegram is busy. Please open this link again in about {wait_seconds} seconds.")
        except LookupError as exc:
            await status.edit_text(f"❌ {exc}")
        except Exception:
            client.LOGGER.exception("Archive retrieval failed for message %s", message_id)
            await status.edit_text("❌ I couldn't retrieve these messages right now. Please try again later.")

    @bot.on_message(filters.private & filters.command("getlink"))
    async def getlink_handler(client: AniflixBot, message):
        if not admin_only(message):
            await message.reply_text("This command is available to bot admins only.")
            return
        if client.global_paused:
            await message.reply_text("⏸ New upload sessions are temporarily paused by an admin.")
            return
        user_id = message.from_user.id
        if user_id in client.getlink_sessions:
            await message.reply_text("You already have an active /getlink session. Continue sending messages or use its buttons.")
            return
        status = await message.reply_text(
            "<b>Stored Messages: 0</b>\n\n"
            "Want to add more messages? Just send them here!\n\n"
            "Send one or multiple messages, then press GENERATE LINK.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 GENERATE LINK", callback_data="getlink:generate")],
                [InlineKeyboardButton("❌ CANCEL", callback_data="getlink:cancel"),
                 InlineKeyboardButton("⏸ PAUSE", callback_data="getlink:pause")],
            ]),
        )
        client.getlink_sessions[user_id] = {
            "chat_id": message.chat.id,
            "items": [],
            "saved_ids": [],
            "panel_id": status.id,
            "paused": False,
            "processing": False,
        }

    @bot.on_message(filters.private & filters.command("batch"))
    async def batch_handler(client: AniflixBot, message):
        if not admin_only(message):
            await message.reply_text("This command is available to bot admins only.")
            return
        if client.global_paused:
            await message.reply_text("⏸ New batch operations are temporarily paused by an admin.")
            return
        client.batch_sessions[message.from_user.id] = {"step": "first"}
        await message.reply_text(
            "<b>Batch Link</b>\n\nForward the FIRST message from your own or an authorized source channel, with the forward tag visible."
        )

    @bot.on_message(filters.private & filters.command("about"))
    async def about_handler(client: AniflixBot, message):
        await message.reply_text(ABOUT_TEXT, parse_mode="html", disable_web_page_preview=True)

    @bot.on_message(filters.private & filters.command("help"))
    async def help_handler(client: AniflixBot, message):
        await message.reply_text(
            "<b>ANIFLIX FILE BOT</b>\n\n"
            "<b>Admins</b>\n"
            "• /getlink — collect one or more messages, then generate one link.\n"
            "• /batch — forward the first and last messages from an authorized channel range.\n"
            "• /admin — open the admin panel.\n"
            "• /broadcast — reply to a message with this command to prepare a broadcast.\n"
            "• /users — view registered users.\n\n"
            "<b>Everyone</b>\n"
            "Open a stored link to receive its message or ordered batch. Supported message types depend on what Telegram allows the bot to copy.",
            parse_mode="html",
        )

    @bot.on_message(filters.private & filters.command("status"))
    async def status_handler(client: AniflixBot, message):
        if not admin_only(message):
            return
        await message.reply_text(
            "✅ Bot is connected.\n"
            f"Archive channel: <code>{client.archive_channel_id}</code>\n"
            f"Registered users: {len(client.state_store.users)}\n"
            f"Force-sub channels: {len(client.state_store.state.get('fsub_channels', []))}/10\n"
            f"Global pause: {'ON' if client.global_paused else 'OFF'}",
            parse_mode="html",
        )

    @bot.on_message(filters.private & filters.command("admin"))
    async def admin_handler(client: AniflixBot, message):
        if not admin_only(message):
            await message.reply_text("This panel is available to bot admins only.")
            return
        await message.reply_text("<b>⚙️ ANIFLIX ADMIN PANEL</b>\nChoose a setting:", reply_markup=admin_keyboard())

    @bot.on_message(filters.private & filters.command("cancel"))
    async def cancel_handler(client: AniflixBot, message):
        if not admin_only(message):
            return
        user_id = message.from_user.id
        removed = False
        if user_id in client.admin_inputs:
            del client.admin_inputs[user_id]
            removed = True
        if user_id in client.batch_sessions:
            del client.batch_sessions[user_id]
            removed = True
        session = client.getlink_sessions.get(user_id)
        if session and not session.get("processing"):
            del client.getlink_sessions[user_id]
            try:
                await client.edit_message_text(user_id, session["panel_id"], "❌ Upload session cancelled.")
            except Exception:
                pass
            removed = True
        if removed:
            await message.reply_text("✅ Current setup/session cancelled.")
        else:
            await message.reply_text("There is no cancellable setup or session right now.")

    @bot.on_message(filters.private & filters.command("users"))
    async def users_handler(client: AniflixBot, message):
        if not admin_only(message):
            await message.reply_text("This command is available to bot admins only.")
            return
        await show_users_page(message.chat.id, 0)

    @bot.on_message(filters.private & filters.command("broadcast"))
    async def broadcast_handler(client: AniflixBot, message):
        if not admin_only(message):
            await message.reply_text("This command is available to bot admins only.")
            return
        if not message.reply_to_message:
            await message.reply_text("Reply to the message you want to broadcast, then send /broadcast.")
            return
        admin_id = message.from_user.id
        client.pending_broadcasts[admin_id] = (message.chat.id, message.reply_to_message.id)
        await message.reply_text(
            f"Broadcast this replied-to message to {len(client.state_store.users)} registered users?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ CONFIRM BROADCAST", callback_data="broadcast:confirm"),
                 InlineKeyboardButton("❌ CANCEL", callback_data="broadcast:cancel")],
            ]),
        )

    @bot.on_message(filters.private)
    async def collect_private_messages(client: AniflixBot, message):
        if not message.from_user or not client.is_admin(message.from_user.id):
            return
        user_id = message.from_user.id
        if message.text and message.text.startswith("/"):
            return

        input_mode = client.admin_inputs.get(user_id)
        if input_mode:
            if message.service:
                await message.reply_text("Please send a photo with caption or a text message.")
                return
            if input_mode in {"welcome", "fsub_message"}:
                if message.photo:
                    content_text = message.caption or ""
                    if len(content_text.encode("utf-8")) > 1800:
                        await message.reply_text("Please keep the caption under 1800 UTF-8 bytes so it can be saved safely.")
                        return
                    saved = {"kind": "photo", "photo_id": message.photo[-1].file_id, "text": content_text}
                elif message.text:
                    if len(message.text.encode("utf-8")) > 1800:
                        await message.reply_text("Please keep the welcome/FSUB text under 1800 UTF-8 bytes so it can be saved safely.")
                        return
                    saved = {"kind": "text", "photo_id": None, "text": message.text}
                else:
                    await message.reply_text("Please send a photo with caption or a text message.")
                    return
                client.state_store.state[input_mode] = saved
                await client.save_state()
                del client.admin_inputs[user_id]
                await message.reply_text("✅ Message saved and will be used from now on.")
                return
            if input_mode == "fsub_add":
                try:
                    pieces = [x.strip() for x in (message.text or "").split("|", 2)]
                    if len(pieces) < 2:
                        raise ValueError("Use: @channel | https://t.me/channel | button label")
                    channel_ref, invite_url = pieces[0], pieces[1]
                    label = pieces[2] if len(pieces) > 2 else "JOIN CHANNEL"
                    if not invite_url.startswith("https://t.me/"):
                        raise ValueError("The invite link must start with https://t.me/")
                    chat = await client.get_chat(channel_ref)
                    member = await client.get_chat_member(chat.id, client.me_id)
                    if not _is_admin_status(member):
                        raise ValueError("Make this bot an administrator in that channel first.")
                    if len(client.state_store.state.get("fsub_channels", [])) >= 10:
                        raise ValueError("You can add a maximum of 10 force-sub channels.")
                    entries = client.state_store.state.setdefault("fsub_channels", [])
                    if any(int(x["chat_id"]) == int(chat.id) for x in entries):
                        raise ValueError("This channel is already in the force-sub list.")
                    entries.append({"chat_id": int(chat.id), "title": chat.title or label, "label": label, "invite_url": invite_url})
                    await client.save_state()
                    del client.admin_inputs[user_id]
                    await message.reply_text(f"✅ Added {html.escape(chat.title or label)} to force subscription.", parse_mode="html")
                except Exception as exc:
                    await message.reply_text(f"❌ {html.escape(str(exc))}\nSend the channel details again or /cancel.", parse_mode="html")
                return
            if input_mode in {"add_admin", "remove_admin"}:
                try:
                    target_id = int((message.text or "").strip())
                    if input_mode == "add_admin":
                        changed = await client.state_store.add_admin(target_id, client.settings.owner_id)
                        text = "✅ Admin added." if changed else "That user is already an admin."
                    else:
                        changed = await client.state_store.remove_admin(target_id, client.settings.owner_id, client.admins)
                        text = "✅ Admin removed." if changed else "That user is not an extra admin."
                    del client.admin_inputs[user_id]
                    await message.reply_text(text)
                except Exception as exc:
                    await message.reply_text(f"❌ {html.escape(str(exc))}\nSend a numeric Telegram user ID or /cancel.", parse_mode="html")
                return

        batch_session = client.batch_sessions.get(user_id)
        if batch_session:
            forwarded = _get_forwarded_channel_ref(message)
            if not forwarded:
                await message.reply_text("Please forward a channel message with its forward tag visible.")
                return
            source_id, source_message_id, source_chat = forwarded
            if batch_session["step"] == "first":
                client.batch_sessions[user_id] = {
                    "step": "last",
                    "chat_id": source_id,
                    "message_id": source_message_id,
                    "title": getattr(source_chat, "title", "Source channel"),
                }
                await message.reply_text("✅ First message received. Now forward the LAST message from the same channel.")
                return
            first = batch_session
            del client.batch_sessions[user_id]
            status = await message.reply_text("⏳ Collecting and archiving the authorized channel range in order...")
            try:
                link, saved_count, range_count = await process_batch_range(user_id, first, forwarded)
                await status.edit_text(
                    f"✅ Batch link generated.\nSaved messages: {saved_count}\nRange checked: {range_count}\n\n{link}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 OPEN BATCH", url=link)]]),
                    disable_web_page_preview=True,
                )
            except Exception as exc:
                client.LOGGER.exception("Batch generation failed for admin %s", user_id)
                await increment_failed()
                await status.edit_text(f"❌ Batch failed: {html.escape(str(exc))}")
            return

        session = client.getlink_sessions.get(user_id)
        if session:
            if session["paused"]:
                await message.reply_text("⏸ This /getlink session is paused. Press RESUME to continue adding messages.")
                return
            if session["processing"]:
                await message.reply_text("The current session is being saved. Please wait until the link is generated.")
                return
            if message.service:
                await message.reply_text("That service message type cannot be added. Send text, media, stickers or another regular message.")
                return
            if int(message.id) not in session["items"]:
                if len(session["items"]) >= MAX_SESSION_MESSAGES:
                    await message.reply_text(f"This session reached its {MAX_SESSION_MESSAGES}-message limit. Generate this link, then start another /getlink session.")
                    return
                session["items"].append(int(message.id))
                session["items"].sort()
                await update_getlink_panel(user_id)
            return

    @bot.on_callback_query()
    async def callback_handler(client: AniflixBot, query):
        user_id = int(query.from_user.id)
        data = query.data or ""
        if data.startswith("fsub:"):
            if data == "fsub:check":
                subscribed, missing = await client.check_fsub(user_id)
                if not subscribed:
                    await query.answer("You still need to join the required channels.", show_alert=True)
                    return
                await query.answer("Subscription verified!")
                try:
                    await query.message.delete()
                except Exception:
                    pass
                pending = client.pending_retrievals.pop(user_id, None)
                if pending:
                    try:
                        decoded = decode_text(pending)
                        kind, channel_id, message_id = parse_link_payload(decoded, client.archive_channel_id)
                        status = await client.send_message(user_id, "⏳ Subscription verified. Preparing your messages...")
                        if kind == "single":
                            await deliver_archived_message(client, channel_id, message_id, user_id, client.settings.protect_content)
                            await status.delete()
                        else:
                            delivered, missing_count = await deliver_archived_batch(client, channel_id, message_id, user_id, client.settings.protect_content)
                            text = f"✅ Delivered {delivered} message(s) in original order."
                            if missing_count:
                                text += f"\n⚠️ {missing_count} message(s) were unavailable."
                            await status.edit_text(text)
                    except Exception:
                        client.LOGGER.exception("Pending retrieval failed after FSUB check")
                        await client.send_message(user_id, "❌ I couldn't retrieve that link. Please open it again.")
                else:
                    await client.send_welcome(user_id)
                return
            return

        if data.startswith("getlink:"):
            if not client.is_admin(user_id):
                await query.answer("Admins only.", show_alert=True)
                return
            session = client.getlink_sessions.get(user_id)
            if not session:
                await query.answer("This upload session has expired.", show_alert=True)
                return
            if data == "getlink:pause":
                session["paused"] = not session["paused"]
                await query.answer("Paused." if session["paused"] else "Resumed.")
                await update_getlink_panel(user_id)
                return
            if data == "getlink:cancel":
                if session["processing"]:
                    await query.answer("The session is currently saving; it cannot be cancelled mid-copy.", show_alert=True)
                    return
                del client.getlink_sessions[user_id]
                await query.answer("Session cancelled.")
                await query.message.edit_text("❌ Upload session cancelled. No link was generated.")
                return
            if data == "getlink:generate":
                if session["processing"]:
                    await query.answer("Already processing.", show_alert=True)
                    return
                if not session["items"]:
                    await query.answer("Send at least one message first.", show_alert=True)
                    return
                session["processing"] = True
                await query.answer("Saving messages in order...")
                try:
                    saved_ids = session["saved_ids"]
                    for source_index in range(len(saved_ids), len(session["items"])):
                        source_id = session["items"][source_index]
                        source_message = await client.get_messages(session["chat_id"], source_id)
                        if not source_message or getattr(source_message, "empty", False):
                            raise RuntimeError(f"Source message {source_id} is no longer available")
                        async with client.archive_lock:
                            archived_id = await archive_and_verify(client, source_message, client.archive_channel_id)
                        saved_ids.append(archived_id)
                        try:
                            await client.edit_message_text(
                                user_id,
                                session["panel_id"],
                                f"⏳ Saving in original order: {len(saved_ids)}/{len(session['items'])}",
                            )
                        except Exception:
                            pass
                    link = await build_result_link(saved_ids)
                    await increment_links("getlink", len(saved_ids))
                    del client.getlink_sessions[user_id]
                    await query.message.edit_text(
                        f"✅ Link generated successfully.\nStored messages: {len(saved_ids)}\n\n{link}",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 OPEN STORED MESSAGES", url=link)]]),
                        disable_web_page_preview=True,
                    )
                except FloodWait as exc:
                    session["processing"] = False
                    await increment_failed()
                    seconds = int(getattr(exc, "value", getattr(exc, "x", 1)))
                    await query.message.edit_text(
                        f"⚠️ Telegram rate-limited this operation. {len(session['saved_ids'])}/{len(session['items'])} messages are already verified. Wait about {seconds} seconds, then press GENERATE LINK to resume.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 GENERATE LINK", callback_data="getlink:generate")],
                                                          [InlineKeyboardButton("❌ CANCEL", callback_data="getlink:cancel")]]),
                    )
                except Exception as exc:
                    session["processing"] = False
                    await increment_failed()
                    client.LOGGER.exception("/getlink generation failed for admin %s", user_id)
                    await query.message.edit_text(
                        f"❌ Could not finish the session: {html.escape(str(exc))}\nVerified messages: {len(session['saved_ids'])}/{len(session['items'])}. Press GENERATE LINK to resume after fixing the issue.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 GENERATE LINK", callback_data="getlink:generate")],
                                                          [InlineKeyboardButton("❌ CANCEL", callback_data="getlink:cancel")]]),
                    )
                return

        if data.startswith("users:"):
            if not client.is_admin(user_id):
                await query.answer("Admins only.", show_alert=True)
                return
            try:
                page = int(data.split(":", 1)[1])
            except ValueError:
                page = 0
            await query.answer()
            await show_users_page(user_id, page, query.message)
            return

        if data.startswith("broadcast:"):
            if not client.is_admin(user_id):
                await query.answer("Admins only.", show_alert=True)
                return
            if data == "broadcast:cancel":
                client.pending_broadcasts.pop(user_id, None)
                await query.answer("Broadcast cancelled.")
                await query.message.edit_text("❌ Broadcast cancelled.")
                return
            pending = client.pending_broadcasts.get(user_id)
            if not pending:
                await query.answer("No pending broadcast.", show_alert=True)
                return
            await query.answer("Broadcast started.")
            source_chat_id, source_message_id = pending
            try:
                source = await client.get_messages(source_chat_id, source_message_id)
                if not source or getattr(source, "empty", False):
                    raise LookupError("The replied-to source message is no longer available")
                records = list(client.state_store.users.values())
                sent_count = 0
                failed_count = 0
                await query.message.edit_text(f"📣 Broadcasting to {len(records)} users...")
                for record in records:
                    try:
                        await source.copy(chat_id=int(record["id"]), disable_notification=True)
                        sent_count += 1
                    except FloodWait as exc:
                        await asyncio.sleep(int(getattr(exc, "value", getattr(exc, "x", 1))))
                        try:
                            await source.copy(chat_id=int(record["id"]), disable_notification=True)
                            sent_count += 1
                        except Exception:
                            failed_count += 1
                    except Exception:
                        failed_count += 1
                client.pending_broadcasts.pop(user_id, None)
                await query.message.edit_text(
                    f"✅ Broadcast completed.\nDelivered: {sent_count}\nFailed: {failed_count}\nTotal: {len(records)}"
                )
            except Exception as exc:
                client.LOGGER.exception("Broadcast failed")
                await query.message.edit_text(f"❌ Broadcast could not start: {html.escape(str(exc))}")
            return

        if not client.is_admin(user_id):
            await query.answer("Admins only.", show_alert=True)
            return

        if data == "admin:welcome":
            client.admin_inputs[user_id] = "welcome"
            await query.answer()
            await query.message.reply_text("Send the new welcome message as text, or send a photo with caption. The latest one will be saved.")
        elif data == "admin:fsubmsg":
            client.admin_inputs[user_id] = "fsub_message"
            await query.answer()
            await query.message.reply_text("Send the force-subscription prompt as text, or send a photo with caption.")
        elif data == "admin:fsub":
            channels = client.state_store.state.get("fsub_channels", [])
            rows = [[InlineKeyboardButton("➕ Add Channel", callback_data="admin:fsubadd")]]
            for index, item in enumerate(channels):
                label = (item.get("title") or "Channel")[:32]
                rows.append([InlineKeyboardButton(f"❌ Remove {label}", callback_data=f"admin:fsubrm:{index}")])
            rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:home")])
            await query.answer()
            await query.message.edit_text(f"<b>Force Subscription: {len(channels)}/10 channels</b>", reply_markup=InlineKeyboardMarkup(rows), parse_mode="html")
        elif data == "admin:fsubadd":
            if len(client.state_store.state.get("fsub_channels", [])) >= 10:
                await query.answer("Maximum 10 channels allowed.", show_alert=True)
                return
            client.admin_inputs[user_id] = "fsub_add"
            await query.answer()
            await query.message.reply_text("Send: <code>@channel or -100 ID | https://t.me/invite | button label</code>\nThe bot must be an administrator in that channel.", parse_mode="html")
        elif data.startswith("admin:fsubrm:"):
            try:
                index = int(data.rsplit(":", 1)[1])
                channels = client.state_store.state.get("fsub_channels", [])
                removed = channels.pop(index)
                await client.save_state()
                await query.answer("Channel removed.")
                await query.message.edit_text(f"Removed {html.escape(removed.get('title', 'channel'))}.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ FSUB Settings", callback_data="admin:fsub")]]), parse_mode="html")
            except Exception:
                await query.answer("Could not remove that channel.", show_alert=True)
        elif data == "admin:users":
            await query.answer()
            await show_users_page(user_id, 0, query.message)
        elif data == "admin:links":
            counts = client.state_store.state.get("links_count", {})
            await query.answer()
            await query.message.edit_text(
                "<b>🔗 LINKS COUNT</b>\n\n"
                f"/getlink links: {int(counts.get('getlink', 0))}\n"
                f"/batch links: {int(counts.get('batch', 0))}\n"
                f"Total links: {int(counts.get('getlink', 0)) + int(counts.get('batch', 0))}\n"
                f"Messages stored: {int(counts.get('messages', 0))}\n"
                f"Failed operations: {int(counts.get('failed', 0))}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]]),
                parse_mode="html",
            )
        elif data == "admin:stats":
            counts = client.state_store.state.get("links_count", {})
            await query.answer()
            await query.message.edit_text(
                "<b>📊 BOT STATISTICS</b>\n\n"
                f"Registered users: {len(client.state_store.users)}\n"
                f"Total generated links: {int(counts.get('getlink', 0)) + int(counts.get('batch', 0))}\n"
                f"Messages archived: {int(counts.get('messages', 0))}\n"
                f"Force-sub channels: {len(client.state_store.state.get('fsub_channels', []))}/10\n"
                f"Current /getlink sessions: {len(client.getlink_sessions)}\n"
                f"Current /batch sessions: {len(client.batch_sessions)}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]]),
                parse_mode="html",
            )
        elif data == "admin:storage":
            await query.answer()
            await query.message.edit_text(
                "<b>🗄 STORAGE INFORMATION</b>\n\n"
                f"Archive channel: <code>{client.archive_channel_id}</code>\n"
                "Media files and metadata are stored as Telegram channel messages.\n"
                "The bot does not use local media storage or an external database.\n"
                "Keep the archive channel and its messages intact for links to work.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]]),
                parse_mode="html",
            )
        elif data == "admin:addadmin":
            client.admin_inputs[user_id] = "add_admin"
            await query.answer()
            await query.message.reply_text("Send the numeric Telegram user ID to add as an admin.")
        elif data == "admin:removeadmin":
            extras = client.state_store.state.get("extra_admins", [])
            rows = [[InlineKeyboardButton(str(value), callback_data=f"admin:rmadmin:{int(value)}")] for value in extras]
            rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:home")])
            await query.answer()
            await query.message.edit_text("Choose an extra admin to remove:", reply_markup=InlineKeyboardMarkup(rows))
        elif data.startswith("admin:rmadmin:"):
            try:
                target = int(data.rsplit(":", 1)[1])
                await client.state_store.remove_admin(target, client.settings.owner_id, client.admins)
                await query.answer("Admin removed.")
                await query.message.edit_text("✅ Admin removed.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]]))
            except Exception as exc:
                await query.answer(str(exc), show_alert=True)
        elif data == "admin:pause":
            client.global_paused = not client.global_paused
            client.state_store.state["global_paused"] = client.global_paused
            await client.save_state()
            await query.answer("Global upload pause updated.")
            await query.message.edit_reply_markup(reply_markup=admin_keyboard())
        elif data == "admin:home":
            await query.answer()
            await query.message.edit_text("<b>⚙️ ANIFLIX ADMIN PANEL</b>\nChoose a setting:", reply_markup=admin_keyboard(), parse_mode="html")


