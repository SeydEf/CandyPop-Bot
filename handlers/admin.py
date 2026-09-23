from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router, types
from aiogram.filters import Command

from config import SUPPORT_LINK
from db.models import (
    get_invoice,
    reset_all_test_subs,
    update_invoice_status,
)
from utils.formatting import format_datetime, to_persian_digits

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


async def render_admin_notification_view(
    message: types.Message,
    invoice_id: str,
    admin_user_id: int,
    status_note: str | None = None,
) -> bool:
    from db.models import get_invoice, get_user, has_admin_permission
    from handlers.buy import _build_admin_invoice_notification_text
    from keyboards.inline_kb import (
        admin_invoice_processed_keyboard,
        admin_payment_review_keyboard,
    )
    from utils.helpers import safe_edit_text

    inv = await get_invoice(invoice_id)
    if not inv:
        return False

    tg_id = inv["tg_id"]
    user = await get_user(tg_id)
    username = user.get("username") if user else None
    receipt_type = (
        "text"
        if (inv.get("receipt_text") and not inv.get("receipt_file_id"))
        else "card"
    )

    base_text = await _build_admin_invoice_notification_text(
        invoice=inv,
        user_id=tg_id,
        username=username,
        receipt_type=receipt_type,
        receipt_text=inv.get("receipt_text"),
    )

    status = inv.get("status", "pending")
    status_badges = {
        "approved": "🟢 تأییدشده",
        "under_review": "⏳ در حال بررسی رسید",
        "pending": "🟡 در انتظار پرداخت",
        "rejected": "🔴 ردشده",
        "expired": "⌛️ منقضی‌شده",
    }
    badge = status_badges.get(status, status)
    base_text += f"\n\n🔘 <b>وضعیت فعلی:</b> {badge}"

    proc_at = inv.get("processed_at")
    proc_by_id = inv.get("processed_by")
    proc_by_name = inv.get("processed_by_name")

    if proc_by_id and proc_by_name:
        admin_mention = f'<a href="tg://user?id={proc_by_id}">{proc_by_name}</a>'
    elif proc_by_name:
        admin_mention = proc_by_name
    elif proc_by_id:
        admin_mention = f'<a href="tg://user?id={proc_by_id}">{proc_by_id}</a>'
    else:
        admin_mention = "ادمین"

    proc_time_str = format_datetime(proc_at) if proc_at else None

    if status_note:
        base_text += f"\n\n{status_note}"
        if proc_time_str:
            action_label = "زمان تأیید" if status in ("approved", "paid") else "زمان رد"
            base_text += f"\n⏱ <b>{action_label}:</b> <code>{proc_time_str}</code> (توسط: {admin_mention})"
    elif status == "approved":
        base_text += "\n\n✅ <b>این فاکتور قبلاً تأیید شده است.</b>"
        if proc_time_str:
            base_text += f"\n⏱ <b>زمان تأیید:</b> <code>{proc_time_str}</code> (توسط: {admin_mention})"
    elif status == "rejected":
        base_text += "\n\n❌ <b>این فاکتور توسط ادمین رد شده است.</b>"
        if proc_time_str:
            base_text += f"\n⏱ <b>زمان رد:</b> <code>{proc_time_str}</code> (توسط: {admin_mention})"
    elif status == "expired":
        base_text += (
            "\n\n⌛️ <b>مهلت پرداخت این فاکتور به پایان رسیده و منقضی شده است.</b>"
        )

    if status in ("pending", "under_review"):
        kb = admin_payment_review_keyboard(invoice_id)
    else:
        can_approve = await has_admin_permission(admin_user_id, "approve_invoices")
        can_reapprove = await has_admin_permission(admin_user_id, "reapprove_invoices")
        kb = admin_invoice_processed_keyboard(
            invoice_id=invoice_id,
            tg_id=tg_id,
            is_rejected=(status == "rejected"),
            is_expired=(status == "expired"),
            can_reapprove=can_reapprove,
            can_approve=can_approve,
            origin="notif",
        )

    await safe_edit_text(message, base_text, reply_markup=kb, parse_mode="HTML")
    return True


@router.callback_query(F.data.startswith("admin_notif_return_"))
async def admin_notif_return(callback: types.CallbackQuery) -> None:
    if not await _is_admin(callback):
        return

    invoice_id = callback.data[len("admin_notif_return_") :]
    success = await render_admin_notification_view(
        callback.message, invoice_id, callback.from_user.id
    )
    if not success:
        await callback.answer("❌ فاکتور یافت نشد.", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith("admin_approve_"))
