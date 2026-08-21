"""
Admin-side handlers.

Handles payment approval/rejection from the admin.
Only the ADMIN_CHAT_ID can trigger these callbacks.
"""

from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router, types
from aiogram.filters import Command

from config import ADMIN_CHAT_ID, INBOUND_IDS, SUB_BASE_URL
from db.models import (
    get_invoice,
    get_user,
    reset_all_test_subs,
    update_invoice_status,
)
from keyboards.inline_kb import sub_config_links_keyboard
from services import xui_api
from utils.formatting import format_size_gb, to_persian_digits
from utils.helpers import gb_to_bytes, generate_email

logger = logging.getLogger(__name__)
router = Router(name="admin")


def _is_admin(event: types.CallbackQuery | types.Message) -> bool:
    return event.from_user is not None and event.from_user.id == ADMIN_CHAT_ID


# ──────────────────────────── Reset All Test Subs (Admin Command) ────────────────────────────


@router.message(Command("reset_test", "reset_tests"))
async def admin_reset_tests(message: types.Message) -> None:
    """Admin command to reset test subscriptions for all users."""
    if not _is_admin(message):
        return

    count = await reset_all_test_subs()
    await message.answer(
        f"✅ <b>اشتراک‌های تست تمامی کاربران با موفقیت بازنشانی شد!</b>\n\n"
        f"👥 تعداد کاربران به‌روزرسانی شده: {to_persian_digits(count)}",
        parse_mode="HTML",
    )


# ──────────────────────────── Approve Payment ────────────────────────────


@router.callback_query(F.data.startswith("admin_approve_"))
async def admin_approve(callback: types.CallbackQuery, bot: Bot) -> None:
    """Admin approves a payment — create client and deliver config."""
    if not _is_admin(callback):
        await callback.answer("❌ شما دسترسی ندارید.", show_alert=True)
        return

    invoice_id = callback.data[len("admin_approve_") :]  # type: ignore[union-attr]
    invoice = await get_invoice(invoice_id)

    if not invoice:
        await callback.answer("❌ فاکتور یافت نشد.", show_alert=True)
        return

    if invoice["status"] not in ("paid", "pending"):
        await callback.answer("❌ این فاکتور قبلاً پردازش شده.", show_alert=True)
        return

    tg_id = invoice["tg_id"]
    duration = invoice["duration_days"]
    gb = invoice["data_gb"]
    invoice["amount"]

    # Update invoice status
    await update_invoice_status(invoice_id, "approved")

    # Update admin message
    await callback.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]

    try:
        # Get the user's username from DB for email generation
        user = await get_user(tg_id)
        username = user.get("username") if user else None

        # Create client in X-UI
        email = generate_email(tg_id, username)
        total_bytes = gb_to_bytes(gb)
        expiry_ms = int((time.time() + duration * 86400) * 1000)

        await xui_api.add_client(
            email=email,
            total_gb=total_bytes,
            expiry_time=expiry_ms,
            tg_id=tg_id,
            inbound_ids=INBOUND_IDS,
        )

        # Get subId
        client = await xui_api.get_client(email)
        sub_id = client.get("subId", "") if client else ""

        sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

        # Notify user
        user_text = (
            f"✅ <b>پرداخت شما تأیید شد و اشتراک فعال گردید!</b>\n\n"
            f"🆔 فاکتور: <code>{invoice_id}</code>\n"
            f"📦 نام سرویس: {email}\n"
            f"⏱ مدت: {duration} روز\n"
            f"📊 حجم: {format_size_gb(gb)}\n\n"
            f"🔗 لینک اشتراک:\n<code>{sub_link}</code>"
        )
        await bot.send_message(
            chat_id=tg_id,
            text=user_text,
            reply_markup=sub_config_links_keyboard(email),
            parse_mode="HTML",
        )

        # Update admin message
        admin_text = callback.message.text or callback.message.caption or ""  # type: ignore[union-attr]
        admin_text += f"\n\n✅ تأیید شد — سرویس ساخته شد: {email}"
        try:
            if callback.message.photo:  # type: ignore[union-attr]
                await callback.message.edit_caption(  # type: ignore[union-attr]
                    caption=admin_text,
                    parse_mode="HTML",
                )
            else:
                await callback.message.edit_text(  # type: ignore[union-attr]
                    text=admin_text,
                    parse_mode="HTML",
                )
        except Exception:
            pass

    except Exception as e:
        logger.exception(
            "Failed to create client after approval for invoice %s", invoice_id
        )
        await update_invoice_status(invoice_id, "paid")  # Revert status
        await callback.answer(f"❌ خطا در ساخت اشتراک: {e}", show_alert=True)
        return

    await callback.answer("✅ تأیید شد", show_alert=False)


# ──────────────────────────── Reject Payment ────────────────────────────


@router.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject(callback: types.CallbackQuery, bot: Bot) -> None:
    """Admin rejects a payment — notify user."""
    if not _is_admin(callback):
        await callback.answer("❌ شما دسترسی ندارید.", show_alert=True)
        return

    invoice_id = callback.data[len("admin_reject_") :]  # type: ignore[union-attr]
    invoice = await get_invoice(invoice_id)

    if not invoice:
        await callback.answer("❌ فاکتور یافت نشد.", show_alert=True)
        return

    if invoice["status"] not in ("paid", "pending"):
        await callback.answer("❌ این فاکتور قبلاً پردازش شده.", show_alert=True)
        return

    tg_id = invoice["tg_id"]

    # Update invoice status
    await update_invoice_status(invoice_id, "rejected")

    # Remove buttons from admin message
    await callback.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]

    # Update admin message
    admin_text = callback.message.text or callback.message.caption or ""  # type: ignore[union-attr]
    admin_text += "\n\n❌ رد شد"
    try:
        if callback.message.photo:  # type: ignore[union-attr]
            await callback.message.edit_caption(  # type: ignore[union-attr]
                caption=admin_text,
                parse_mode="HTML",
            )
        else:
            await callback.message.edit_text(  # type: ignore[union-attr]
                text=admin_text,
                parse_mode="HTML",
            )
    except Exception:
        pass

    # Notify user
    await bot.send_message(
        chat_id=tg_id,
        text=(
            f"❌ <b>پرداخت شما تأیید نشد.</b>\n\n"
            f"🆔 فاکتور: <code>{invoice_id}</code>\n\n"
            "در صورت اطمینان از واریز، با پشتیبانی تماس بگیرید."
        ),
        parse_mode="HTML",
    )

    await callback.answer("❌ رد شد", show_alert=False)
