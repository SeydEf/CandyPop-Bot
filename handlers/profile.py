from __future__ import annotations

import logging
import math
from typing import Any

from aiogram import F, Router, types
from aiogram.filters import Command
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
    name = user_info.get("full_name") if user_info else None
    if not name and user_info:
        name = user_info.get("username", "کاربر")
    if not name:
        name = "کاربر گرامی"

    balance = await get_balance(tg_id)
    subs = await xui_api.get_clients_by_tg_id(tg_id)
    active_subs_count = len(subs)

    summary = await get_user_purchase_summary(tg_id)
    approved_count = summary["approved_count"]
    total_spent = summary["total_spent"]

    text = (
        f"✨ <b>حساب کاربری شما</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>نام کاربر:</b> {name}\n"
        f"🆔 <b>شناسه عددی (User ID):</b> <code>{tg_id}</code>\n"
        f"👛 <b>موجودی کیف پول:</b> <b>{format_price(balance)}</b>\n"
        f"⚡️ <b>تعداد سرویس‌های فعال:</b> {to_persian_digits(active_subs_count)} سرویس\n"
        f"🛍 <b>سوابق خرید:</b> {to_persian_digits(approved_count)} تراکنش موفق ({format_price(total_spent)})\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>با استفاده از منوی زیر می‌توانید موجودی خود را افزایش داده یا سوابق سفارشات خود را بررسی کنید.</i>"
    )
    return text


@router.message(Command("profile"))
@router.message(F.text == BTN_PROFILE)
async def profile_dashboard(message: types.Message) -> None:
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
    if not callback.from_user:
        return
    tg_id = callback.from_user.id
    user_info = await get_user(tg_id)
    text = await _build_profile_text(tg_id, user_info)
    await callback.message.edit_text(
        text,
        reply_markup=profile_dashboard_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "profile_topup")
async def profile_topup_callback(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    from db.models import get_card_config

    card_cfg = await get_card_config()
    if not card_cfg.get("enabled", True):
        await callback.answer(
            "⚠️ شارژ کیف پول از طریق کارت به کارت در حال حاضر غیرفعال است.",
            show_alert=True,
        )
        return

    from handlers.wallet import WalletStates

    await state.set_state(WalletStates.waiting_deposit_amount)
    text = (
        "💳 <b>شارژ و افزایش موجودی کیف پول</b>\n\n"
        "با شارژ کیف پول، می‌توانید در هر زمان سرویس‌های خود را <b>به‌صورت آنی و بدون معطلی</b> خریداری یا تمدید کنید!\n\n"
        "🔹 یکی از مبالغ آماده زیر را انتخاب کنید یا مبلغ دلخواه خود (به تومان) را بنویسید و ارسال کنید:\n"
        "💡 <i>مثال: <code>100000</code></i>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=deposit_amount_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


def _format_status_badge(status: str) -> str:
    if status == "approved":
        return "✅ موفق و تایید شده"
    elif status == "pending":
        return "⏳ در انتظار بررسی و تایید"
    elif status == "expired":
        return "⚠️ منقضی شده"
    elif status == "rejected":
        return "❌ رد شده"
    return status


def _format_invoice_details(inv: dict[str, Any]) -> str:
    inv_id = inv["id"]
    status_str = _format_status_badge(inv.get("status", ""))
    amount = inv.get("amount", 0)
    dur = inv.get("duration_days", 0)
    gb = inv.get("data_gb", 0)
    users = inv.get("users_count", 1)
    target_email = inv.get("target_email")
    payment_method = inv.get("payment_method", "card")
    created_at = inv.get("created_at", "")

    pm_str = "کیف پول 👛" if payment_method == "wallet" else "کارت به کارت 💳"
    date_str = format_datetime(created_at)

    if target_email == "TOPUP" or (dur == 0 and gb == 0):
        item_type = "💰 <b>شارژ مستقیم کیف پول</b>"
    elif target_email:
        item_type = (
            f"🔄 <b>تمدید اشتراک</b>\n"
            f"   🏷 سرویس: <code>{target_email}</code>\n"
            f"   ⏱ مدت: {dur} روز | 👥 کاربر: {to_persian_digits(users)} | 📊 حجم: {format_size_gb(gb)}"
        )
    else:
        item_type = (
            f"🚀 <b>خرید اشتراک جدید</b>\n"
            f"   ⏱ مدت اعتبار: {dur} روز\n"
            f"   📊 حجم اختصاصی: {format_size_gb(gb)}\n"
            f"   👥 ظرفیت همزمان: {to_persian_digits(users)} کاربر"
        )

    return (
        f"🧾 <b>فاکتور:</b> <code>{inv_id}</code> | {status_str}\n"
        f"   {item_type}\n"
        f"   💎 مبلغ: <b>{format_price(amount)}</b>\n"
        f"   💳 پرداخت: <b>{pm_str}</b>\n"
        f"   📅 زمان ثبت: <code>{date_str}</code>\n"
    )


@router.callback_query(F.data.startswith("profile_orders_"))
async def profile_orders_callback(callback: types.CallbackQuery) -> None:
    if not callback.from_user:
        return
    tg_id = callback.from_user.id
    page = int(callback.data[len("profile_orders_") :])

    offset = page * PAGE_SIZE
    invoices, total_count = await get_user_invoices_paginated(
        tg_id, offset=offset, limit=PAGE_SIZE
    )

    if total_count == 0:
        text = (
            "🧾 <b>تاریخچه سفارشات و فاکتورها</b>\n\n"
            "📭 <i>شما تا این لحظه هیچ سفارش یا تراکنشی در سیستم ثبت نکرده‌اید.</i>"
        )
        await callback.message.edit_text(
            text,
            reply_markup=orders_pagination_keyboard(0, 0),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    total_pages = math.ceil(total_count / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    text = (
        f"🧾 <b>تاریخچه سفارشات شما</b> (مجموع: {to_persian_digits(total_count)} فاکتور):\n"
        f"📄 <i>صفحه {to_persian_digits(page + 1)} از {to_persian_digits(total_pages)}</i>\n\n"
    )
    for inv in invoices:
        text += _format_invoice_details(inv) + "\n"

    await callback.message.edit_text(
        text,
        reply_markup=orders_pagination_keyboard(page, total_pages),
        parse_mode="HTML",
    )
    await callback.answer()
