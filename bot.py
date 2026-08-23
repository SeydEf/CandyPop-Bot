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
    admin_broadcast,
    admin_control,
    admin_create_sub,
    admin_sub_manage,
    buy,
    pricing,
    profile,
    referral,
    start,
    subscriptions,
    test_sub,
    wallet,
)
from middlewares.channel_check import ChannelCheckMiddleware
from services.xui_api import close_client

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ErrorEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def global_error_handler(event: ErrorEvent) -> bool:
    if isinstance(
        event.exception, TelegramBadRequest
    ) and "message is not modified" in str(event.exception):
        logger.debug("Suppressed TelegramBadRequest: message is not modified.")
        if event.update and event.update.callback_query:
            try:
                await event.update.callback_query.answer()
            except Exception:
                pass
        return True
    return False


async def on_startup(bot: Bot) -> None:
    await init_db()
    me = await bot.get_me()
    logger.info("Bot started: @%s (%s)", me.username, me.full_name)


async def on_shutdown(bot: Bot) -> None:
    await close_db()
    await close_client()
    logger.info("Bot shut down gracefully.")


async def main() -> None:
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

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    dp.error.register(global_error_handler)

    dp.message.outer_middleware(ChannelCheckMiddleware())
    dp.callback_query.outer_middleware(ChannelCheckMiddleware())

    dp.include_routers(
        start.router,
        profile.router,
        pricing.router,
        buy.router,
        subscriptions.router,
        wallet.router,
        referral.router,
        test_sub.router,
        admin.router,
        admin_control.router,
        admin_sub_manage.router,
        admin_create_sub.router,
        admin_broadcast.router,
    )

    logger.info("Starting CandyPop Bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
