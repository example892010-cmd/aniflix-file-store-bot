"""Render Web Service entry point with a Telegram bot and health endpoint."""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from pyrogram import idle

from bot import AniflixBot, register_handlers
from config import load_settings

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
HEALTH = {"ready": False, "bot": None}


async def health_handler(_request):
    if not HEALTH["ready"]:
        return web.json_response({"status": "starting"}, status=503)
    return web.json_response({"status": "ok", "bot": "ANIFLIX FILE STORE BOT"})


async def root_handler(_request):
    return web.Response(
        text="ANIFLIX FILE STORE BOT is running. Health endpoint: /health",
        content_type="text/plain",
    )


async def run():
    settings = load_settings()
    app = web.Application()
    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.port)
    await site.start()

    bot = AniflixBot(settings)
    register_handlers(bot)
    HEALTH["bot"] = bot
    try:
        await bot.start()
        await bot.verify_archive_access()
        HEALTH["ready"] = True
        logging.getLogger("aniflix").info("Bot is ready and health check is live")
        await idle()
    finally:
        HEALTH["ready"] = False
        if getattr(bot, "is_connected", False):
            await bot.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(run())
