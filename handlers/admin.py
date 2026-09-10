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
from utils.formatting import to_persian_digits

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
    from services.invoice_service import approve_invoice

    success, msg, inv_info = await approve_invoice(invoice_id, bot, is_reapproval=False)
    if not success:
        await callback.answer(msg, show_alert=True)
        return

    import html

    admin_name = html.escape(
        callback.from_user.full_name
        or (f"@{callback.from_user.username}" if callback.from_user.username else "")
        or str(callback.from_user.id)
    )
    admin_mention = f'<a href="tg://user?id={callback.from_user.id}">{admin_name}</a>'

    admin_text = callback.message.text or callback.message.caption or ""
    if "وضعیت فعلی:" in admin_text:
        admin_text = re.sub(
            r"🔘 <b>وضعیت فعلی:</b> [^\n]+",
            "🔘 <b>وضعیت فعلی:</b> 🟢 تأییدشده",
            admin_text,
        )
        admin_text += f"\n\n✅ <b>تأیید شد توسط {admin_mention} — {msg}</b>"
    else:
        admin_text += f"\n\n✅ تأیید شد توسط {admin_mention} — {msg}"

    from keyboards.inline_kb import admin_invoice_processed_keyboard
    from utils.helpers import safe_edit_text

    tg_id = inv_info.get("tg_id", 0) if inv_info else 0
    new_kb = admin_invoice_processed_keyboard(
        invoice_id=invoice_id,
        tg_id=tg_id,
        is_rejected=False,
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

    if invoice["status"] not in ("paid", "pending"):
        await callback.answer("❌ این فاکتور قبلاً پردازش شده.", show_alert=True)
        return

    tg_id = invoice["tg_id"]

    await update_invoice_status(invoice_id, "rejected")

    import html

    admin_name = html.escape(
        callback.from_user.full_name
        or (f"@{callback.from_user.username}" if callback.from_user.username else "")
        or str(callback.from_user.id)
    )
    admin_mention = f'<a href="tg://user?id={callback.from_user.id}">{admin_name}</a>'

    admin_text = callback.message.text or callback.message.caption or ""
    if "وضعیت فعلی:" in admin_text:
        admin_text = re.sub(
            r"🔘 <b>وضعیت فعلی:</b> [^\n]+",
            "🔘 <b>وضعیت فعلی:</b> 🔴 ردشده",
            admin_text,
        )
        admin_text += f"\n\n❌ <b>پرداخت توسط ادمین ({admin_mention}) رد شد.</b>"
    else:
        admin_text += f"\n\n❌ رد شد توسط {admin_mention}"

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
