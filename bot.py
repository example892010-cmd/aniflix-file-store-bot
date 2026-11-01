"""Minimal Pyrofork bot: admin uploads, channel archive, start-link retrieval."""
from __future__ import annotations

import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from helper.link_codec import decode_text, encode_storage_payload, parse_storage_payload
from helper.storage import archive_and_verify, deliver_archived_message


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

    async def verify_archive_access(self):
        me = await self.get_me()
        self.username = me.username or ""
        if not self.username:
            raise RuntimeError("The bot account has no username. Set one in BotFather.")
        chat = await self.get_chat(self.archive_channel_id)
        member = await self.get_chat_member(self.archive_channel_id, me.id)
        raw_status = getattr(member, "status", "")
        status = str(getattr(raw_status, "value", raw_status)).lower()
        if not ("administrator" in status or "owner" in status or "creator" in status):
            raise RuntimeError("Make the bot an administrator in the archive channel before starting.")
        privileges = getattr(member, "privileges", None)
        if "administrator" in status and privileges is not None and not getattr(privileges, "can_post_messages", False):
            raise RuntimeError("The bot administrator must have permission to post messages in the archive channel.")
        self.LOGGER.info("Archive channel access verified: %s (%s)", chat.title, self.archive_channel_id)


def register_handlers(bot: AniflixBot):
    @bot.on_message(filters.private & filters.command("start"))
    async def start_handler(client: AniflixBot, message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            await message.reply_text(
                f"Welcome to {client.settings.bot_display_name}!\n\n"
                "This bot stores admin-uploaded files in a Telegram archive channel. "
                "Open a stored-file link to retrieve a file.\n\n"
                "Uploads are restricted to the bot administrators. Use /about for details."
            )
            return

        try:
            decoded = decode_text(parts[1].strip())
            channel_id, file_message_id = parse_storage_payload(
                decoded, client.archive_channel_id
            )
        except ValueError:
            await message.reply_text("❌ This file link is invalid or does not belong to this archive.")
            return

        status = await message.reply_text("⏳ Checking the archive and preparing your file...")
        try:
            await deliver_archived_message(
                client,
                channel_id,
                file_message_id,
                message.chat.id,
                client.settings.protect_content,
            )
            await status.delete()
        except FloodWait as exc:
            wait_seconds = int(getattr(exc, "value", getattr(exc, "x", 1)))
            await status.edit_text(f"Telegram is busy. Please open this link again in about {wait_seconds} seconds.")
        except LookupError as exc:
            await status.edit_text(f"❌ {exc}")
        except Exception:
            client.LOGGER.exception("Archive retrieval failed for message %s", file_message_id)
            await status.edit_text("❌ I couldn't retrieve this file right now. Please try again later.")

    @bot.on_message(filters.private & filters.media)
    async def upload_handler(client: AniflixBot, message):
        if not message.from_user or message.from_user.id not in client.admins:
            await message.reply_text("Uploads are restricted to the configured bot administrators.")
            return

        async with client.archive_lock:
            status = await message.reply_text("⏳ Saving your file to the Telegram archive...")
            try:
                stored_id = await archive_and_verify(
                    client, message, client.archive_channel_id
                )
                payload = encode_storage_payload(client.archive_channel_id, stored_id)
                link = f"https://t.me/{client.username}?start={payload}"
                await status.edit_text(
                    "✅ File saved and verified. The archive copy has not been deleted.\n\n"
                    f"{link}",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔗 OPEN STORED FILE", url=link)]]
                    ),
                    disable_web_page_preview=True,
                )
                client.LOGGER.info("Archived message %s as archive message %s", message.id, stored_id)
            except FloodWait as exc:
                wait_seconds = int(getattr(exc, "value", getattr(exc, "x", 1)))
                await status.edit_text(
                    "⚠️ Telegram rate-limited the archive operation. "
                    f"Wait about {wait_seconds} seconds, then check the archive channel before retrying. "
                    "No retrieval link was issued."
                )
            except Exception:
                client.LOGGER.exception("Archive save failed for source message %s", message.id)
                await status.edit_text(
                    "❌ The save could not be confirmed, so no link was issued. "
                    "Check the archive channel before retrying to avoid duplicate copies."
                )

    @bot.on_message(filters.private & filters.command("about"))
    async def about_handler(client: AniflixBot, message):
        settings = client.settings
        lines = [f"{settings.bot_display_name}", "Telegram-only file archive and retrieval."]
        if settings.owner_credit:
            lines.append(f"Owner: {settings.owner_credit}")
        if settings.developer_credit:
            lines.append(f"Developer: {settings.developer_credit}")
        for label, url in (
            ("Updates Channel", settings.updates_url),
            ("Ongoing Anime", settings.ongoing_url),
            ("Support", settings.support_url),
        ):
            if url:
                lines.append(f"{label}: {url}")
        await message.reply_text("\n".join(lines), disable_web_page_preview=True)

    @bot.on_message(filters.private & filters.command("help"))
    async def help_handler(client: AniflixBot, message):
        await message.reply_text(
            "How to use ANIFLIX FILE STORE BOT\n\n"
            "• Admins: send one media file to archive it and receive a link.\n"
            "• Members: open a stored-file link to receive the archived copy.\n"
            "• Original archive messages are not deleted by this bot.\n\n"
            "This bot has no external database or local media storage."
        )

    @bot.on_message(filters.private & filters.command("status"))
    async def status_handler(client: AniflixBot, message):
        if not message.from_user or message.from_user.id not in client.admins:
            return
        await message.reply_text(
            "✅ Bot is connected.\n"
            f"Archive channel: {client.archive_channel_id}\n"
            f"Configured admins: {len(client.admins)}"
        )
