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
ADMIN_PENDING_ACTIONS: Dict[int, str] = {}


def _get_archive_channel_id() -> int:
    return bot.settings.archive_channel_id


def _get_admins() -> List[int]:
    return list(bot.settings.admins)


def _get_fsub_channels() -> List[Dict]:
    return getattr(config, "FSUB_CHANNELS", [])


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
        self.state_store = StateStore(self, self.archive_channel_id)
        self.storage = Storage(self, self.archive_channel_id)
        self.batch_reader = None
        if self.settings.batch_user_session_string:
            # Optional user authorization is isolated to /batch source reads.
            # Never log or persist the session string in project files.
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
            # Initialize remote state safely without get_chat_history
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
        logger.info("Bot stopped cleanly.")


bot = AniflixBot()


# ------------------- HELPER FUNCTIONS ------------------- #

def is_admin(user_id: int) -> bool:
    if user_id in _get_admins():
        return True
    admins = bot.state_store.get_admins()
    return user_id in admins


def is_owner(user_id: int) -> bool:
    return user_id == bot.settings.owner_id


async def check_fsub(client: Client, user_id: int) -> List[Dict]:
    """Returns a list of FSUB channels that the user hasn't joined yet."""
    fsub_channels = bot.state_store.get_fsub_channels()
    if not fsub_channels:
        fsub_channels = _get_fsub_channels()

    unjoined = []
    for ch in fsub_channels:
        chat_identifier = ch.get("id") or ch.get("username")
        try:
            await client.get_chat_member(chat_identifier, user_id)
        except UserNotParticipant:
            unjoined.append(ch)
        except Exception as e:
            # Fail closed: a missing bot admin right must not silently bypass FSUB.
            logger.warning("FSUB membership check failed for %s: %s", chat_identifier, e)
            unjoined.append(ch)
    return unjoined


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
    """Deliver a single file, a new ordered manifest, or a legacy bounded range."""
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
                if await bot.storage.deliver_message(
                    user_id, message_id, bot.settings.protect_content
                ):
                    sent_count += 1
                else:
                    failed_count += 1
                await asyncio.sleep(0.1)
            await bot.state_store.update_stats(total_views=1)
            if sent_count == 0:
                return await message.reply_text("❌ No files in this batch are available.")
            if failed_count:
                await message.reply_text(
                    f"⚠️ Legacy batch delivered: `{sent_count}` files; `{failed_count}` unavailable."
                )
            return

        message_id = decode_message_id(param)
        if not await bot.storage.deliver_message(
            user_id, message_id, bot.settings.protect_content
        ):
            return await message.reply_text("❌ This file is no longer available in the archive.")
        await bot.state_store.update_stats(total_views=1)
    except Exception as exc:
        logger.warning("Start payload could not be delivered: %s", exc)
        await message.reply_text("❌ This link is corrupted, invalid, or expired.")


async def _reply_welcome(message: Message, first_name: str = "there"):
    welcome = bot.state_store.get("welcome_message") or (
        f"👋 Hello, **{first_name}**!\n\n"
        "Welcome to **ANIFLIX FILE STORE BOT**.\n"
        "Send me media or use `/getlink` and `/batch` to store and generate sharing links."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Channel", url="https://t.me/Aniflix_Bots"),
         InlineKeyboardButton("Support", url="https://t.me/Aniflix_Support")]
    ])
    await message.reply_text(welcome, reply_markup=kb)


async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    await bot.state_store.record_user(user_id)
    args = getattr(message, "command", []) or []
    if len(args) > 1:
        param = args[1]
        unjoined = await check_fsub(client, user_id)
        if unjoined:
            fsub_text = bot.state_store.get("fsub_message") or (
                "⚠️ **Access Denied!**\n\n"
                "You must join our official channels to access files stored here.\n"
                "Join each channel below and tap **Try Again**."
            )
            return await message.reply_text(
                fsub_text,
                reply_markup=get_fsub_keyboard(unjoined, redirect_data=param)
            )
        return await _deliver_start_payload(client, user_id, message, param)
    return await _reply_welcome(message, message.from_user.first_name)


