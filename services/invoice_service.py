from __future__ import annotations

import logging
import time
from typing import Any

from aiogram import Bot
from aiogram.types import BufferedInputFile

from config import SUB_BASE_URL
from db.discounts import increment_discount_usage
from db.models import (
    credit_wallet,
    get_active_client_group,
    get_active_inbound_ids,
    get_invoice,
    get_start_first_use_config,
    get_user,
    process_referral_commission,
    update_invoice_status,
)
from keyboards.inline_kb import sub_config_links_keyboard
from services import xui_api
from utils.formatting import format_price, format_size_gb, to_persian_digits
from utils.helpers import gb_to_bytes, generate_email, generate_qr

logger = logging.getLogger(__name__)


async def approve_invoice(
    invoice_id: str,
    bot: Bot,
    is_reapproval: bool = False,
) -> tuple[bool, str, dict[str, Any] | None]:
    invoice = await get_invoice(invoice_id)
    if not invoice:
        return False, "❌ فاکتور یافت نشد.", None

    current_status = invoice.get("status")
    if not is_reapproval and current_status not in ("paid", "pending"):
        return False, "❌ این فاکتور قبلاً پردازش شده است.", invoice
    if is_reapproval and current_status != "rejected":
        return (
            False,
            f"❌ این فاکتور در وضعیت ردشده قرار ندارد (وضعیت فعلی: {current_status}).",
            invoice,
        )

    tg_id = invoice["tg_id"]
    duration = invoice["duration_days"]
    gb = invoice["data_gb"]
    amount = invoice["amount"]
    users_count = invoice.get("users_count", 1)
    target_email = invoice.get("target_email")

    await update_invoice_status(invoice_id, "approved")

    prefix_msg = (
        "✅ <b>پرداخت شما پس از بازبینی مجدد توسط مدیریت تأیید شد و "
        if is_reapproval
        else "✅ <b>پرداخت شما تأیید شد و "
    )

    try:
        if target_email == "TOPUP" or (duration == 0 and gb == 0):
            new_balance = await credit_wallet(tg_id, amount)

            user_text = (
                f"{prefix_msg}کیف پول شما شارژ گردید!</b>\n\n"
                f"🆔 فاکتور: <code>{invoice_id}</code>\n"
                f"💰 مبلغ واریزی: {format_price(amount)}\n"
                f"👛 موجودی جدید کیف پول: {format_price(new_balance)}"
            )
            await bot.send_message(chat_id=tg_id, text=user_text, parse_mode="HTML")
            return True, f"کیف پول کاربر شارژ گردید (+{format_price(amount)})", invoice

        if invoice.get("discount_code"):
            await increment_discount_usage(invoice["discount_code"], tg_id)

        await process_referral_commission(tg_id, amount, bot)

        if target_email:
            email = target_email
            await xui_api.renew_client(
                email=email,
                duration_days=duration,
                data_gb=gb,
                users_count=users_count,
            )
            action_msg = "اشتراک تمدید گردید"
        else:
            user = await get_user(tg_id)
            username = user.get("username") if user else None
            email = generate_email(tg_id, username)
            total_bytes = gb_to_bytes(gb)

            start_first_use = await get_start_first_use_config()
            if start_first_use and duration > 0:
                expiry_ms = -int(duration * 86400 * 1000)
            else:
                expiry_ms = (
                    int((time.time() + duration * 86400) * 1000) if duration > 0 else 0
                )

            active_inbound_ids = await get_active_inbound_ids()
            active_group = await get_active_client_group()

            await xui_api.add_client(
                email=email,
                total_gb=total_bytes,
                expiry_time=expiry_ms,
                tg_id=tg_id,
                inbound_ids=active_inbound_ids,
                limit_ip=users_count,
                group=active_group,
            )
            action_msg = "اشتراک فعال گردید"

        client = await xui_api.get_client(email)
        sub_id = client.get("subId", "") if client else ""
        sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

        user_text = (
            f"{prefix_msg}{action_msg}!</b>\n\n"
            f"🆔 فاکتور: <code>{invoice_id}</code>\n"
            f"📦 نام سرویس: {email}\n"
            f"⏱ مدت: {duration} روز\n"
            f"👤 تعداد کاربر: {to_persian_digits(users_count)} کاربر\n"
            f"📊 حجم: {format_size_gb(gb)}\n\n"
            f"🔗 لینک اشتراک:\n<code>{sub_link}</code>"
        )

        if sub_id:
            qr_buf = generate_qr(sub_link)
            photo = BufferedInputFile(qr_buf.getvalue(), filename="qrcode.png")
            await bot.send_photo(
                chat_id=tg_id,
                photo=photo,
                caption=user_text,
                reply_markup=sub_config_links_keyboard(email),
                parse_mode="HTML",
            )
        else:
            await bot.send_message(
                chat_id=tg_id,
                text=user_text,
                reply_markup=sub_config_links_keyboard(email),
                parse_mode="HTML",
            )

        return True, f"سرویس فعال گردید ({email})", invoice

    except Exception as e:
        logger.exception("Failed to process approval for invoice %s", invoice_id)
        rollback_status = "rejected" if is_reapproval else "paid"
        await update_invoice_status(invoice_id, rollback_status)
        return False, f"خطا در پردازش فاکتور: {e}", invoice
