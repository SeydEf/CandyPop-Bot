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
from utils.formatting import format_price, to_persian_digits

logger = logging.getLogger(__name__)

_runner: web.AppRunner | None = None
_site: web.TCPSite | None = None
_bot_instance: Bot | None = None


async def handle_bank_sms_webhook(request: web.Request) -> web.Response:
    bot = _bot_instance
    if not bot:
        return web.json_response(
            {"error": "Server Not Ready", "detail": "ربات هنوز آماده نیست."},
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

    # 1. Authentication
    api_key_header = request.headers.get("X-Api-Key") or request.headers.get(
        "x-api-key"
    )
    secret_key = cfg.get("secret_key", "")
    if not api_key_header or not secrets.compare_digest(api_key_header, secret_key):
        return web.json_response(
            {"error": "Unauthorized", "detail": "کلید احراز هویت نامعتبر است."},
            status=401,
        )

    # 2. Parse & Validate JSON
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

    # 3. Idempotency Check
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

    # 4. Find matching pending card invoice
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

    # 5. Matching invoice found -> approve it!
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
        admin_name="تأیید خودکار پیامک بانکی",
    )

    if not success:
        logger.error("Failed to approve invoice %s via bank SMS: %s", invoice_id, msg)
        return web.json_response(
            {"status": "error", "message": f"خطا در فعال‌سازی سفارش: {msg}"},
            status=500,
        )

    # Update admin notification message if it already existed in review queue
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
                        caption=(
                            f"⚡️ <b>این پرداخت به صورت خودکار توسط پیامک بانکی تأیید شد.</b>\n\n"
                            f"🆔 فاکتور: <code>{invoice_id}</code>\n"
                            f"💰 مبلغ: <code>{format_price(amount_toman)}</code>"
                        ),
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

    # Send telegram notification log to admins
    try:
        sender_disp = html.escape(str(account_number or sender or "نامشخص"))
        time_disp = html.escape(str(timestamp_str or "نامشخص"))
        admin_notice = (
            "⚡️ <b>تأیید خودکار پرداخت کارت به کارت (پیامک بانکی)</b>\n\n"
            f"🧾 <b>شماره فاکتور:</b> <code>{invoice_id}</code>\n"
            f"👤 <b>شناسه کاربر:</b> <code>{tg_id}</code>\n"
            f"💰 <b>مبلغ واریزی:</b> <code>{format_price(amount_toman)}</code>\n"
            f"🏦 <b>اطلاعات فرستنده/حساب:</b> <code>{sender_disp}</code>\n"
            f"⏱ <b>زمان واریز:</b> <code>{to_persian_digits(time_disp)}</code>\n"
            f"🔑 <b>اثرانگشت تراکنش:</b> <code>{fingerprint[:16]}...</code>\n\n"
            "✅ سفارش کاربر به صورت خودکار تایید و تحویل شد."
        )

        admin_ids = [ADMIN_CHAT_ID]
        for adm in await get_all_admins():
            a_id = adm["tg_id"]
            if a_id > 0 and a_id not in admin_ids:
                if await has_admin_permission(a_id, "approve_invoices"):
                    admin_ids.append(a_id)

        for a_id in admin_ids:
            try:
                await bot.send_message(
                    chat_id=a_id,
                    text=admin_notice,
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
            "message": "سفارش با موفقیت تایید و فعال شد.",
        },
        status=200,
    )


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
