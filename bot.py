"""
CandyPop VPN Bot — Entry Point

Initializes the bot, registers handlers & middleware, and starts polling.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.client.session.aiohttp import AiohttpSession

from config import BOT_TOKEN, PROXY_URL
from db.database import close_db, init_db
from handlers import (
    admin,
    admin_pricing,
    buy,
    pricing,
    profile,
    start,
    subscriptions,
    test_sub,
    wallet,
)
from middlewares.channel_check import ChannelCheckMiddleware
from services.xui_api import close_client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def on_startup(bot: Bot) -> None:
    """Called when the bot starts up."""
    await init_db()
    me = await bot.get_me()
    logger.info("Bot started: @%s (%s)", me.username, me.full_name)


async def on_shutdown(bot: Bot) -> None:
    """Called when the bot shuts down."""
    await close_db()
    await close_client()
    logger.info("Bot shut down gracefully.")


async def main() -> None:
    """Main entry point."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set! Check your .env file.")
        return

    session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else None

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )
    dp = Dispatcher()

    # Register startup/shutdown hooks
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Register channel-check middleware on all updates
    dp.message.outer_middleware(ChannelCheckMiddleware())
    dp.callback_query.outer_middleware(ChannelCheckMiddleware())

    # Register routers (order matters — first match wins)
    dp.include_routers(
        start.router,
        profile.router,
        pricing.router,
        buy.router,
        subscriptions.router,
        wallet.router,
        test_sub.router,
        admin.router,
        admin_pricing.router,
    )

    logger.info("Starting CandyPop Bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
