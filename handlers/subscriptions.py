"""
My Subscriptions handler.

Lists user's active subscriptions, shows live stats from X-UI,
and provides management actions (rename, regenerate link, delete, QR, links).
"""

from __future__ import annotations

import logging
import uuid

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import SUB_BASE_URL
from db.models import (
    delete_subscription,
    get_subscription_by_email,
    get_user_subscriptions,
    update_subscription_name,
)
from keyboards.inline_kb import (
    confirm_delete_keyboard,
    confirm_regen_keyboard,
    subscription_manage_keyboard,
    subscriptions_list_keyboard,
)
from keyboards.reply_kb import BTN_MY_SUBS
from services import xui_api
from utils.formatting import (
    format_remaining_days,
    format_size,
    format_traffic_usage,
    to_persian_digits,
)
from utils.helpers import generate_qr

logger = logging.getLogger(__name__)
router = Router(name="subscriptions")


class SubStates(StatesGroup):
    waiting_rename = State()


# ──────────────────────────── List Subscriptions ────────────────────────────


@router.message(F.text == BTN_MY_SUBS)
async def my_subscriptions(message: types.Message) -> None:
    """Show list of user's subscriptions."""
    if not message.from_user:
        return

    subs = await get_user_subscriptions(message.from_user.id)
    if not subs:
        await message.answer(
            "📭 <b>شما هیچ اشتراک فعالی ندارید.</b>\n\n"
            "از منوی اصلی گزینه «🛒 خرید اشتراک» را انتخاب کنید.",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"📋 <b>اشتراک‌های شما ({to_persian_digits(len(subs))}):</b>\n\n"
        "یکی را انتخاب کنید:",
        reply_markup=subscriptions_list_keyboard(subs),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "sub_back_list")
async def back_to_list(callback: types.CallbackQuery) -> None:
    """Go back to subscription list."""
    if not callback.from_user:
        return
    subs = await get_user_subscriptions(callback.from_user.id)
    if not subs:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "📭 <b>شما هیچ اشتراک فعالی ندارید.</b>",
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"📋 <b>اشتراک‌های شما ({to_persian_digits(len(subs))}):</b>\n\n"
            "یکی را انتخاب کنید:",
            reply_markup=subscriptions_list_keyboard(subs),
            parse_mode="HTML",
        )
    await callback.answer()


# ──────────────────────────── View Subscription Dashboard ────────────────────────────


@router.callback_query(F.data.startswith("sub_view_"))
async def view_subscription(callback: types.CallbackQuery) -> None:
    """Show subscription dashboard with live stats."""
    email = callback.data[len("sub_view_"):]  # type: ignore[union-attr]
    sub = await get_subscription_by_email(email)

    if not sub:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    # Fetch live data from X-UI
    traffic = await xui_api.get_client_traffic(email)
    client = await xui_api.get_client(email)

    if traffic:
        up = traffic.get("up", 0)
        down = traffic.get("down", 0)
        total = traffic.get("total", 0)
        expiry_ms = traffic.get("expiryTime", 0)
        used = up + down
        remaining = max(0, total - used)

        usage_text = format_traffic_usage(up, down, total)
        remaining_text = format_size(remaining)
        days_text = format_remaining_days(expiry_ms)
    else:
        usage_text = "نامشخص"
        remaining_text = "نامشخص"
        days_text = "نامشخص"

    sub_id = sub.get("sub_id", "") or (client.get("subId", "") if client else "")
    sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

    text = (
        f"📦 <b>داشبورد اشتراک</b>\n\n"
        f"📛 نام سرویس: {sub['service_name']}\n"
        f"📊 مصرف ترافیک: {usage_text}\n"
        f"📉 ترافیک باقیمانده: {remaining_text}\n"
        f"⏱ روزهای باقیمانده: {days_text}\n\n"
        f"🔗 لینک اشتراک:\n<code>{sub_link}</code>"
    )

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=subscription_manage_keyboard(email),
        parse_mode="HTML",
    )
    await callback.answer()


# ──────────────────────────── Rename ────────────────────────────


