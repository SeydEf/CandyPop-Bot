from __future__ import annotations

import io
import uuid

import qrcode
from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from config import SUB_BASE_URL


def _build_sub_link(sub_id: str) -> str:
    if sub_id:
        return f"{SUB_BASE_URL}/{sub_id}"
    return "نامشخص"


def generate_qr(data: str) -> io.BytesIO:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_email(tg_id: int, username: str | None = None, test: bool = False) -> str:
    short = uuid.uuid4().hex[:6]
    name = username or "user"
    name = "".join(c if c.isalnum() or c == "_" else "" for c in name)
    if test:
        return f"{name}_{short}_test"
    else:
        return f"{name}_{short}"


def gb_to_bytes(gb: float) -> int:
    return int(round(gb * 1024 * 1024 * 1024))


async def safe_edit_text(
    message: types.Message,
    text: str,
    reply_markup: types.InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool | None = None,
) -> bool:
    try:
        if (
            message.photo
            or message.video
            or message.document
            or message.voice
            or message.audio
        ):
            await message.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        else:
            await message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return False
        raise e


async def handle_reply_keyboard_or_command(
    message: types.Message, state: FSMContext
) -> bool:
    if not message.text:
        return False
    text = message.text.strip()
    if text == "/cancel":
        await state.clear()
        from keyboards.reply_kb import main_menu_keyboard

        await message.answer("🚫 عملیات لغو شد.", reply_markup=main_menu_keyboard())
        return True
    if text.startswith("/start"):
        await state.clear()
        from handlers.start import cmd_start

        await cmd_start(message, state)
        return True

    from keyboards.reply_kb import (
        BTN_BUY,
        BTN_GUIDE,
        BTN_INCREASE_WALLET,
        BTN_INVITE,
        BTN_MY_SUBS,
        BTN_PRICING,
        BTN_PROFILE,
        BTN_SUPPORT,
        BTN_TEST,
    )

    if text == BTN_BUY:
        await state.clear()
        from handlers.buy import buy_start

        await buy_start(message, state)
        return True
    if text == BTN_MY_SUBS:
        await state.clear()
        from handlers.subscriptions import my_subscriptions

        await my_subscriptions(message)
        return True
    if text == BTN_PROFILE:
        await state.clear()
        from handlers.profile import profile_dashboard

        await profile_dashboard(message)
        return True
    if text == BTN_INCREASE_WALLET:
        await state.clear()
        from handlers.wallet import wallet_deposit_start

        await wallet_deposit_start(message, state)
        return True
    if text == BTN_PRICING:
        await state.clear()
        from handlers.pricing import show_pricing

        await show_pricing(message)
        return True
    if text == BTN_GUIDE:
        await state.clear()
        from handlers.guide import cmd_guide

        await cmd_guide(message)
        return True
    if text == BTN_SUPPORT:
        await state.clear()
        from handlers.start import cmd_support

        await cmd_support(message)
        return True
    if text == BTN_INVITE:
        await state.clear()
        from handlers.referral import referral_menu

        bot = message.bot
        if bot:
            await referral_menu(message, bot)
        return True
    if text == BTN_TEST:
        await state.clear()
        from handlers.test_sub import test_subscription

        await test_subscription(message)
        return True

    return False
