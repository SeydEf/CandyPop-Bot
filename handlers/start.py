from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart

from config import BOT_NAME, SUPPORT_LINK
from db.models import create_referral, create_user, get_user
from keyboards.reply_kb import BTN_GUIDE, BTN_SUPPORT, main_menu_keyboard

logger = logging.getLogger(__name__)
router = Router(name="start")


def get_welcome_text(user_name: str | None = None) -> str:
    name_str = f" <b>{user_name}</b>" if user_name else ""
    return (
        f"سلام {name_str} عزیز، به ربات {BOT_NAME} خوش اومدی! 🍭✨\n\n"
        f"از طریق این ربات می‌تونی به‌صورت ۲۴ ساعته:\n"
        f"🛒 سرویس جدید بخری\n"
        f"📊 وضعیت و حجم سرویس‌های فعلی‌ت رو بررسی کنی\n"
        f"👤 اطلاعات حساب کاربری و کیف پولت رو مدیریت کنی\n\n"
        f"برای شروع، یکی از گزینه‌های زیر رو انتخاب کن 👇\n"
    )


WELCOME_TEXT = get_welcome_text()


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
    if existing is None or (
        referrer_id is not None and not existing.get("referrer_id")
    ):
        await create_user(tg_id, username, full_name, referrer_id)
        if referrer_id is not None and referrer_id != tg_id:
            referrer = await get_user(referrer_id)
            if referrer is not None:
                await create_referral(referrer_id, tg_id)
                logger.info("Referral: %d referred by %d", tg_id, referrer_id)

    await send_welcome(message)


async def send_welcome(
    message: types.Message | Any,
    data: dict | None = None,
) -> None:
    user_name: str | None = None
    if isinstance(message, types.Message) and message.from_user:
        user_name = message.from_user.full_name or message.from_user.first_name
    elif isinstance(message, types.CallbackQuery) and message.from_user:
        user_name = message.from_user.full_name or message.from_user.first_name

    text = get_welcome_text(user_name)

    if isinstance(message, types.Message) and message.chat:
        await message.answer(
            text,
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
                    text=text,
                    reply_markup=main_menu_keyboard(),
                    parse_mode="HTML",
                )


@router.message(Command("help"))
@router.message(F.text == BTN_GUIDE)
async def cmd_guide(message: types.Message) -> None:
    text = (
        f"📖 <b>راهنمای اتصال به سرویس‌های {BOT_NAME}</b>\n\n"
        "برای استفاده از اشتراک خود در برنامه‌های مختلف، لینک ساب‌اسکریپشن دریافت شده را کپی کرده و طبق راهنمای زیر در برنامه وارد کنید:\n\n"
        "📱 <b>اندروید (Android):</b>\n"
        "برنامه‌های پیشنهادی: <b>v2rayNG</b> | <b>NekoBox</b> | <b>Streisand</b>\n"
        "• برنامه را باز کنید ⬅️ منو / علامت + ⬅️ گزینه <i>Import config from clipboard</i> یا افزودن ساب‌اسکریپشن.\n\n"
        "🍏 <b>آیفون (iOS):</b>\n"
        "برنامه‌های پیشنهادی: <b>v2box</b> | <b>Streisand</b> | <b>Shadowrocket</b>\n"
        "• برنامه را باز کرده ⬅️ بخش Subscriptions ⬅️ دکمه + ⬅️ لینک ساب‌اسکریپشن را وارد و ذخیره کنید.\n\n"
        "💻 <b>ویندوز (Windows):</b>\n"
        "برنامه‌های پیشنهادی: <b>v2rayN</b> | <b>NekoRay</b>\n"
        "• برنامه را باز کرده ⬅️ گزینه Subscription Group ⬅️ افزودن لینک ⬅️ دکمه Update Subscription.\n\n"
        "💡 <i>در صورت نیاز به راهنمایی بیشتر، از بخش «🆘 پشتیبانی» با ما در ارتباط باشید.</i>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("support"))
@router.message(F.text == BTN_SUPPORT)
async def cmd_support(message: types.Message) -> None:
    text = (
        "🆘 <b>پشتیبانی و ارتباط با ما</b>\n\n"
        f"تیم پشتیبانی {BOT_NAME} آماده پاسخگویی به سوالات، مشاوره و حل مشکلات شماست.\n\n"
        "جهت ارتباط مستقیم با پشتیبانی می‌توانید از آیدی زیر استفاده کنید:\n"
        f"👨‍💻 <b>آیدی پشتیبانی:</b> {SUPPORT_LINK}\n\n"
        "⏱ <b>ساعات پاسخگویی:</b> همه روزه به صورت ۲۴ ساعته"
    )
    await message.answer(text, parse_mode="HTML")
