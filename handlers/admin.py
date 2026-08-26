from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router, types
from aiogram.filters import Command

from config import SUB_BASE_URL, SUPPORT_LINK
from db.models import (
    get_invoice,
    get_user,
    reset_all_test_subs,
    update_invoice_status,
)
from keyboards.inline_kb import sub_config_links_keyboard
from services import xui_api
from utils.formatting import format_price, format_size_gb, to_persian_digits
from utils.helpers import gb_to_bytes, generate_email

logger = logging.getLogger(__name__)
router = Router(name="admin")


async def _is_admin(event: types.CallbackQuery | types.Message) -> bool:
    from db.models import has_admin_permission

    if event.from_user is None:
        return False
    permitted = await has_admin_permission(event.from_user.id, "approve_invoices")
    if not permitted:
        msg = "⛔️ شما دسترسی به بخش «تأیید و رد پرداخت فاکتورها» را ندارید."
        if isinstance(event, types.CallbackQuery):
            await event.answer(msg, show_alert=True)
        else:
            await event.answer(msg)
        return False
    return True


@router.message(Command("reset_test", "reset_tests"))
async def admin_reset_tests(message: types.Message) -> None:
    from db.models import has_admin_permission

    if not message.from_user or not await has_admin_permission(
        message.from_user.id, "reset_configs"
    ):
        await message.answer("⛔️ شما دسترسی به بازنشانی تنظیمات را ندارید.")
        return

    count = await reset_all_test_subs()
    await message.answer(
        f"✅ <b>اشتراک‌های تست تمامی کاربران با موفقیت بازنشانی شد!</b>\n\n"
        f"👥 تعداد کاربران به‌روزرسانی شده: {to_persian_digits(count)}",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_approve_"))
async def admin_approve(callback: types.CallbackQuery, bot: Bot) -> None:
    if not await _is_admin(callback):
        await callback.answer("❌ شما دسترسی ندارید.", show_alert=True)
        return

    invoice_id = callback.data[len("admin_approve_") :]
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
    amount = invoice["amount"]

    await update_invoice_status(invoice_id, "approved")

    await callback.message.edit_reply_markup(reply_markup=None)

    try:
        users_count = invoice.get("users_count", 1)
        target_email = invoice.get("target_email")

        if target_email == "TOPUP" or (duration == 0 and gb == 0):
            from db.models import credit_wallet

            amount = invoice["amount"]
            new_balance = await credit_wallet(tg_id, amount)

            user_text = (
                f"✅ <b>پرداخت شما تأیید شد و کیف پول شارژ گردید!</b>\n\n"
                f"🆔 فاکتور: <code>{invoice_id}</code>\n"
                f"💰 مبلغ واریزی: {format_price(amount)}\n"
                f"👛 موجودی جدید کیف پول: {format_price(new_balance)}"
            )
            await bot.send_message(chat_id=tg_id, text=user_text, parse_mode="HTML")
            from utils.helpers import safe_edit_text

            await safe_edit_text(
                callback.message,
                f"✅ فاکتور <code>{invoice_id}</code> (شارژ کیف پول به مبلغ {format_price(amount)}) با موفقیت تأیید شد.",
                parse_mode="HTML",
            )
            return

        if invoice.get("discount_code"):
            from db.discounts import increment_discount_usage

            await increment_discount_usage(invoice["discount_code"], tg_id)

        from db.models import process_referral_commission

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
            expiry_ms = int((time.time() + duration * 86400) * 1000)

            from db.models import get_active_client_group, get_active_inbound_ids

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
            f"✅ <b>پرداخت شما تأیید شد و {action_msg}!</b>\n\n"
            f"🆔 فاکتور: <code>{invoice_id}</code>\n"
            f"📦 نام سرویس: {email}\n"
            f"⏱ مدت: {duration} روز\n"
            f"👤 تعداد کاربر: {to_persian_digits(users_count)} کاربر\n"
            f"📊 حجم: {format_size_gb(gb)}\n\n"
            f"🔗 لینک اشتراک:\n<code>{sub_link}</code>"
        )
        if sub_id:
            from aiogram.types import BufferedInputFile
            from utils.helpers import generate_qr

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

        admin_text = callback.message.text or callback.message.caption or ""
        admin_text += f"\n\n✅ تأیید شد — سرویس ساخته شد: {email}"
        try:
            if callback.message.photo:
                await callback.message.edit_caption(
                    caption=admin_text,
                    parse_mode="HTML",
                )
            else:
                await callback.message.edit_text(
                    text=admin_text,
                    parse_mode="HTML",
                )
        except Exception:
            pass

    except Exception as e:
        logger.exception(
            "Failed to create client after approval for invoice %s", invoice_id
        )
        await update_invoice_status(invoice_id, "paid")
        await callback.answer(f"❌ خطا در ساخت اشتراک: {e}", show_alert=True)
        return

    await callback.answer("✅ تأیید شد", show_alert=False)


@router.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject(callback: types.CallbackQuery, bot: Bot) -> None:
    if not await _is_admin(callback):
        await callback.answer("❌ شما دسترسی ندارید.", show_alert=True)
        return

    invoice_id = callback.data[len("admin_reject_") :]
    invoice = await get_invoice(invoice_id)

    if not invoice:
        await callback.answer("❌ فاکتور یافت نشد.", show_alert=True)
        return

    if invoice["status"] not in ("paid", "pending"):
        await callback.answer("❌ این فاکتور قبلاً پردازش شده.", show_alert=True)
        return

    tg_id = invoice["tg_id"]

    await update_invoice_status(invoice_id, "rejected")

    await callback.message.edit_reply_markup(reply_markup=None)

    admin_text = callback.message.text or callback.message.caption or ""
    admin_text += "\n\n❌ رد شد"
    try:
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=admin_text,
                parse_mode="HTML",
            )
        else:
            await callback.message.edit_text(
                text=admin_text,
                parse_mode="HTML",
            )
    except Exception:
        pass

    await bot.send_message(
        chat_id=tg_id,
        text=(
            f"❌ <b>پرداخت شما تأیید نشد.</b>\n\n"
            f"🆔 فاکتور: <code>{invoice_id}</code>\n\n"
            "در صورت اطمینان از واریز، با پشتیبانی تماس بگیرید.\n"
            f"👨‍💻 <b>آیدی پشتیبانی:</b> {SUPPORT_LINK}"
        ),
        parse_mode="HTML",
    )

    await callback.answer("❌ رد شد", show_alert=False)
