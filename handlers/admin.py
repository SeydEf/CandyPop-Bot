"""
Admin-side handlers.

Handles payment approval/rejection from the admin.
Only the ADMIN_CHAT_ID can trigger these callbacks.
"""

from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router, types

from config import ADMIN_CHAT_ID, INBOUND_IDS, SUB_BASE_URL
from db.models import (
    create_subscription,
    get_invoice,
    update_invoice_status,
)
from services import xui_api
from utils.formatting import format_size_gb
from utils.helpers import gb_to_bytes, generate_email, generate_service_name

logger = logging.getLogger(__name__)
router = Router(name="admin")


def _is_admin(callback: types.CallbackQuery) -> bool:
    return callback.from_user is not None and callback.from_user.id == ADMIN_CHAT_ID


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
        # Create client in X-UI
        email = generate_email(tg_id)
        service_name = generate_service_name(tg_id)
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

        # Save subscription
        await create_subscription(
            tg_id=tg_id,
            email=email,
            sub_id=sub_id,
            service_name=service_name,
            data_gb=gb,
            duration_days=duration,
        )

        sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

        # Get config links
        config_links = await xui_api.get_client_links(email)
        links_text = ""
        if config_links:
            links_text = "\n\n🔗 <b>لینک‌های کانفیگ:</b>\n"
            for link in config_links:
                links_text += f"<code>{link}</code>\n\n"

        # Notify user
        user_text = (
            f"✅ <b>پرداخت شما تأیید شد و اشتراک فعال گردید!</b>\n\n"
            f"🆔 فاکتور: <code>{invoice_id}</code>\n"
            f"📦 نام سرویس: {service_name}\n"
            f"⏱ مدت: {duration} روز\n"
            f"📊 حجم: {format_size_gb(gb)}\n\n"
            f"🔗 لینک اشتراک:\n<code>{sub_link}</code>"
            f"{links_text}"
        )
        await bot.send_message(
            chat_id=tg_id,
            text=user_text,
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