async def admin_approve(callback: types.CallbackQuery, bot: Bot) -> None:
    if not await _is_admin(callback):
        await callback.answer("❌ شما دسترسی ندارید.", show_alert=True)
        return

    invoice_id = callback.data[len("admin_approve_") :]
    from services.invoice_service import approve_invoice

    import html
    from datetime import datetime, timezone

    admin_name = html.escape(
        callback.from_user.full_name
        or (f"@{callback.from_user.username}" if callback.from_user.username else "")
        or str(callback.from_user.id)
    )
    admin_mention = f'<a href="tg://user?id={callback.from_user.id}">{admin_name}</a>'

    success, msg, inv_info = await approve_invoice(
        invoice_id,
        bot,
        is_reapproval=False,
        admin_user_id=callback.from_user.id,
        admin_name=admin_name,
    )
    if not success:
        await callback.answer(msg, show_alert=True)
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    now_str = format_datetime(now_iso)

    admin_text = callback.message.text or callback.message.caption or ""
    time_line = f"⏱ <b>زمان تأیید:</b> <code>{now_str}</code> (توسط: {admin_mention})"
    if "وضعیت فعلی:" in admin_text:
        admin_text = re.sub(
            r"🔘 <b>وضعیت فعلی:</b> [^\n]+",
            "🔘 <b>وضعیت فعلی:</b> 🟢 تأییدشده",
            admin_text,
        )
        admin_text += f"\n\n✅ <b>تأیید شد — {msg}</b>\n{time_line}"
    else:
        admin_text += f"\n\n✅ <b>تأیید شد — {msg}</b>\n{time_line}"

    from keyboards.inline_kb import admin_invoice_processed_keyboard
    from utils.helpers import safe_edit_text

    tg_id = inv_info.get("tg_id", 0) if inv_info else 0
    new_kb = admin_invoice_processed_keyboard(
        invoice_id=invoice_id,
        tg_id=tg_id,
        is_rejected=False,
        origin="notif",
    )

    await safe_edit_text(
        callback.message,
        admin_text,
        reply_markup=new_kb,
        parse_mode="HTML",
    )
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

    if invoice["status"] not in ("pending", "under_review"):
        await callback.answer("❌ این فاکتور قبلاً پردازش شده.", show_alert=True)
        return

    tg_id = invoice["tg_id"]

    import html
    from datetime import datetime, timezone

    admin_name = html.escape(
        callback.from_user.full_name
        or (f"@{callback.from_user.username}" if callback.from_user.username else "")
        or str(callback.from_user.id)
    )
    admin_mention = f'<a href="tg://user?id={callback.from_user.id}">{admin_name}</a>'

    now_iso = datetime.now(timezone.utc).isoformat()
    now_str = format_datetime(now_iso)

    await update_invoice_status(
        invoice_id,
        "rejected",
        processed_at=now_iso,
        processed_by=callback.from_user.id,
        processed_by_name=admin_name,
    )

    admin_text = callback.message.text or callback.message.caption or ""
    time_line = f"⏱ <b>زمان رد:</b> <code>{now_str}</code> (توسط: {admin_mention})"
    if "وضعیت فعلی:" in admin_text:
        admin_text = re.sub(
            r"🔘 <b>وضعیت فعلی:</b> [^\n]+",
            "🔘 <b>وضعیت فعلی:</b> 🔴 ردشده",
            admin_text,
        )
        admin_text += f"\n\n❌ <b>پرداخت رد شد.</b>\n{time_line}"
    else:
        admin_text += f"\n\n❌ <b>پرداخت رد شد.</b>\n{time_line}"

    from db.models import has_admin_permission
    from keyboards.inline_kb import admin_invoice_processed_keyboard
    from utils.helpers import safe_edit_text

    can_reapprove = await has_admin_permission(
        callback.from_user.id, "reapprove_invoices"
    )
    new_kb = admin_invoice_processed_keyboard(
        invoice_id=invoice_id,
        tg_id=tg_id,
        is_rejected=True,
        can_reapprove=can_reapprove,
        origin="notif",
    )

    await safe_edit_text(
        callback.message,
        admin_text,
        reply_markup=new_kb,
        parse_mode="HTML",
    )

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
