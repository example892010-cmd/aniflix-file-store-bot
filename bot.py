import os
import sys
import asyncio
import logging
from typing import Dict, List, Optional, Union
from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from pyrogram.errors import (
    FloodWait,
    UserNotParticipant,
    RPCError,
    MessageNotModified
)

from config import Config
from helper.state_store import StateStore
from helper.link_codec import encode_message_id, decode_message_id, encode_batch, decode_batch
from helper.storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ANIFLIX-FILE-STORE-BOT")

# In-memory tracking for multi-file /getlink upload sessions
USER_UPLOAD_SESSIONS: Dict[int, Dict] = {}
# In-memory tracking for /batch forwarding sessions
USER_BATCH_SESSIONS: Dict[int, Dict] = {}


class AniflixBot(Client):
    def __init__(self):
        super().__init__(
            name="AniflixFileStoreBot",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            bot_token=Config.BOT_TOKEN,
            workers=8
        )
        self.state_store = StateStore(self, Config.ARCHIVE_CHANNEL_ID)
        self.storage = Storage(self, Config.ARCHIVE_CHANNEL_ID)

    async def start(self):
        await super().start()
        me = await self.get_me()
        self.username = me.username
        logger.info(f"Bot started as @{self.username}")
        # Initialize remote state safely without get_chat_history
        await self.state_store.load_state()
        logger.info("Bot state initialized successfully.")

    async def stop(self, *args):
        await self.state_store.save_state()
        await super().stop(*args)
        logger.info("Bot stopped cleanly.")


bot = AniflixBot()


# ------------------- HELPER FUNCTIONS ------------------- #

def is_admin(user_id: int) -> bool:
    if user_id in Config.ADMINS:
        return True
    admins = bot.state_store.get_admins()
    return user_id in admins


async def check_fsub(client: Client, user_id: int) -> List[Dict]:
    """Returns a list of FSUB channels that the user hasn't joined yet."""
    fsub_channels = bot.state_store.get_fsub_channels()
    if not fsub_channels and Config.FSUB_CHANNELS:
        fsub_channels = Config.FSUB_CHANNELS

    unjoined = []
    for ch in fsub_channels:
        chat_identifier = ch.get("id") or ch.get("username")
        try:
            await client.get_chat_member(chat_identifier, user_id)
        except UserNotParticipant:
            unjoined.append(ch)
        except Exception as e:
            logger.debug(f"FSUB check skip/error for {chat_identifier}: {e}")
            continue
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

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    await bot.state_store.record_user(user_id)

    # Check payload (single link or batch link)
    args = message.command
    if len(args) > 1:
        param = args[1]
        
        # Check Force Subscription before delivering files
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

        # Process link payload
        if param.startswith("batch_"):
            # Batch link decoding
            try:
                start_id, end_id = decode_batch(param[6:])
                status_msg = await message.reply_text("⏳ Fetching requested files...")
                sent_count = 0
                for mid in range(start_id, end_id + 1):
                    try:
                        await client.copy_message(
                            chat_id=user_id,
                            from_chat_id=Config.ARCHIVE_CHANNEL_ID,
                            message_id=mid
                        )
                        sent_count += 1
                        await asyncio.sleep(0.3)
                    except FloodWait as f:
                        await asyncio.sleep(f.value)
                        await client.copy_message(
                            chat_id=user_id,
                            from_chat_id=Config.ARCHIVE_CHANNEL_ID,
                            message_id=mid
                        )
                        sent_count += 1
                    except Exception as e:
                        logger.error(f"Error copying message {mid}: {e}")
                
                await status_msg.delete()
                await bot.state_store.update_stats(total_views=1)
                return
            except Exception as e:
                logger.error(f"Batch decoding error: {e}")
                return await message.reply_text("❌ This batch link is corrupted, invalid, or expired.")
        else:
            # Single file link decoding
            try:
                mid = decode_message_id(param)
                await client.copy_message(
                    chat_id=user_id,
                    from_chat_id=Config.ARCHIVE_CHANNEL_ID,
                    message_id=mid
                )
                await bot.state_store.update_stats(total_views=1)
                return
            except Exception as e:
                logger.error(f"Single file decoding error: {e}")
                return await message.reply_text("❌ This file link is corrupted, invalid, or expired.")

    # Default Start message
    welcome = bot.state_store.get("welcome_message") or (
        f"👋 Hello, **{message.from_user.first_name}**!\n\n"
        "Welcome to **ANIFLIX FILE STORE BOT**.\n"
        "Send me media or use `/getlink` and `/batch` to store and generate sharing links."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Channel", url="https://t.me/Aniflix_Bots"),
         InlineKeyboardButton("Support", url="https://t.me/Aniflix_Support")]
    ])
    await message.reply_text(welcome, reply_markup=kb)


