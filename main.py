import asyncio
import logging
from aiohttp import web
from pyrogram import idle
from bot import bot, register_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("MAIN")


async def health_check(request):
    """Render health check / keep-alive endpoint."""
    return web.Response(text="Bot is running!")


async def start_web_server():
    """Starts lightweight web server for Render Free port binding."""
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server started on port {port}")
    return runner


async def main():
    import os
    # Start web server if PORT is set (Render Web Service)
    web_runner = None
    if os.environ.get("PORT"):
        web_runner = await start_web_server()

    # Register any extra handlers if defined
    register_handlers(bot)

    # Start Pyrogram Bot
    await bot.start()
    logger.info("Aniflix File Store Bot is fully online.")

    # Keep running until terminated
    await idle()

    # Graceful shutdown
    await bot.stop()
    if web_runner:
        await web_runner.cleanup()


if __name__ == "__main__":
    import os
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution interrupted.")
    finally:
        loop.close()