@router.callback_query(F.data.startswith("sub_rename_"))
async def rename_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Start rename flow — ask for new name."""
    email = callback.data[len("sub_rename_"):]  # type: ignore[union-attr]
    await state.set_state(SubStates.waiting_rename)
    await state.update_data(rename_email=email)

    await callback.message.edit_text(  # type: ignore[union-attr]
        "✏️ <b>نام جدید سرویس را وارد کنید:</b>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SubStates.waiting_rename, F.text)
async def rename_process(message: types.Message, state: FSMContext) -> None:
    """Process the new service name."""
    if not message.text:
        return

    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    data = await state.get_data()
    email = data.get("rename_email")
    if not email:
        await state.clear()
        return

    new_name = message.text.strip()[:50]  # Limit name length
    await update_subscription_name(email, new_name)
    await state.clear()

    await message.answer(
        f"✅ نام سرویس به <b>{new_name}</b> تغییر کرد.",
        parse_mode="HTML",
    )


# ──────────────────────────── Regenerate Link ────────────────────────────


@router.callback_query(F.data.startswith("sub_regen_"))
async def regen_confirm(callback: types.CallbackQuery) -> None:
    """Ask for confirmation before regenerating subscription link."""
    email = callback.data[len("sub_regen_"):]  # type: ignore[union-attr]
    await callback.message.edit_text(  # type: ignore[union-attr]
        "⚠️ <b>آیا مطمئن هستید؟</b>\n\n"
        "با تغییر لینک اشتراک، لینک‌ قبلی و UUID های قبلی غیرفعال شده "
        "و دسترسی افراد غیرمجاز قطع می‌شود.\n\n"
        "تمام کانفیگ‌های متصل به این اشتراک باید با لینک جدید جایگزین شوند.",
        reply_markup=confirm_regen_keyboard(email),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sub_confirm_regen_"))
async def regen_execute(callback: types.CallbackQuery) -> None:
    """Execute the link regeneration — generate new UUID for the client."""
    email = callback.data[len("sub_confirm_regen_"):]  # type: ignore[union-attr]

    # Get current client data
    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ کلاینت در پنل یافت نشد.", show_alert=True)
        return

    # Generate new UUID and subId
    new_uuid = str(uuid.uuid4())
    new_sub_id = uuid.uuid4().hex[:16]

    # Update client with new credentials
    update_data = dict(client)
    update_data["id"] = new_uuid
    update_data["subId"] = new_sub_id

    try:
        await xui_api.update_client(email, update_data)

        # Update local DB
        from db.database import get_db
        db = await get_db()
        await db.execute(
            "UPDATE subscriptions SET sub_id = ? WHERE email = ?",
            (new_sub_id, email),
        )
        await db.commit()

        new_link = f"{SUB_BASE_URL}/{new_sub_id}"

        await callback.message.edit_text(  # type: ignore[union-attr]
            f"✅ <b>لینک اشتراک با موفقیت تغییر کرد!</b>\n\n"
            f"🔗 لینک جدید:\n<code>{new_link}</code>\n\n"
            "⚠️ لینک قبلی دیگر کار نمی‌کند.",
            reply_markup=subscription_manage_keyboard(email),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Failed to regenerate link for %s", email)
        await callback.answer(f"❌ خطا: {e}", show_alert=True)

    await callback.answer()


# ──────────────────────────── Delete ────────────────────────────


@router.callback_query(F.data.startswith("sub_delete_"))
async def delete_confirm(callback: types.CallbackQuery) -> None:
    """Ask for confirmation before deleting subscription."""
    email = callback.data[len("sub_delete_"):]  # type: ignore[union-attr]
    await callback.message.edit_text(  # type: ignore[union-attr]
        "⚠️ <b>آیا مطمئن هستید که می‌خواهید این سرویس را حذف کنید؟</b>\n\n"
        "این عملیات قابل بازگشت نیست!",
        reply_markup=confirm_delete_keyboard(email),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sub_confirm_del_"))
async def delete_execute(callback: types.CallbackQuery) -> None:
    """Execute the subscription deletion."""
    email = callback.data[len("sub_confirm_del_"):]  # type: ignore[union-attr]

    try:
        # Delete from X-UI
        await xui_api.delete_client(email)
    except Exception:
        logger.exception("Failed to delete client %s from X-UI", email)

    # Delete from local DB
    await delete_subscription(email)

    await callback.message.edit_text(  # type: ignore[union-attr]
        "✅ <b>سرویس با موفقیت حذف شد.</b>",
        parse_mode="HTML",
    )
    await callback.answer()


# ──────────────────────────── QR Code ────────────────────────────


@router.callback_query(F.data.startswith("sub_qr_"))
async def show_qr(callback: types.CallbackQuery, bot: Bot) -> None:
    """Generate and send QR code for subscription link."""
    email = callback.data[len("sub_qr_"):]  # type: ignore[union-attr]
    sub = await get_subscription_by_email(email)
    client = await xui_api.get_client(email)

    sub_id = ""
    if sub:
        sub_id = sub.get("sub_id", "")
    if not sub_id and client:
        sub_id = client.get("subId", "")

    if not sub_id:
        await callback.answer("❌ لینک اشتراک یافت نشد.", show_alert=True)
        return

    sub_link = f"{SUB_BASE_URL}/{sub_id}"
    qr_image = generate_qr(sub_link)

    await bot.send_photo(
        chat_id=callback.message.chat.id,  # type: ignore[union-attr]
        photo=types.BufferedInputFile(qr_image.read(), filename="qrcode.png"),
        caption=f"📱 QR Code اشتراک\n\n🔗 <code>{sub_link}</code>",
        parse_mode="HTML",
    )
    await callback.answer()


# ──────────────────────────── Individual Config Links ────────────────────────────


@router.callback_query(F.data.startswith("sub_links_"))
async def show_links(callback: types.CallbackQuery) -> None:
    """Show individual config links for the subscription."""
    email = callback.data[len("sub_links_"):]  # type: ignore[union-attr]

    links = await xui_api.get_client_links(email)

    if not links:
        await callback.answer("❌ لینکی یافت نشد.", show_alert=True)
        return

    text = "🔗 <b>لینک‌های کانفیگ:</b>\n\n"
    for i, link in enumerate(links, 1):
        text += f"<b>{to_persian_digits(i)}.</b>\n<code>{link}</code>\n\n"

    # Send as a new message since links can be very long
    await callback.message.answer(  # type: ignore[union-attr]
        text,
        parse_mode="HTML",
    )
    await callback.answer()
