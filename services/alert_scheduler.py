from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import (
    get_alert_config,
    get_db,
    has_notified_alert,
    record_notified_alert,
)
from services import xui_api
from utils.formatting import format_size_gb, to_persian_digits

logger = logging.getLogger(__name__)

ALERT_POLL_INTERVAL_SECONDS = 1800


async def _resolve_tg_id_for_client(client: dict) -> int:
    tg_id = client.get("tgId")
    if tg_id and isinstance(tg_id, int) and tg_id > 0:
        return tg_id

    try:
        tg_id_int = int(tg_id)
        if tg_id_int > 0:
            return tg_id_int
    except (TypeError, ValueError):
        pass

    email = client.get("email", "")
    if not email:
        return 0

    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT tg_id FROM invoices WHERE target_email = ? ORDER BY created_at DESC LIMIT 1",
        (email,),
    )
    if row:
        return row["tg_id"]

    return 0


async def check_and_send_alerts(bot: Bot) -> None:
    try:
        config = await get_alert_config()
        min_gb = float(config["min_gb"])
        min_days = int(config["min_days"])
        auto_delete_days = int(config["auto_delete_days"])

        clients = await xui_api.list_clients()
        if not clients:
            return

        now_ms = int(time.time() * 1000)

        for client in clients:
            email = client.get("email", "")
            enable = client.get("enable", True)

            if not email or not enable:
                continue

            tg_id = await _resolve_tg_id_for_client(client)
            if not tg_id:
                continue

            total = client.get("total", 0)
            up = client.get("up", 0)
            down = client.get("down", 0)
            used = up + down

            if total > 0:
                rem_bytes = max(0, total - used)
                rem_gb = rem_bytes / (1024**3)

                if 0 <= rem_gb <= min_gb:
                    if not await has_notified_alert(email, "low_gb"):
                        try:
                            rem_gb_str = format_size_gb(rem_gb)
                            text = (
                                f"⚠️ <b>هشدار اتمام حجم ترافیک اشتراک</b>\n\n"
                                f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                                f"📊 <b>حجم باقی‌مانده:</b> <b>{rem_gb_str}</b>\n\n"
                                f"ترافیک اشتراک شما روبه‌اتمام است. برای جلوگیری از قطعی سرویس، می‌توانید همین حالا آن را تمدید بفرمایید."
                            )
                            keyboard = InlineKeyboardMarkup(
                                inline_keyboard=[
                                    [
                                        InlineKeyboardButton(
                                            text="🔄 تمدید اشتراک",
                                            callback_data=f"sub_renew_{email}",
                                        )
                                    ]
                                ]
                            )
                            await bot.send_message(
                                chat_id=tg_id,
                                text=text,
                                reply_markup=keyboard,
                                parse_mode="HTML",
                            )
                            await record_notified_alert(email, "low_gb")
                            logger.info("Sent low_gb alert to %s (%s)", email, tg_id)
                        except (TelegramForbiddenError, TelegramBadRequest):
                            pass
                        except Exception as e:
                            logger.warning(
                                "Failed to send low_gb alert to %s: %s", email, e
                            )

            expiry_time = client.get("expiryTime", 0)
            if expiry_time > 0:
                rem_ms = expiry_time - now_ms
                rem_days = int(rem_ms / (86400 * 1000))

                if 0 <= rem_days <= min_days:
                    if not await has_notified_alert(email, "expiring_days"):
                        try:
                            rem_days_str = (
                                f"{to_persian_digits(rem_days)} روز"
                                if rem_days > 0
                                else "کمتر از ۲۴ ساعت"
                            )
                            text = (
                                f"⏳ <b>هشدار اتمام زمان اشتراک</b>\n\n"
                                f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                                f"⏱ <b>زمان باقی‌مانده:</b> <b>{rem_days_str}</b>\n\n"
                                f"مدت اعتبار اشتراک شما به زودی به پایان می‌رسد. جهت تمدید سرویس روی دکمه زیر کلیک نمایید."
                            )
                            keyboard = InlineKeyboardMarkup(
                                inline_keyboard=[
                                    [
                                        InlineKeyboardButton(
                                            text="🔄 تمدید اشتراک",
                                            callback_data=f"sub_renew_{email}",
                                        )
                                    ]
                                ]
                            )
                            await bot.send_message(
                                chat_id=tg_id,
                                text=text,
                                reply_markup=keyboard,
                                parse_mode="HTML",
                            )
                            await record_notified_alert(email, "expiring_days")
                            logger.info(
                                "Sent expiring_days alert to %s (%s)", email, tg_id
                            )
                        except (TelegramForbiddenError, TelegramBadRequest):
                            pass
                        except Exception as e:
                            logger.warning(
                                "Failed to send expiring_days alert to %s: %s", email, e
                            )

            if auto_delete_days > 0 and expiry_time > 0:
                expired_ms = now_ms - expiry_time
                if expired_ms > 0:
                    expired_days = expired_ms / (86400 * 1000)
                    if expired_days >= auto_delete_days:
                        try:
                            logger.info(
                                "Auto-deleting expired client %s (expired %.1f days ago, threshold %d days)",
                                email,
                                expired_days,
                                auto_delete_days,
                            )
                            await xui_api.delete_client(email)
                            from db.models import clear_notified_alerts

                            await clear_notified_alerts(email)

                            if tg_id:
                                try:
                                    del_text = (
                                        f"🗑 <b>اطلاعیه حذف اشتراک منقضی‌شده</b>\n\n"
                                        f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n\n"
                                        f"اشتراک فوق به دلیل گذشت بیش از {to_persian_digits(auto_delete_days)} روز از تاریخ انقضا، از سرور حذف گردید."
                                    )
                                    await bot.send_message(
                                        chat_id=tg_id, text=del_text, parse_mode="HTML"
                                    )
                                except Exception:
                                    pass
                        except Exception as e:
                            logger.error(
                                "Failed to auto-delete expired client %s: %s", email, e
                            )

    except Exception as e:
        logger.error("Error in alert scheduler check: %s", e)


async def start_alert_scheduler(bot: Bot) -> None:
    logger.info("Starting automated subscription alert scheduler...")
    await asyncio.sleep(10)

    while True:
        try:
            await check_and_send_alerts(bot)
        except Exception as e:
            logger.error("Unexpected error in alert scheduler loop: %s", e)

        await asyncio.sleep(ALERT_POLL_INTERVAL_SECONDS)
