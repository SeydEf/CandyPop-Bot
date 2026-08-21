"""
My Subscriptions handler.

Lists user's active subscriptions by querying X-UI panel in real-time
using the user's Telegram ID, and provides management actions
(rename, regenerate link, delete, QR, links).
"""

from __future__ import annotations

import logging
import uuid

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import SUB_BASE_URL
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
    to_persian_digits,
)
from utils.helpers import generate_qr

logger = logging.getLogger(__name__)
router = Router(name="subscriptions")


class SubStates(StatesGroup):
    waiting_rename = State()


# ──────────────────────────── Helpers ────────────────────────────


async def _fetch_subs_from_xui(tg_id: int) -> list[dict]:
    """Fetch subscriptions from X-UI panel by Telegram user ID.

    Returns a list of dicts with keys the rest of the handler expects:
        email, service_name (=email/remark), sub_id, totalGB, expiryTime,
        usedTraffic, enable
    """
    raw = await xui_api.get_clients_by_tg_id(tg_id)
    subs: list[dict] = []
    for entry in raw:
        client = entry.get("client", {})
        subs.append(
            {
                "email": client.get("email", ""),
                "service_name": client.get("email", ""),  # remark = email
                "sub_id": client.get("subId", ""),
                "totalGB": client.get("totalGB", 0),
                "expiryTime": client.get("expiryTime", 0),
                "usedTraffic": entry.get("usedTraffic", 0),
                "enable": client.get("enable", True),
                "inboundIds": entry.get("inboundIds", []),
            }
        )
    return subs


def _build_sub_link(sub_id: str) -> str:
    """Build subscription URL from subId."""
    if sub_id:
        return f"{SUB_BASE_URL}/{sub_id}"
    return "نامشخص"


# ──────────────────────────── List Subscriptions ────────────────────────────