# ------------------- COMMAND: /GETLINK (Interactive Upload) ------------------- #

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
            await message.reply_to_message.copy(chat_id=uid)
            success += 1
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ **Broadcast Completed**\n\n"
        f"• Successful: `{success}`\n"
        f"• Failed: `{failed}`"
    )


# ------------------- PRIVATE MESSAGE INTERCEPTOR ------------------- #

@bot.on_message(filters.private & ~filters.command(["start", "admin", "getlink", "batch", "broadcast", "users", "status"]))
async def incoming_private_messages(client: Client, message: Message):
    user_id = message.from_user.id

    # Handle /batch conversational input
    if user_id in USER_BATCH_SESSIONS:
        step = USER_BATCH_SESSIONS[user_id].get("step")
        if message.text and message.text == "/cancel":
            del USER_BATCH_SESSIONS[user_id]
            return await message.reply_text("❌ Batch process cancelled.")

        if step == "await_first":
            f_chat_id = None
            f_msg_id = None

            if message.forward_from_chat:
                f_chat_id = message.forward_from_chat.id
                f_msg_id = message.forward_from_message_id
            elif message.text and ("t.me/" in message.text):
                parts = message.text.strip().split("/")
                try:
                    f_msg_id = int(parts[-1])
                    f_chat_id = parts[-2]
                except Exception:
                    pass

            if not f_msg_id or not f_chat_id:
                return await message.reply_text("⚠️ Please forward a message from a channel or supply a valid message link.")

            USER_BATCH_SESSIONS[user_id]["chat_id"] = f_chat_id
            USER_BATCH_SESSIONS[user_id]["first_id"] = f_msg_id
            USER_BATCH_SESSIONS[user_id]["step"] = "await_last"
            return await message.reply_text(
                f"✅ First message ID: `{f_msg_id}`\n\n"
                "Now forward the **LAST** message from the same channel."
            )

        elif step == "await_last":
            l_msg_id = None
            if message.forward_from_message_id:
                l_msg_id = message.forward_from_message_id
            elif message.text and ("t.me/" in message.text):
                try:
                    l_msg_id = int(message.text.strip().split("/")[-1])
                except Exception:
                    pass

            if not l_msg_id:
                return await message.reply_text("⚠️ Please forward the last message.")

            first_id = USER_BATCH_SESSIONS[user_id]["first_id"]
            chat_id = USER_BATCH_SESSIONS[user_id]["chat_id"]
            del USER_BATCH_SESSIONS[user_id]

            start_id = min(first_id, l_msg_id)
            end_id = max(first_id, l_msg_id)

            status_msg = await message.reply_text(f"⏳ Processing `{end_id - start_id + 1}` messages into Archive...")

            # Store range into Archive channel using get_messages (Bot API supported)
            archived_first_id = None
            archived_last_id = None
            stored_count = 0

            # Retrieve messages sequentially/in chunks
            all_ids = list(range(start_id, end_id + 1))
            for i in range(0, len(all_ids), 200):
                chunk = all_ids[i:i + 200]
                try:
                    msgs = await client.get_messages(chat_id, chunk)
                except Exception as e:
                    logger.error(f"Cannot get messages from {chat_id}: {e}")
                    return await status_msg.edit_text(
                        f"❌ Failed to fetch messages from source: `{e}`.\n"
                        "Make sure the source channel is public or the bot is an administrator there."
                    )

                for src_msg in msgs:
                    if not src_msg or src_msg.empty:
                        continue
                    try:
                        copied = await src_msg.copy(chat_id=Config.ARCHIVE_CHANNEL_ID)
                        if archived_first_id is None:
                            archived_first_id = copied.id
                        archived_last_id = copied.id
                        stored_count += 1
                        await asyncio.sleep(0.2)
                    except FloodWait as f:
                        await asyncio.sleep(f.value)
                        copied = await src_msg.copy(chat_id=Config.ARCHIVE_CHANNEL_ID)
                        if archived_first_id is None:
                            archived_first_id = copied.id
                        archived_last_id = copied.id
                        stored_count += 1
                    except Exception as e:
                        logger.error(f"Failed to archive msg {src_msg.id}: {e}")

            if not stored_count or not archived_first_id:
                return await status_msg.edit_text("❌ No messages were successfully copied.")

            batch_hash = encode_batch(archived_first_id, archived_last_id)
            batch_url = f"https://t.me/{bot.username}?start=batch_{batch_hash}"

            await bot.state_store.update_stats(total_links=1, total_files=stored_count)
            return await status_msg.edit_text(
                f"🎉 **Batch Link Generated Successfully!**\n\n"
                f"• Total Files: `{stored_count}`\n"
                f"• Link: {batch_url}"
            )

    # Handle /getlink active recording session
    if user_id in USER_UPLOAD_SESSIONS and USER_UPLOAD_SESSIONS[user_id].get("status") == "recording":
        copied = await message.copy(chat_id=Config.ARCHIVE_CHANNEL_ID)
        USER_UPLOAD_SESSIONS[user_id]["message_ids"].append(copied.id)
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

    # Single-file direct upload when not in a session
    if message.media or message.document:
        copied = await message.copy(chat_id=Config.ARCHIVE_CHANNEL_ID)
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

    if data.startswith("try_again"):
        param = data.split("try_again_", 1)[1] if "_" in data else None
        unjoined = await check_fsub(client, user_id)
        if unjoined:
            return await query.answer("⚠️ You still haven't joined all required channels!", show_alert=True)
        await query.answer("✅ Verification passed!")
        await query.message.delete()
        if param:
            # Replay the user command with payload
            query.message.from_user = query.from_user
            query.message.command = ["start", param]
            return await start_handler(client, query.message)
        else:
            return await start_handler(client, query.message)

    if data == "gen_link_finish":
        session = USER_UPLOAD_SESSIONS.get(user_id)
        if not session or not session.get("message_ids"):
            return await query.answer("No files uploaded yet!", show_alert=True)

        mids = session["message_ids"]
        del USER_UPLOAD_SESSIONS[user_id]

        if len(mids) == 1:
            code = encode_message_id(mids[0])
            url = f"https://t.me/{bot.username}?start={code}"
        else:
            code = encode_batch(mids[0], mids[-1])
            url = f"https://t.me/{bot.username}?start=batch_{code}"

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

    elif data == "adm_back":
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
        channels = bot.state_store.get_fsub_channels()
        ch_list = "\n".join([f"• {c.get('name', 'Channel')}: `{c.get('id') or c.get('username')}`" for c in channels]) if channels else "None"
        await query.message.edit_text(
            f"📢 **Force Subscribe Channels (Max 10)**:\n\n{ch_list}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]])
        )


# ------------------- ENTRY POINT ------------------- #

if __name__ == "__main__":
    bot.run()
