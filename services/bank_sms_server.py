from __future__ import annotations

import html
import logging
import secrets
from typing import Any

from aiohttp import web
from aiogram import Bot

from config import ADMIN_CHAT_ID
from db.models import (
    find_pending_card_invoice_by_amount,
    get_all_admins,
    get_bank_sms_config,
    has_admin_permission,
    is_sms_fingerprint_processed,
    record_bank_sms_transaction,
)
from services.invoice_service import approve_invoice
from utils.formatting import format_datetime, format_price, to_persian_digits

logger = logging.getLogger(__name__)

_runner: web.AppRunner | None = None
_site: web.TCPSite | None = None
_bot_instance: Bot | None = None


async def handle_bank_sms_webhook(request: web.Request) -> web.Response:
    bot = _bot_instance
    if not bot:
        return web.json_response(
            {"error": "Server Not Ready", "detail": "سرور هنوز آماده نیست."},
            status=503,
        )

    cfg = await get_bank_sms_config()
    if not cfg.get("enabled", True):
        return web.json_response(
            {
                "error": "Disabled",
                "detail": "سیستم تایید خودکار پیامک بانکی غیرفعال است.",
            },
            status=503,
        )

    api_key_header = request.headers.get("X-Api-Key") or request.headers.get(
        "x-api-key"
    )
    secret_key = cfg.get("secret_key", "")
    if not api_key_header or not secrets.compare_digest(api_key_header, secret_key):
        return web.json_response(
            {"error": "Unauthorized", "detail": "کلید احراز هویت نامعتبر است."},
            status=401,
        )

    try:
        data: dict[str, Any] = await request.json()
    except Exception as e:
        logger.warning("Bank SMS Webhook invalid JSON: %s", e)
        return web.json_response(
            {"error": "Bad Request", "detail": "قالب JSON نامعتبر است."},
            status=400,
        )

    fingerprint = str(data.get("fingerprint") or "").strip()
    amount_toman = data.get("amount_toman")
    amount_rial = data.get("amount_rial") or (
        int(amount_toman) * 10 if amount_toman else 0
    )

    if not fingerprint or amount_toman is None:
        return web.json_response(
            {
                "error": "Bad Request",
                "detail": "فیلدهای الزامی amount_toman و fingerprint یافت نشد.",
            },
            status=400,
        )

    try:
        amount_toman = int(amount_toman)
        amount_rial = int(amount_rial)
    except (TypeError, ValueError):
        return web.json_response(
            {"error": "Bad Request", "detail": "مبالغ باید عددی باشند."},
            status=400,
        )

    sender = data.get("sender")
    account_number = data.get("account_number")
    balance_rial = data.get("balance_rial")
    timestamp_str = data.get("timestamp_str")
    raw_sms = data.get("raw_sms")
    received_at = data.get("received_at")

    if await is_sms_fingerprint_processed(fingerprint):
        logger.info("Bank SMS already processed: %s", fingerprint)
        return web.json_response(
            {
                "status": "already_processed",
                "message": "این واریز قبلاً پردازش و تایید شده است.",
                "fingerprint": fingerprint,
            },
            status=200,
        )

    invoice = await find_pending_card_invoice_by_amount(amount_toman)
    if not invoice:
        logger.info(
            "Bank SMS unmatched: amount_toman=%d, fingerprint=%s",
            amount_toman,
            fingerprint,
        )
        await record_bank_sms_transaction(
            fingerprint=fingerprint,
            amount_toman=amount_toman,
            amount_rial=amount_rial,
            sender=sender,
            account_number=account_number,
            balance_rial=balance_rial,
            timestamp_str=timestamp_str,
            raw_sms=raw_sms,
            received_at=received_at,
            status="unmatched",
        )
        return web.json_response(
            {
                "status": "unmatched",
                "message": f"هیچ فاکتور معلقی با مبلغ {amount_toman} تومان یافت نشد.",
                "amount_toman": amount_toman,
            },
            status=422,
        )

    invoice_id = invoice["id"]
    tg_id = invoice["tg_id"]

    await record_bank_sms_transaction(
        fingerprint=fingerprint,
        amount_toman=amount_toman,
        amount_rial=amount_rial,
        sender=sender,
        account_number=account_number,
        balance_rial=balance_rial,
        timestamp_str=timestamp_str,
        raw_sms=raw_sms,
        received_at=received_at,
        status="success",
        invoice_id=invoice_id,
    )

    success, msg, _ = await approve_invoice(
        invoice_id=invoice_id,
        bot=bot,
        is_reapproval=False,
        admin_name="تأیید خودکار از طریق پیامک بانکی",
    )

    if not success:
        logger.error("Failed to approve invoice %s via bank SMS: %s", invoice_id, msg)
        return web.json_response(
            {"status": "error", "message": f"خطا در فعال‌سازی سفارش: {msg}"},
            status=500,
        )

    admin_notice, review_caption = await _build_bank_sms_admin_messages(
        invoice=invoice,
        amount_toman=amount_toman,
        fingerprint=fingerprint,
        delivery_msg=msg,
    )

    review_msg_id = invoice.get("message_id")
    if review_msg_id:
        try:
            from keyboards.inline_kb import admin_invoice_processed_keyboard

            admin_ids = [ADMIN_CHAT_ID]
            for adm in await get_all_admins():
                a_id = adm["tg_id"]
                if a_id > 0 and a_id not in admin_ids:
                    if await has_admin_permission(a_id, "approve_invoices"):
                        admin_ids.append(a_id)

            for a_id in admin_ids:
                try:
                    await bot.edit_message_caption(
                        chat_id=a_id,
                        message_id=review_msg_id,
                        caption=review_caption,
                        reply_markup=admin_invoice_processed_keyboard(
                            invoice_id=invoice_id,
                            tg_id=tg_id,
                            is_rejected=False,
                            origin="notif",
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    try:
                        await bot.edit_message_text(
                            chat_id=a_id,
                            message_id=review_msg_id,
                            text=review_caption,
                            reply_markup=admin_invoice_processed_keyboard(
                                invoice_id=invoice_id,
                                tg_id=tg_id,
                                is_rejected=False,
                                origin="notif",
                            ),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(
                "Could not update review message for auto-approved invoice: %s", e
            )

    try:
        admin_ids = [ADMIN_CHAT_ID]
        for adm in await get_all_admins():
            a_id = adm["tg_id"]
            if a_id > 0 and a_id not in admin_ids:
                if await has_admin_permission(a_id, "approve_invoices"):
                    admin_ids.append(a_id)

        from keyboards.inline_kb import admin_invoice_processed_keyboard

        admin_keyboard = admin_invoice_processed_keyboard(
            invoice_id=invoice_id,
            tg_id=tg_id,
            is_rejected=False,
            origin="notif",
        )

        for a_id in admin_ids:
            try:
                await bot.send_message(
                    chat_id=a_id,
                    text=admin_notice,
                    reply_markup=admin_keyboard,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(
                    "Failed to send bank SMS notice to admin %d: %s", a_id, e
                )
    except Exception as e:
        logger.warning("Error notifying admin of auto-approved invoice: %s", e)

    return web.json_response(
        {
            "status": "success",
            "order_id": invoice_id,
            "user_id": tg_id,
            "amount_toman": amount_toman,
            "message": "تراکنش با موفقیت تایید شد.",
        },
        status=200,
    )


async def _build_bank_sms_admin_messages(
    invoice: dict[str, Any],
    amount_toman: int,
    fingerprint: str,
    delivery_msg: str,
) -> tuple[str, str]:
    from db.models import get_user
    from services.pricing import format_volume_label

    tg_id = invoice["tg_id"]
    invoice_id = invoice["id"]
    user = await get_user(tg_id)
    username = user.get("username") if user else None
    full_name = user.get("full_name") if user else None

    user_disp = f"<code>{tg_id}</code>"
    if username:
        user_disp += f" (@{username})"
    if full_name:
        user_disp += f" | {html.escape(full_name)}"

    target_email = invoice.get("target_email")
    is_topup = target_email == "TOPUP" or (
        invoice.get("duration_days") == 0 and invoice.get("data_gb") == 0
    )
    is_renewal = bool(target_email and target_email != "TOPUP")

    duration = invoice.get("duration_days", 0)
    gb = invoice.get("data_gb", 0)
    users_count = invoice.get("users_count", 1)

    service_email = target_email if is_renewal else ""
    if (
        not service_email
        and delivery_msg
        and "(" in delivery_msg
        and ")" in delivery_msg
    ):
        start_idx = delivery_msg.find("(")
        end_idx = delivery_msg.rfind(")")
        if start_idx != -1 and end_idx != -1:
            service_email = delivery_msg[start_idx + 1 : end_idx].strip()

    if is_topup:
        order_title = "⚡️ <b>تأیید خودکار افزایش موجودی کیف پول (پیامک بانکی)</b>"
        order_header = "👛 <b>مشخصات تراکنش:</b>"
        specs_lines = [
            "• نوع عملیات: <b>شارژ مستقیم کیف پول</b>",
            f"• مبلغ شارژ: <b>{format_price(amount_toman)}</b>",
        ]
    elif is_renewal:
        order_title = "⚡️ <b>تأیید خودکار تمدید اشتراک (پیامک بانکی)</b>"
        order_header = "🔄 <b>مشخصات بسته تمدید:</b>"
        vol_label = await format_volume_label(gb, with_real_gb=True)
        specs_lines = [
            f"• نام سرویس: <code>{target_email}</code>",
            f"• مدت اعتبار: <b>{to_persian_digits(duration)} روز</b>",
            f"• ظرفیت اتصال: <b>{to_persian_digits(users_count)} کاربر</b>",
            f"• حجم ترافیک: <b>{vol_label}</b>",
        ]
    else:
        order_title = "⚡️ <b>تأیید خودکار پرداخت اشتراک (پیامک بانکی)</b>"
        order_header = "📦 <b>مشخصات سرویس:</b>"
        vol_label = await format_volume_label(gb, with_real_gb=True)
        specs_lines = []
        if service_email:
            specs_lines.append(f"• نام سرویس: <code>{service_email}</code>")
        specs_lines.extend(
            [
                f"• مدت اعتبار: <b>{to_persian_digits(duration)} روز</b>",
                f"• ظرفیت اتصال: <b>{to_persian_digits(users_count)} کاربر</b>",
                f"• حجم ترافیک: <b>{vol_label}</b>",
            ]
        )

    specs_str = "\n".join(specs_lines)

    disc_code = invoice.get("discount_code")
    orig_amount = invoice.get("original_amount") or invoice.get("amount", amount_toman)
    disc_lines = []
    if orig_amount > amount_toman:
        disc_lines.append(f"• مبلغ پایه: <s>{format_price(orig_amount)}</s>")
        if disc_code:
            disc_lines.append(f"• کد تخفیف: <code>{disc_code}</code>")
        elif is_renewal:
            disc_lines.append("• تخفیف: <b>تخفیف ویژه تمدید اشتراک</b>")
        else:
            disc_lines.append("• تخفیف: <b>تخفیف پیش‌فرض</b>")
    disc_str = ("\n" + "\n".join(disc_lines)) if disc_lines else ""

    price_str = f"💰 <b>مبلغ واریزی:</b> <b>{format_price(amount_toman)}</b>"
    bank_lines = [
        f"• اثرانگشت تراکنش: <code>{fingerprint}</code>",
    ]
    bank_str = "\n".join(bank_lines)

    admin_notice = (
        f"{order_title}\n\n"
        f"🧾 <b>شماره فاکتور:</b> <code>{invoice_id}</code>\n"
        f"👤 <b>کاربر:</b> {user_disp}\n\n"
        f"{order_header}\n"
        f"{specs_str}"
        f"{disc_str}\n"
        f"{price_str}\n\n"
        f"💳 <b>اطلاعات تراکنش پیامکی:</b>\n"
        f"{bank_str}\n\n"
        f"✅ <b>وضعیت:</b> {delivery_msg}\n"
        f"⏳ <b>زمان تایید تراکنش:</b> {format_datetime(invoice.get('processed_at'))}"
    )

    review_caption = (
        f"⚡️ <b>این پرداخت به صورت خودکار توسط پیامک بانکی تأیید شد.</b>\n\n"
        f"🧾 <b>شماره فاکتور:</b> <code>{invoice_id}</code>\n"
        f"👤 <b>کاربر:</b> {user_disp}\n\n"
        f"{order_header}\n"
        f"{specs_str}"
        f"{disc_str}\n"
        f"{price_str}\n\n"
        f"✅ <b>{delivery_msg}</b>"
    )
    if len(review_caption) > 1024:
        review_caption = review_caption[:1020] + "..."

    return admin_notice, review_caption


async def start_bank_sms_server(bot: Bot) -> None:
    global _runner, _site, _bot_instance
    _bot_instance = bot

    cfg = await get_bank_sms_config()
    host = cfg.get("host", "0.0.0.0")
    port = cfg.get("port", 24318)

    app = web.Application()
    app.router.add_post("/api/v1/payments/bank-sms-webhook", handle_bank_sms_webhook)

    _runner = web.AppRunner(app)
    await _runner.setup()
    _site = web.TCPSite(_runner, host, port)
    await _site.start()
    logger.info("Bank SMS Webhook server listening on http://%s:%d", host, port)


async def stop_bank_sms_server() -> None:
    global _runner, _site
    if _site:
        await _site.stop()
        _site = None
    if _runner:
        await _runner.cleanup()
        _runner = None
    logger.info("Bank SMS Webhook server stopped.")