@router.message(F.text == BTN_MY_SUBS)
async def my_subscriptions(message: types.Message) -> None:
    """Show list of user's subscriptions from X-UI panel."""
    if not message.from_user:
        return

    subs = await _fetch_subs_from_xui(message.from_user.id)
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
    subs = await _fetch_subs_from_xui(callback.from_user.id)
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
    """Show subscription dashboard with live stats from X-UI."""
    email = callback.data[len("sub_view_") :]  # type: ignore[union-attr]

    # Fetch live data from X-UI
    client = await xui_api.get_client(email)
    client_full = await xui_api.get_client_full(email)

    if not client:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    total_bytes = client.get("totalGB", 0)
    expiry_ms = client.get("expiryTime", 0)
    used_traffic = client_full.get("usedTraffic", 0) if client_full else 0
    remaining = max(0, total_bytes - used_traffic) if total_bytes > 0 else 0
    sub_id = client.get("subId", "")
    sub_link = _build_sub_link(sub_id)

    # Format traffic
    if total_bytes > 0:
        usage_text = f"{format_size(used_traffic)} / {format_size(total_bytes)}"
        remaining_text = format_size(remaining)
    else:
        usage_text = f"{format_size(used_traffic)} / نامحدود"
        remaining_text = "نامحدود"

    days_text = format_remaining_days(expiry_ms)

    text = (
        f"📦 <b>داشبورد اشتراک</b>\n\n"
        f"📛 نام سرویس: {client.get('email', email)}\n"
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
    email = callback.data[len("sub_rename_") :]  # type: ignore[union-attr]
    await state.set_state(SubStates.waiting_rename)
    await state.update_data(rename_email=email)

    await callback.message.edit_text(  # type: ignore[union-attr]
        "✏️ <b>نام جدید سرویس را وارد کنید:</b>\n\nبرای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SubStates.waiting_rename, F.text)
async def rename_process(message: types.Message, state: FSMContext) -> None:
    """Process the new service name — updates the email/remark in X-UI."""
    if not message.text:
        return

    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    data = await state.get_data()
    old_email = data.get("rename_email")
    if not old_email:
        await state.clear()
        return

    new_name = message.text.strip()[:50]

    # Fetch current client data from X-UI
    client = await xui_api.get_client(old_email)
    if not client:
        await state.clear()
        await message.answer("❌ کلاینت در پنل یافت نشد.")
        return

    # Update email/remark in X-UI (email is the remark/name field)
    update_data = dict(client)
    update_data["email"] = new_name

    try:
        await xui_api.update_client(old_email, update_data)
        await state.clear()
        await message.answer(
            f"✅ نام سرویس به <b>{new_name}</b> تغییر کرد.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Failed to rename client %s", old_email)
        await state.clear()
        await message.answer(f"❌ خطا در تغییر نام: {e}")


# ──────────────────────────── Regenerate Link ────────────────────────────


@router.callback_query(F.data.startswith("sub_regen_"))
async def regen_confirm(callback: types.CallbackQuery) -> None:
    """Ask for confirmation before regenerating subscription link."""
    email = callback.data[len("sub_regen_") :]  # type: ignore[union-attr]
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
    email = callback.data[len("sub_confirm_regen_") :]  # type: ignore[union-attr]

    # Get current client data
    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ کلاینت در پنل یافت نشد.", show_alert=True)
        return

    # Generate new UUID and subId
    new_uuid = str(uuid.uuid4())
    new_sub_id = str(uuid.uuid4())

    # Update client with new credentials
    update_data = dict(client)
    update_data["uuid"] = new_uuid
    update_data["subId"] = new_sub_id

    try:
        await xui_api.update_client(email, update_data)

        new_link = _build_sub_link(new_sub_id)

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
    email = callback.data[len("sub_delete_") :]  # type: ignore[union-attr]
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
    email = callback.data[len("sub_confirm_del_") :]  # type: ignore[union-attr]

    try:
        await xui_api.delete_client(email)
        await callback.message.edit_text(  # type: ignore[union-attr]
            "✅ <b>سرویس با موفقیت حذف شد.</b>",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Failed to delete client %s from X-UI", email)
        await callback.message.edit_text(  # type: ignore[union-attr]
            "❌ خطا در حذف سرویس.",
            parse_mode="HTML",
        )
    await callback.answer()


# ──────────────────────────── QR Code ────────────────────────────


@router.callback_query(F.data.startswith("sub_qr_"))
async def show_qr(callback: types.CallbackQuery, bot: Bot) -> None:
    """Generate and send QR code for subscription link."""
    email = callback.data[len("sub_qr_") :]  # type: ignore[union-attr]
    client = await xui_api.get_client(email)

    sub_id = client.get("subId", "") if client else ""
    if not sub_id:
        await callback.answer("❌ لینک اشتراک یافت نشد.", show_alert=True)
        return

    sub_link = _build_sub_link(sub_id)
    qr_image = generate_qr(sub_link)

    await bot.send_photo(
        chat_id=callback.message.chat.id,  # type: ignore[union-attr]
        photo=types.BufferedInputFile(qr_image.read(), filename="qrcode.png"),
        caption=f"📱 QR Code اشتراک\n\n🔗 <code>{sub_link}</code>",
        parse_mode="HTML",
    )
    await callback.answer()


def _extract_link_name(link: str, index: int) -> str:
    """Extract link remark name from URL fragment (#name), or fallback to index."""
    if "#" in link:
        from urllib.parse import unquote

        name = unquote(link.split("#", 1)[1]).strip()
        if name:
            return name
    return f"کانفیگ {to_persian_digits(index)}"


@router.callback_query(F.data.startswith("sub_links_"))
async def show_links(callback: types.CallbackQuery) -> None:
    """Show individual config links for the subscription."""
    email = callback.data[len("sub_links_") :]  # type: ignore[union-attr]

    links = await xui_api.get_client_links(email)

    if not links:
        await callback.answer("❌ لینکی یافت نشد.", show_alert=True)
        return

    text = "🔗 <b>لینک‌های کانفیگ:</b>\n\n"
    for i, link in enumerate(links, 1):
        name = _extract_link_name(link, i)
        text += f"📌 <b>{name}:</b>\n<code>{link}</code>\n\n"

    # Send as a new message since links can be very long
    await callback.message.answer(  # type: ignore[union-attr]
        text,
        parse_mode="HTML",
    )
    await callback.answer()
