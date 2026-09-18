from __future__ import annotations

import logging
from urllib.parse import quote

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import BOT_NAME
from db.models import get_referral_config, get_referral_stats
from keyboards.reply_kb import BTN_INVITE
from utils.formatting import to_persian_digits

logger = logging.getLogger(__name__)
router = Router(name="referral")


@router.message(Command("invite"))
@router.message(F.text == BTN_INVITE)
async def referral_menu(message: types.Message, bot: Bot) -> None:
    if not message.from_user:
        return

    tg_id = message.from_user.id
    me = await bot.get_me()
    bot_username = me.username or "candypop_bot"

    ref_link = f"https://t.me/{bot_username}?start=ref_{tg_id}"

    stats = await get_referral_stats(tg_id)
    config = await get_referral_config()

    invited_count = stats.get("invited_count", 0)
    percent = config.get("percent", 10)
    is_enabled = config.get("enabled", True)

    status_note = (
        ""
        if is_enabled
        else "\n⚠️ <i>سیستم پاداش دعوت در حال حاضر موقتاً غیرفعال است.</i>\n"
    )

    share_url = f"https://t.me/share/url?url={quote(ref_link)}&text={quote(f'🍭 اتصال پرسرعت و بدون محدودیت با {BOT_NAME} 🚀')}"

    text = (
        f"💰 <b>دعوت از دوستان و دریافت پاداش</b>\n\n"
        f"با اشتراک‌گذاری لینک اختصاصی خود، با هر خرید دوستانتان "
        f"<b>{to_persian_digits(percent)}٪</b> از مبلغ فاکتور مستقیماً به کیف پول شما افزوده می‌شود.\n\n"
        f"📊 <b>آمار دعوت‌های شما:</b>\n"
        f"🎯 تعداد کاربران دعوت‌شده: <b>{to_persian_digits(invited_count)} نفر</b>\n"
        f"🎁 درصد پاداش معرفی: <b>{to_persian_digits(percent)}٪</b>\n"
        f"{status_note}\n"
        f"🔗 <b>لینک اختصاصی شما:</b>\n<code>{ref_link}</code>\n\n"
        f"💡 <i>کافی است این لینک را برای دوستان خود بفرستید؛ پاداش هر خرید به‌صورت خودکار در کیف پول شما ثبت می‌شود.</i>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 ارسال لینک برای دوستان",
                    url=share_url,
                )
            ]
        ]
    )

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
