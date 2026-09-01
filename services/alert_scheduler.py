from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import (
    clear_notified_alerts,
    get_alert_config,
    get_all_notified_alerts_map,
    record_notified_alert,
    resolve_client_tg_id,
)
from services import xui_api
from utils.formatting import format_size_gb, to_persian_digits

logger = logging.getLogger(__name__)

CONCURRENCY_LIMIT = 15


async def _process_single_client(
    client: dict[str, Any],
    now_ms: int,
    min_gb: float,
    min_days: int,
    auto_delete_days: int,
    low_gb_enabled: bool,
    expiring_days_enabled: bool,
    expired_notice_enabled: bool,
    auto_delete_enabled: bool,
    notified_set: set[tuple[str, str]],
    notified_map: dict[tuple[str, str], float],
    bot: Bot,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        email = client.get("email", "")

        if not email:
            return

        is_test_sub = "_test" in email

        tg_id = await resolve_client_tg_id(client)
        if not tg_id:
            return

        total = client.get("totalGB", 0)
        up = client.get("up", 0)
        down = client.get("down", 0)
        used = up + down

        expiry_time = client.get("expiryTime", 0)

        is_volume_expired = total > 0 and used >= total
        is_time_expired = expiry_time > 0 and now_ms >= expiry_time
        is_expired = is_volume_expired or is_time_expired

        if is_test_sub and is_expired:
            text = (
                f"⌛️ <b>اشتراک تست رایگان شما به پایان رسید!</b>\n\n"
                f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n\n"
                f"امیدواریم از کیفیت و سرعت سرویس رضایت داشته باشید. "
                f"برای ادامه استفاده، می‌توانید همین حالا از بخش «🛒 خرید اشتراک» سرویس اختصاصی خود را تهیه کنید."
            )
            
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text=text,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(
                    "Failed to send test sub expired notice to %s: %s", email, e
                )

            try:
                logger.info("Auto-deleting expired test client %s", email)
                await xui_api.delete_client(email)
                await clear_notified_alerts(email)
            except Exception as e:
                logger.error("Failed to delete expired test client %s: %s", email, e)

            return

        if is_expired:
            if expired_notice_enabled and (email, "expired_notice") not in notified_set:
                reason = (
                    "اتمام کامل حجم ترافیک"
                    if is_volume_expired
                    else "پایان مهلت زمانی اعتبار"
                )
                text = (
                    f"⛔️ <b>اشتراک شما غیرفعال و منقضی گردید!</b>\n\n"
                    f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                    f"📌 <b>علت انقضا:</b> {reason}\n\n"
                    f"اتصال سرویس شما قطع شده است. جهت اتصال مجدد و تمدید فوری اشتراک روی دکمه زیر کلیک کنید:"
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
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                    await record_notified_alert(email, "expired_notice")
                    notified_set.add((email, "expired_notice"))
                    notified_map[(email, "expired_notice")] = time.time()
                    logger.info("Sent expired_notice alert to %s (%s)", email, tg_id)
                except (TelegramForbiddenError, TelegramBadRequest):
                    pass
                except Exception as e:
                    logger.warning("Failed to send expired_notice to %s: %s", email, e)

            if auto_delete_enabled and auto_delete_days > 0:
                expired_days = 0.0

                if is_time_expired and expiry_time > 0:
                    time_days = (now_ms - expiry_time) / (86400 * 1000)
                    expired_days = max(expired_days, time_days)

                if is_volume_expired:
                    notified_ts = notified_map.get((email, "expired_notice"))
                    if notified_ts:
                        now_sec = now_ms / 1000
                        vol_days = (now_sec - notified_ts) / 86400
                        expired_days = max(expired_days, vol_days)

                if (
                    expired_days >= auto_delete_days
                    and (email, "auto_deleted") not in notified_set
                ):
                    try:
                        logger.info(
                            "Auto-deleting expired client %s (expired %.1f days ago, threshold %d days)",
                            email,
                            expired_days,
                            auto_delete_days,
                        )
                        await xui_api.delete_client(email)
                        await clear_notified_alerts(email)

                        try:
                            del_text = (
                                f"🗑 <b>اطلاعیه حذف اشتراک منقضی‌شده</b>\n\n"
                                f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n\n"
                                f"اشتراک فوق به دلیل گذشت بیش از {to_persian_digits(auto_delete_days)} روز "
                                f"از تاریخ انقضا، حذف گردید."
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

            return

        if low_gb_enabled and total > 0 and not is_test_sub:
            rem_bytes = max(0, total - used)
            rem_gb = rem_bytes / (1024**3)

            if 0 < rem_gb <= min_gb:
                step_gb = int(math.ceil(rem_gb))
                alert_key = f"low_gb_{step_gb}"

                if (email, alert_key) not in notified_set:
                    rem_gb_str = format_size_gb(rem_gb)
                    step_title = (
                        f"کمتر از {to_persian_digits(step_gb)} گیگ"
                        if step_gb > 0
                        else "کمتر از ۱ گیگ"
                    )
                    text = (
                        f"⚠️ <b>هشدار اتمام حجم ترافیک ({step_title})</b>\n\n"
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
                    try:
                        await bot.send_message(
                            chat_id=tg_id,
                            text=text,
                            reply_markup=keyboard,
                            parse_mode="HTML",
                        )
                        await record_notified_alert(email, alert_key)
                        notified_set.add((email, alert_key))
                        logger.info("Sent %s alert to %s (%s)", alert_key, email, tg_id)
                    except (TelegramForbiddenError, TelegramBadRequest):
                        pass
                    except Exception as e:
                        logger.warning(
                            "Failed to send %s alert to %s: %s", alert_key, email, e
                        )

        if expiring_days_enabled and expiry_time > 0 and not is_test_sub:
            rem_ms = expiry_time - now_ms
            rem_days = int(rem_ms / (86400 * 1000))

            if 0 <= rem_days <= min_days:
                alert_key = f"expiring_day_{rem_days}"

                if (email, alert_key) not in notified_set:
                    rem_days_str = (
                        f"{to_persian_digits(rem_days)} روز"
                        if rem_days > 0
                        else "کمتر از ۲۴ ساعت"
                    )
                    text = (
                        f"⏳ <b>یادآور روزانه اتمام زمان اشتراک</b>\n\n"
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
                    try:
                        await bot.send_message(
                            chat_id=tg_id,
                            text=text,
                            reply_markup=keyboard,
                            parse_mode="HTML",
                        )
                        await record_notified_alert(email, alert_key)
                        notified_set.add((email, alert_key))
                        logger.info("Sent %s alert to %s (%s)", alert_key, email, tg_id)
                    except (TelegramForbiddenError, TelegramBadRequest):
                        pass
                    except Exception as e:
                        logger.warning(
                            "Failed to send %s alert to %s: %s", alert_key, email, e
                        )


async def check_and_send_alerts(bot: Bot) -> None:
    try:
        config = await get_alert_config()
        if not config.get("enabled", True):
            logger.debug("Alert scheduler is disabled in config.")
            return

        min_gb = float(config["min_gb"])
        min_days = int(config["min_days"])
        auto_delete_days = int(config["auto_delete_days"])
        low_gb_enabled = bool(config.get("low_gb_enabled", True))
        expiring_days_enabled = bool(config.get("expiring_days_enabled", True))
        expired_notice_enabled = bool(config.get("expired_notice_enabled", True))
        auto_delete_enabled = bool(config.get("auto_delete_enabled", True))

        clients = await xui_api.list_clients()
        if not clients:
            return

        now_ms = int(time.time() * 1000)

        notified_map = await get_all_notified_alerts_map()
        notified_set = set(notified_map.keys())

        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        tasks = [
            _process_single_client(
                client=c,
                now_ms=now_ms,
                min_gb=min_gb,
                min_days=min_days,
                auto_delete_days=auto_delete_days,
                low_gb_enabled=low_gb_enabled,
                expiring_days_enabled=expiring_days_enabled,
                expired_notice_enabled=expired_notice_enabled,
                auto_delete_enabled=auto_delete_enabled,
                notified_set=notified_set,
                notified_map=notified_map,
                bot=bot,
                semaphore=semaphore,
            )
            for c in clients
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    except Exception as e:
        logger.error("Error in alert scheduler check: %s", e)


async def start_alert_scheduler(bot: Bot) -> None:
    logger.info("Starting automated subscription alert scheduler...")
    await asyncio.sleep(10)

    while True:
        try:
            config = await get_alert_config()
            if config.get("enabled", True):
                await check_and_send_alerts(bot)
            else:
                logger.debug("Alert scheduler cycle skipped (disabled).")
        except Exception as e:
            logger.error("Unexpected error in alert scheduler loop: %s", e)

        try:
            config = await get_alert_config()
            interval_minutes = int(config.get("interval_minutes", 30))
            sleep_seconds = max(60, interval_minutes * 60)
        except Exception:
            sleep_seconds = 1800

        await asyncio.sleep(sleep_seconds)