bot.on_message(filters.command("start") & filters.private)(start_handler)


# ------------------- COMMAND: /GETLINK ------------------- #

@bot.on_message(filters.command("getlink") & filters.private)
async def getlink_handler(client: Client, message: Message):
    user_id = message.from_user.id
    USER_UPLOAD_SESSIONS[user_id] = {
        "status": "recording",
        "message_ids": []
    }

    buttons = [
        [InlineKeyboardButton("✅ GENERATE LINK", callback_data="gen_link_finish")],
        [InlineKeyboardButton("⏸ PAUSE", callback_data="gen_link_pause"),
         InlineKeyboardButton("❌ CANCEL", callback_data="gen_link_cancel")]
    ]
    await message.reply_text(
        "📥 **Multi-Message Upload Session Started**\n\n"
        "Forward or send any files, videos, documents, or photos.\n"
        "Current Stored Count: **0**\n\n"
        "Tap **GENERATE LINK** when finished.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


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
         InlineKeyboardButton("📬 Broadcast", callback_data="adm_broadcast")]
    ])
    await message.reply_text(text, reply_markup=kb)


@bot.on_message(filters.command("status") & filters.private)
async def status_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return
    stats = bot.state_store.get_stats()
    await message.reply_text(
        f"📊 **Bot Status & Health**\n\n"
        f"• Status: `Online & Running`\n"
        f"• Registered Users: `{stats.get('total_users', 0)}`\n"
        f"• Total Links Created: `{stats.get('total_links', 0)}`\n"
        f"• Total Files Copied: `{stats.get('total_files', 0)}`\n"
        f"• Total Link Views: `{stats.get('total_views', 0)}`"
    )


@bot.on_message(filters.command("users") & filters.private)
async def users_count_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return
    users = bot.state_store.get("users", [])
    await message.reply_text(f"👥 Total Registered Users: **{len(users)}**")


@bot.on_message(filters.command("addadmin") & filters.private)
async def add_admin_handler(client: Client, message: Message):
    if not is_owner(message.from_user.id):
        return await message.reply_text("⛔ Only the configured owner can manage extra administrators.")
    args = getattr(message, "command", []) or []
    if len(args) != 2 or not args[1].isdigit() or int(args[1]) <= 0:
        return await message.reply_text("Usage: `/addadmin <numeric_user_id>`")
    await bot.state_store.add_admin(int(args[1]))
    await message.reply_text("✅ Administrator added.")


@bot.on_message(filters.command("deladmin") & filters.private)
async def remove_admin_handler(client: Client, message: Message):
    if not is_owner(message.from_user.id):
        return await message.reply_text("⛔ Only the configured owner can manage extra administrators.")
    args = getattr(message, "command", []) or []
    if len(args) != 2 or not args[1].isdigit() or int(args[1]) <= 0:
        return await message.reply_text("Usage: `/deladmin <numeric_user_id>`")
    admin_id = int(args[1])
    if admin_id == bot.settings.owner_id or admin_id in bot.settings.admins:
        return await message.reply_text("⚠️ The owner and environment-configured admins cannot be removed here.")
    await bot.state_store.remove_admin(admin_id)
    await message.reply_text("✅ Administrator removed if they were present.")


@bot.on_message(filters.command("broadcast") & filters.private)
async def broadcast_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message:
        return await message.reply_text("⚠️ Reply to a message with `/broadcast` to send it to all users.")

    users = bot.state_store.get("users", [])
    status_msg = await message.reply_text(f"🚀 Broadcasting to `{len(users)}` users...")
    success = 0
    failed = 0

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

    await status_msg.edit_text(
        f"✅ **Broadcast Completed**\n\n"
        f"• Successful: `{success}`\n"
        f"• Failed: `{failed}`"
    )


# ------------------- PRIVATE MESSAGE INTERCEPTOR ------------------- #

