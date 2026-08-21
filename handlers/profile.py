"""
Profile handler ("👤 پروفایل").

Displays account information:
  - User Full Name
  - Telegram Numeric ID (code format)
  - Current Wallet Balance
  - Active Subscriptions Count (from X-UI)
  - Purchase History Summary (Total Successful Purchases & Total Spent Amount)

Inline Actions:
  - "💳 افزایش موجودی" (Top Up Wallet): Opens deposit options / workflow
  - "🧾 تاریخچه سفارشات" (Order History): Displays paginated list of transaction logs (5 per page)
"""

from __future__ import annotations

import logging
import math
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from db.models import (
    get_balance,
    get_user,
    get_user_invoices_paginated,
    get_user_purchase_summary,
)
from keyboards.inline_kb import (
    deposit_amount_keyboard,
    orders_pagination_keyboard,
    profile_dashboard_keyboard,
)
from keyboards.reply_kb import BTN_PROFILE
from services import xui_api
from utils.formatting import (
    format_datetime,
    format_price,
    format_size_gb,
    to_persian_digits,
)

logger = logging.getLogger(__name__)
router = Router(name="profile")

PAGE_SIZE = 5


async def _build_profile_text(tg_id: int, user_info: dict[str, Any] | None) -> str:
    """Build the main profile dashboard text."""
    name = user_info.get("full_name") if user_info else None
    if not name and user_info:
        name = user_info.get("username", "کاربر")
    if not name:
        name = "کاربر"

    balance = await get_balance(tg_id)
    subs = await xui_api.get_clients_by_tg_id(tg_id)
    active_subs_count = len(subs)

    summary = await get_user_purchase_summary(tg_id)
    approved_count = summary["approved_count"]
    total_spent = summary["total_spent"]

    text = (
        f"👤 <b>پروفایل کاربری</b>\n\n"
        f"📛 <b>نام:</b> {name}\n"
        f"🆔 <b>شناسه عددی تلگرام:</b> <code>{tg_id}</code>\n"
        f"👛 <b>موجودی کیف پول:</b> {format_price(balance)}\n"
        f"📦 <b>اشتراک‌های فعال:</b> {to_persian_digits(active_subs_count)} اشتراک\n"
        f"🛒 <b>خلاصه‌ی خریدها:</b> {to_persian_digits(approved_count)} خرید موفق ({format_price(total_spent)})"
    )
    return text


@router.message(F.text == BTN_PROFILE)
async def profile_dashboard(message: types.Message) -> None:
    """Show profile dashboard."""
    if not message.from_user:
        return
    tg_id = message.from_user.id
    user_info = await get_user(tg_id)
    text = await _build_profile_text(tg_id, user_info)
    await message.answer(
        text,
        reply_markup=profile_dashboard_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "profile_main")
async def profile_main_callback(callback: types.CallbackQuery) -> None:
    """Return to main profile dashboard via inline button."""
    if not callback.from_user:
        return
    tg_id = callback.from_user.id
    user_info = await get_user(tg_id)
    text = await _build_profile_text(tg_id, user_info)
    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=profile_dashboard_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "profile_topup")
async def profile_topup_callback(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Trigger Top Up Wallet flow from profile."""
    from handlers.wallet import WalletStates

    await state.set_state(WalletStates.waiting_deposit_amount)
    text = (
        "💳 <b>افزایش موجودی کیف پول</b>\n\n"
        "لطفاً یکی از مبالغ پیشنهادی زیر را انتخاب کنید یا مبلغ دلخواه (به تومان) را ارسال نمایید:\n"
        "مثال: <code>100000</code>\n\n"
    )
    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=deposit_amount_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


def _format_status_badge(status: str) -> str:
    """Return status emoji and Persian tag."""
    if status == "approved":
        return "✅ موفق"
    elif status == "pending":
        return "⏳ در انتظار پرداخت"
    elif status == "expired":
        return "⚠️ منقضی شده"
    elif status == "rejected":
        return "❌ رد شده"
    return status


def _format_invoice_details(inv: dict[str, Any]) -> str:
    """Format single invoice details line for order history list."""
    inv_id = inv["id"]
    status_str = _format_status_badge(inv.get("status", ""))
    amount = inv.get("amount", 0)
    dur = inv.get("duration_days", 0)
    gb = inv.get("data_gb", 0)
    users = inv.get("users_count", 1)
    target_email = inv.get("target_email")
    payment_method = inv.get("payment_method", "card")
    created_at = inv.get("created_at", "")

    pm_str = "موجودی کیف پول 👛" if payment_method == "wallet" else "کارت به کارت 💳"
    date_str = format_datetime(created_at)

    if target_email == "TOPUP" or (dur == 0 and gb == 0):
        item_type = "💳 شارژ کیف پول"
    elif target_email:
        item_type = f"🔄 تمدید سرویس ({target_email})"
    else:
        item_type = (
            f"📦 خرید اشتراک:\n"
            f"   ⏱️ دوره: {dur} روز\n"
            f"   📊 حجم: {format_size_gb(gb)}\n"
            f"   👤 تعداد کاربران: {to_persian_digits(users)} کاربر"
        )

    return (
        f"▫️ <b>فاکتور <code>{inv_id}</code></b> — {status_str}\n"
        f"   {item_type}\n"
        f"   💰 مبلغ: {format_price(amount)}\n"
        f"   💳 روش پرداخت: <b>{pm_str}</b>\n"
        f"   📅 تاریخ و زمان: <code>{date_str}</code>\n"
    )


@router.callback_query(F.data.startswith("profile_orders_"))
async def profile_orders_callback(callback: types.CallbackQuery) -> None:
    """Show paginated transaction logs / order history."""
    if not callback.from_user:
        return
    tg_id = callback.from_user.id
    page = int(callback.data[len("profile_orders_") :])  # type: ignore[union-attr]

    offset = page * PAGE_SIZE
    invoices, total_count = await get_user_invoices_paginated(
        tg_id, offset=offset, limit=PAGE_SIZE
    )

    if total_count == 0:
        text = "🧾 <b>تاریخچه سفارشات</b>\n\n📭 شما هنوز هیچ تراکنشی ثبت نکرده‌اید."
        await callback.message.edit_text(  # type: ignore[union-attr]
            text,
            reply_markup=orders_pagination_keyboard(0, 0),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    total_pages = math.ceil(total_count / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    text = f"🧾 <b>تاریخچه سفارشات (کل: {to_persian_digits(total_count)}):</b>\n\n"
    for inv in invoices:
        text += _format_invoice_details(inv) + "\n"

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=orders_pagination_keyboard(page, total_pages),
        parse_mode="HTML",
    )
    await callback.answer()
