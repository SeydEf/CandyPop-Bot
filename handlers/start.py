from __future__ import annotations

import logging
from typing import Any

from aiogram import Router, types
from aiogram.filters import CommandStart

from db.models import create_user, get_user, create_referral
from keyboards.reply_kb import main_menu_keyboard

logger = logging.getLogger(__name__)
router = Router(name="start")

WELCOME_TEXT = (
    "🍭 <b>به ربات CandyPop خوش آمدید!</b>\n\n"
    "با استفاده از این ربات می‌توانید اشتراک VPN خریداری کنید.\n\n"
    "از منوی زیر گزینه مورد نظر خود را انتخاب کنید 👇"
)


@router.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    if not message.from_user:
        return

    tg_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name

    referrer_id: int | None = None
    if message.text and " " in message.text:
        payload = message.text.split(maxsplit=1)[1]
        if payload.startswith("ref_"):
            try:
                from utils.formatting import persian_to_english_digits

                clean_payload = persian_to_english_digits(payload[4:])
                referrer_id = int(clean_payload)
                if referrer_id == tg_id:
                    referrer_id = None
            except ValueError:
                referrer_id = None

    existing = await get_user(tg_id)
    if existing is None:
        await create_user(tg_id, username, full_name, referrer_id)
        if referrer_id is not None:
            referrer = await get_user(referrer_id)
            if referrer is not None:
                await create_referral(referrer_id, tg_id)
                logger.info("Referral: %d referred by %d", tg_id, referrer_id)

    await send_welcome(message)


async def send_welcome(
    message: types.Message | Any,
    data: dict | None = None,
) -> None:
    if isinstance(message, types.Message) and message.chat:
        await message.answer(
            WELCOME_TEXT,
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
    elif isinstance(message, types.CallbackQuery):
        if message.message and message.message.chat:
            from aiogram import Bot

            bot: Bot | None = data.get("bot") if data else None
            if bot:
                await bot.send_message(
                    chat_id=message.message.chat.id,
                    text=WELCOME_TEXT,
                    reply_markup=main_menu_keyboard(),
                    parse_mode="HTML",
                )