async def _batch_endpoint(client: Client, message: Message):
    """Resolve a forwarded channel post or Telegram post URL to (chat ID, msg ID)."""
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
        return list(result)
    if result is None:
        return []
    return [result]

@bot.on_message(filters.private & ~filters.command(["start", "admin", "getlink", "batch", "broadcast", "users", "status"]))
async def incoming_private_messages(client: Client, message: Message):
    user_id = message.from_user.id

    action = ADMIN_PENDING_ACTIONS.get(user_id)
    if action:
        if message.text and message.text.split(maxsplit=1)[0].split("@")[0] == "/cancel":
            ADMIN_PENDING_ACTIONS.pop(user_id, None)
            return await message.reply_text("❌ Admin edit cancelled.")
        if not is_admin(user_id):
            ADMIN_PENDING_ACTIONS.pop(user_id, None)
            return await message.reply_text("⛔ Unauthorized.")
        value = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
        if not value:
            return await message.reply_text("⚠️ Send text for this setting, or /cancel.")

        if action == "welcome":
            await bot.state_store.set("welcome_message", value)
            ADMIN_PENDING_ACTIONS.pop(user_id, None)
            return await message.reply_text("✅ Welcome message updated.")
        if action == "fsub_message":
            await bot.state_store.set("fsub_message", value)
            ADMIN_PENDING_ACTIONS.pop(user_id, None)
            return await message.reply_text("✅ Force-subscription message updated.")
        if action == "fsub_add":
            parts = [part.strip() for part in value.split("|", 2)]
            if len(parts) != 3 or not parts[0] or not parts[1].startswith("https://t.me/") or not parts[2]:
                return await message.reply_text(
                    "Format: `@channelusername | https://t.me/join-link | Button label`"
                )
            channels = list(bot.state_store.get_fsub_channels())
            if len(channels) >= 10:
                ADMIN_PENDING_ACTIONS.pop(user_id, None)
                return await message.reply_text("⚠️ The maximum of 10 FSUB channels is already configured.")
            identifier = parts[0]
            if identifier.lstrip("-").isdigit():
                entry = {"id": int(identifier), "link": parts[1], "name": parts[2]}
            else:
                entry = {"username": identifier.lstrip("@"), "link": parts[1], "name": parts[2]}
            if any((item.get("id") == entry.get("id") and entry.get("id")) or
                   (str(item.get("username", "")).lstrip("@").lower() == str(entry.get("username", "")).lower()
                    and entry.get("username")) for item in channels):
                return await message.reply_text("⚠️ That channel is already configured.")
            channels.append(entry)
            await bot.state_store.set_fsub_channels(channels)
            ADMIN_PENDING_ACTIONS.pop(user_id, None)
            return await message.reply_text("✅ FSUB channel added. Make the bot an administrator there for membership checks.")
        if action == "fsub_remove":
            if not value.isdigit():
                return await message.reply_text("Send the 1-based channel number to remove, or /cancel.")
            channels = list(bot.state_store.get_fsub_channels())
            index = int(value) - 1
            if index < 0 or index >= len(channels):
                return await message.reply_text("⚠️ No FSUB channel has that number.")
            channels.pop(index)
            await bot.state_store.set_fsub_channels(channels)
            ADMIN_PENDING_ACTIONS.pop(user_id, None)
            return await message.reply_text("✅ FSUB channel removed.")

    if message.text and message.text.split(maxsplit=1)[0].split("@")[0] == "/cancel":
        if USER_BATCH_SESSIONS.pop(user_id, None) is not None:
            return await message.reply_text("❌ Batch process cancelled.")
        if USER_UPLOAD_SESSIONS.pop(user_id, None) is not None:
            return await message.reply_text("❌ Upload session cancelled.")

    # Handle /batch conversational input
    if user_id in USER_BATCH_SESSIONS:
        step = USER_BATCH_SESSIONS[user_id].get("step")
        if message.text and message.text == "/cancel":
            del USER_BATCH_SESSIONS[user_id]
            return await message.reply_text("❌ Batch process cancelled.")

        if step == "await_first":
            endpoint = await _batch_endpoint(client, message)
            if not endpoint:
                return await message.reply_text("⚠️ Please forward a message from a channel or supply a valid message link.")
            f_chat_id, f_msg_id = endpoint
            USER_BATCH_SESSIONS[user_id]["chat_id"] = f_chat_id
            USER_BATCH_SESSIONS[user_id]["first_id"] = f_msg_id
            USER_BATCH_SESSIONS[user_id]["step"] = "await_last"
            return await message.reply_text(
                f"✅ First message ID: `{f_msg_id}`\n\n"
                "Now forward the **LAST** message from the same channel."
            )

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
            if end_id - start_id + 1 > MAX_BATCH_MESSAGES:
                return await message.reply_text(
                    f"⚠️ Batch ranges are limited to {MAX_BATCH_MESSAGES} message IDs. Start a new /batch session."
                )

            status_msg = await message.reply_text(f"⏳ Processing `{end_id - start_id + 1}` messages into Archive...")

            archived_ids = []
            stored_count = 0

            all_ids = list(range(start_id, end_id + 1))
            for i in range(0, len(all_ids), MAX_BATCH_MESSAGES):
                chunk = all_ids[i:i + MAX_BATCH_MESSAGES]
                try:
                    source_reader = bot.batch_reader or client
                    msgs = await source_reader.get_messages(chat_id, chunk)
                except Exception as e:
                    logger.error(f"Cannot get messages from {chat_id}: {e}")
                    if "BOT_METHOD_INVALID" in str(e).upper() and not bot.batch_reader:
                        return await status_msg.edit_text(
                            "❌ Telegram rejected source-history access for the bot account. "
                            "Set the optional `BATCH_USER_SESSION_STRING` to an authorized "
                            "MTProto user session that can access this channel, then restart. "
                            "No arbitrary chat-history scan is performed."
                        )
                    return await status_msg.edit_text(
                        f"❌ Failed to fetch messages from source: `{e}`.\n"
                        "Make sure the source channel is public or the bot is an administrator there."
                    )

                for src_msg in _normalize_messages(msgs):
                    if not src_msg or src_msg.empty:
                        continue
                    try:
                        copied = await bot.storage.store_message(src_msg)
                        if not copied:
                            continue
                        archived_ids.append(copied.id)
                        stored_count += 1
                        await asyncio.sleep(0.2)
                    except FloodWait as f:
                        await asyncio.sleep(f.value)
                        copied = await bot.storage.store_message(src_msg)
                        if not copied:
                            continue
                        archived_ids.append(copied.id)
                        stored_count += 1
                    except Exception as e:
                        logger.error(f"Failed to archive msg {src_msg.id}: {e}")

            if not stored_count or not archived_ids:
                return await status_msg.edit_text("❌ No messages were successfully copied.")

            try:
                manifest_id = await save_batch_manifest(bot, bot.archive_channel_id, archived_ids)
            except Exception as exc:
                logger.exception("Could not save batch manifest")
                return await status_msg.edit_text(
                    f"❌ Files were archived, but the batch link could not be saved: `{exc}`"
                )
            batch_hash = encode_batch_manifest_id(manifest_id)
            batch_url = f"https://t.me/{bot.username}?start=batch_{batch_hash}"

            await bot.state_store.update_stats(total_links=1, total_files=stored_count)
            return await status_msg.edit_text(
                f"🎉 **Batch Link Generated Successfully!**\n\n"
                f"• Total Files: `{stored_count}`\n"
                f"• Link: {batch_url}"
            )

    # Handle /getlink upload session, including a true paused state.
    if user_id in USER_UPLOAD_SESSIONS:
        session = USER_UPLOAD_SESSIONS[user_id]
        if session.get("status") == "paused":
            return await message.reply_text("⏸ This upload session is paused. Tap RESUME or CANCEL before sending more files.")
        if session.get("status") != "recording":
            return
        if len(session.get("message_ids", [])) >= MAX_BATCH_MESSAGES:
            return await message.reply_text(
                f"⚠️ Upload sessions are limited to {MAX_BATCH_MESSAGES} messages. Generate this link or cancel the session."
            )
        try:
            copied = await bot.storage.store_message(message)
        except Exception as exc:
            logger.exception("Could not archive upload-session message")
            return await message.reply_text(f"❌ File could not be stored: `{exc}`")
        if not copied:
            return await message.reply_text("❌ File could not be verified in the archive. Please retry.")
        session["message_ids"].append(copied.id)
        count = len(USER_UPLOAD_SESSIONS[user_id]["message_ids"])

        buttons = [
            [InlineKeyboardButton("✅ GENERATE LINK", callback_data="gen_link_finish")],
            [InlineKeyboardButton("⏸ PAUSE", callback_data="gen_link_pause"),
             InlineKeyboardButton("❌ CANCEL", callback_data="gen_link_cancel")]
        ]
        return await message.reply_text(
            f"✅ File received!\nTotal queued: **{count}**",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # Single-file direct upload
    if message.media or message.document:
        copied = await bot.storage.store_message(message)
        if not copied:
            return await message.reply_text("❌ File could not be verified in the archive. Please retry.")
        link_hash = encode_message_id(copied.id)
        file_url = f"https://t.me/{bot.username}?start={link_hash}"
        await bot.state_store.update_stats(total_links=1, total_files=1)
        return await message.reply_text(
            f"✅ **File Stored Permanently!**\n\n"
            f"🔗 Access Link:\n{file_url}"
        )


# ------------------- CALLBACK QUERY HANDLERS ------------------- #

@bot.on_callback_query()
async def callback_router(client: Client, query: CallbackQuery):
    data = query.data
    user_id = query.from_user.id

    if data.startswith("adm_") and not is_admin(user_id):
        return await query.answer("⛔ Unauthorized.", show_alert=True)

    if data.startswith("try_again"):
        param = data.split("try_again_", 1)[1] if "_" in data else None
        unjoined = await check_fsub(client, user_id)
        if unjoined:
            return await query.answer("⚠️ You still haven't joined all required channels!", show_alert=True)
        await query.answer("✅ Verification passed!")
        await query.message.delete()
        if param:
            return await _deliver_start_payload(client, user_id, query.message, param)
        return await _reply_welcome(query.message, query.from_user.first_name)

    if data == "gen_link_finish":
        session = USER_UPLOAD_SESSIONS.get(user_id)
        if not session or not session.get("message_ids"):
            return await query.answer("No files uploaded yet!", show_alert=True)

        mids = session["message_ids"]
        try:
            if len(mids) == 1:
                code = encode_message_id(mids[0])
                url = f"https://t.me/{bot.username}?start={code}"
            else:
                manifest_id = await save_batch_manifest(bot, bot.archive_channel_id, mids)
                code = encode_batch_manifest_id(manifest_id)
                url = f"https://t.me/{bot.username}?start=batch_{code}"
        except Exception:
            logger.exception("Could not create upload-session link")
            return await query.answer("Could not save the link manifest. Your upload session is still available; try again.", show_alert=True)

        del USER_UPLOAD_SESSIONS[user_id]
        await bot.state_store.update_stats(total_links=1, total_files=len(mids))
        await query.message.edit_text(
            f"🎉 **Batch Generated!**\n\n"
            f"• Count: `{len(mids)}` messages\n"
            f"• Shareable Link: {url}"
        )

    elif data == "gen_link_pause":
        session = USER_UPLOAD_SESSIONS.get(user_id)
        if session:
            session["status"] = "paused"
            await query.answer("Upload session paused.")
            buttons = [
                [InlineKeyboardButton("▶️ RESUME", callback_data="gen_link_resume")],
                [InlineKeyboardButton("✅ GENERATE LINK", callback_data="gen_link_finish"),
                 InlineKeyboardButton("❌ CANCEL", callback_data="gen_link_cancel")]
            ]
            await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "gen_link_resume":
        session = USER_UPLOAD_SESSIONS.get(user_id)
        if session:
            session["status"] = "recording"
            await query.answer("Upload session resumed.")
            buttons = [
                [InlineKeyboardButton("✅ GENERATE LINK", callback_data="gen_link_finish")],
                [InlineKeyboardButton("⏸ PAUSE", callback_data="gen_link_pause"),
                 InlineKeyboardButton("❌ CANCEL", callback_data="gen_link_cancel")]
            ]
            await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "gen_link_cancel":
        if user_id in USER_UPLOAD_SESSIONS:
            del USER_UPLOAD_SESSIONS[user_id]
        await query.message.edit_text("❌ Upload session cancelled.")

    elif data == "adm_status":
        await query.answer()
        stats = bot.state_store.get_stats()
        text = (
            f"📊 **Current Statistics**\n\n"
            f"• Users: `{stats.get('total_users', 0)}`\n"
            f"• Links: `{stats.get('total_links', 0)}`\n"
            f"• Files: `{stats.get('total_files', 0)}`\n"
            f"• Views: `{stats.get('total_views', 0)}`"
        )
        try:
            await query.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]])
            )
        except MessageNotModified:
            pass

    elif data == "adm_edit_welcome":
        ADMIN_PENDING_ACTIONS[user_id] = "welcome"
        await query.answer()
        await query.message.edit_text(
            "📝 Send the new welcome message text. Use /cancel to keep the current message.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]])
        )

    elif data == "adm_broadcast":
        await query.answer()
        await query.message.edit_text(
            "📬 Reply to the message you want to broadcast, then send `/broadcast` in that chat.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]])
        )

    elif data == "adm_back":
        ADMIN_PENDING_ACTIONS.pop(user_id, None)
        await query.answer()
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
             InlineKeyboardButton("📬 Broadcast", callback_data="adm_broadcast")]
        ])
        await query.message.edit_text(text, reply_markup=kb)

    elif data == "adm_fsub":
        ADMIN_PENDING_ACTIONS.pop(user_id, None)
        await query.answer()
        channels = bot.state_store.get_fsub_channels()
        ch_list = "\n".join(
            [f"{index}. {channel.get('name', 'Channel')}: `{channel.get('id') or channel.get('username')}`"
             for index, channel in enumerate(channels, start=1)]
        ) if channels else "None"
        await query.message.edit_text(
            f"📢 **Force Subscribe Channels (Max 10)**:\n\n{ch_list}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Channel", callback_data="adm_fsub_add"),
                 InlineKeyboardButton("➖ Remove Channel", callback_data="adm_fsub_remove")],
                [InlineKeyboardButton("📝 Edit FSUB Message", callback_data="adm_fsub_message")],
                [InlineKeyboardButton("🔙 Back", callback_data="adm_back")],
            ])
        )

    elif data == "adm_fsub_add":
        if len(bot.state_store.get_fsub_channels()) >= 10:
            return await query.answer("Maximum 10 FSUB channels are allowed.", show_alert=True)
        ADMIN_PENDING_ACTIONS[user_id] = "fsub_add"
        await query.answer()
        await query.message.edit_text(
            "Send one channel as `@channelusername | https://t.me/join-link | Button label`.\n"
            "The bot must be an administrator in the channel. Use /cancel to abort.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_fsub")]])
        )

    elif data == "adm_fsub_remove":
        if not bot.state_store.get_fsub_channels():
            return await query.answer("No FSUB channels are configured.", show_alert=True)
        ADMIN_PENDING_ACTIONS[user_id] = "fsub_remove"
        await query.answer()
        await query.message.edit_text(
            "Send the 1-based number of the channel to remove, or /cancel to abort.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_fsub")]])
        )

    elif data == "adm_fsub_message":
        ADMIN_PENDING_ACTIONS[user_id] = "fsub_message"
        await query.answer()
        await query.message.edit_text(
            "📝 Send the new force-subscription prompt text, or /cancel to keep the current message.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_fsub")]])
        )


def register_handlers(app: Client):
    """Compatibility export for main.py."""
    pass


if __name__ == "__main__":
    bot.run()
