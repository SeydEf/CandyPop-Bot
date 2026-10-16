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
    get_renewal_discount_config,
    record_notified_alert,
    record_scheduler_run,
    remove_notified_alerts,
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
    email_alerts: dict[str, set[str]],
    notified_map: dict[tuple[str, str], float],
    bot: Bot,
    semaphore: asyncio.Semaphore,
    renewal_discount_enabled: bool = False,
    renewal_discount_percent: int = 0,
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

        client_alerts = email_alerts.setdefault(email, set())

        stale_alerts: list[str] = []

        if not is_expired and "expired_notice" in client_alerts:
            stale_alerts.append("expired_notice")

        if expiry_time < 0:
            for alert_type in list(client_alerts):
                if alert_type.startswith("expiring_day_"):
                    stale_alerts.append(alert_type)
        elif expiry_time > 0 and not is_time_expired:
            rem_ms = expiry_time - now_ms
            rem_days = int(rem_ms / (86400 * 1000))
            if rem_days >= 0:
                for alert_type in list(client_alerts):
                    if alert_type.startswith("expiring_day_"):
                        try:
                            day_val = int(alert_type.split("_")[2])
                            if rem_days > day_val:
                                stale_alerts.append(alert_type)
                        except (IndexError, ValueError):
                            pass

        if total == 0:
            for alert_type in list(client_alerts):
                if alert_type.startswith("low_gb_"):
                    stale_alerts.append(alert_type)
        elif total > 0 and not is_volume_expired:
            rem_bytes = max(0, total - used)
            rem_gb = rem_bytes / (1024**3)
            for alert_type in list(client_alerts):
                if alert_type.startswith("low_gb_"):
                    try:
                        gb_val = int(alert_type.split("_")[2])
                        if rem_gb > gb_val:
                            stale_alerts.append(alert_type)
                    except (IndexError, ValueError):
                        pass

        if stale_alerts:
            await remove_notified_alerts(email, stale_alerts)
            for sa in stale_alerts:
                client_alerts.discard(sa)
                notified_map.pop((email, sa), None)
            logger.info("Cleared stale alerts for %s: %s", email, stale_alerts)

        if is_test_sub and is_expired:
            text = (
                f"⌛️ <b>اشتراک تست رایگان شما به پایان رسید!</b>\n\n"
                f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n\n"
                f"امیدواریم از کیفیت و سرعت سرویس رضایت داشته باشید. "
                f"برای ادامه استفاده، می‌توانید همین حالا از بخش «🛒 خرید اشتراک» سرویس اختصاصی خود را تهیه کنید."
            )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🛒 خرید اشتراک",
                            callback_data="buy_start",
                        ),
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
            except Exception as e:
                logger.warning(
                    "Failed to send test sub expired notice to %s: %s", email, e
                )

            try:
                logger.info("Auto-deleting expired test client %s", email)
                await xui_api.delete_client(email)
                await clear_notified_alerts(email)
                client_alerts.clear()
            except Exception as e:
                logger.error("Failed to delete expired test client %s: %s", email, e)

            return

        btn_renew_text = (
            f"🔄 تمدید اشتراک ({to_persian_digits(renewal_discount_percent)}٪ تخفیف)"
            if (renewal_discount_enabled and renewal_discount_percent > 0)
            else "🔄 تمدید اشتراک"
        )

        if is_expired:
            from db.models import get_reserved_renewal
            from services.renewal_service import activate_reserved_renewal

            res_rec = await get_reserved_renewal(email)
            if res_rec:
                logger.info(
                    "Found reserved renewal for expired client %s, auto-activating...",
                    email,
                )
                act_res = await activate_reserved_renewal(email, force=True, bot=bot)
                if act_res.get("status") == "activated":
                    logger.info("Successfully activated reserved renewal for %s", email)
                    return

            if expired_notice_enabled and "expired_notice" not in client_alerts:
                reason = (
                    "اتمام کامل حجم ترافیک"
                    if is_volume_expired
                    else "پایان مهلت زمانی اعتبار"
                )
                discount_text = (
                    f"\n\n🎁 <b>تخفیف ویژه تمدید:</b> هم‌اکنون می‌توانید با <b>{to_persian_digits(renewal_discount_percent)}٪ تخفیف ویژه</b>، اشتراک خود را تمدید کرده و مجدداً متصل شوید!"
                    if (renewal_discount_enabled and renewal_discount_percent > 0)
                    else ""
                )
                text = (
                    f"⛔️ <b>اشتراک شما غیرفعال و منقضی گردید!</b>\n\n"
                    f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                    f"📌 <b>علت انقضا:</b> {reason}\n\n"
                    f"اتصال سرویس شما قطع شده است. جهت اتصال مجدد و تمدید فوری اشتراک روی دکمه زیر کلیک کنید:"
                    f"{discount_text}"
                )
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=btn_renew_text,
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
                    client_alerts.add("expired_notice")
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
                    and "auto_deleted" not in client_alerts
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
                        client_alerts.clear()

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

                if alert_key not in client_alerts:
                    rem_gb_str = format_size_gb(rem_gb)
                    step_title = (
                        f"کمتر از {to_persian_digits(step_gb)} گیگ"
                        if step_gb > 0
                        else "کمتر از ۱ گیگ"
                    )
                    discount_text = (
                        f"\n\n🎁 <b>پیشنهاد ویژه:</b> تمدید این اشتراک هم‌اکنون با <b>{to_persian_digits(renewal_discount_percent)}٪ تخفیف</b> محاسبه خواهد شد!"
                        if (renewal_discount_enabled and renewal_discount_percent > 0)
                        else ""
                    )
                    text = (
                        f"⚠️ <b>هشدار اتمام حجم ترافیک ({step_title})</b>\n\n"
                        f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                        f"📊 <b>حجم باقی‌مانده:</b> <b>{rem_gb_str}</b>\n\n"
                        f"ترافیک اشتراک شما روبه‌اتمام است. برای جلوگیری از قطعی سرویس، می‌توانید همین حالا آن را تمدید بفرمایید."
                        f"{discount_text}"
                    )
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=btn_renew_text,
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
                        client_alerts.add(alert_key)
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

                if alert_key not in client_alerts:
                    rem_days_str = (
                        f"{to_persian_digits(rem_days)} روز"
                        if rem_days > 0
                        else "کمتر از ۲۴ ساعت"
                    )
                    discount_text = (
                        f"\n\n🎁 <b>هدیه ویژه تمدید:</b> تمدید اشتراک شما هم‌اکنون شامل <b>{to_persian_digits(renewal_discount_percent)}٪ تخفیف اختصاصی</b> است. پیش از پایان مهلت از این تخفیف استفاده کنید!"
                        if (renewal_discount_enabled and renewal_discount_percent > 0)
                        else ""
                    )
                    text = (
                        f"⏳ <b>یادآور روزانه اتمام زمان اشتراک</b>\n\n"
                        f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                        f"⏱ <b>زمان باقی‌مانده:</b> <b>{rem_days_str}</b>\n\n"
                        f"مدت اعتبار اشتراک شما به زودی به پایان می‌رسد. جهت تمدید سرویس روی دکمه زیر کلیک نمایید."
                        f"{discount_text}"
                    )
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=btn_renew_text,
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
                        client_alerts.add(alert_key)
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

        renewal_discount_cfg = await get_renewal_discount_config()
        renewal_discount_enabled = bool(renewal_discount_cfg.get("enabled", False))
        renewal_discount_percent = int(renewal_discount_cfg.get("percent", 0))

        clients = await xui_api.list_clients()
        if not clients:
            return

        now_ms = int(time.time() * 1000)

        notified_map = await get_all_notified_alerts_map()
        email_alerts: dict[str, set[str]] = {}
        for em, alert_type in notified_map.keys():
            email_alerts.setdefault(em, set()).add(alert_type)

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
                email_alerts=email_alerts,
                notified_map=notified_map,
                bot=bot,
                semaphore=semaphore,
                renewal_discount_enabled=renewal_discount_enabled,
                renewal_discount_percent=renewal_discount_percent,
            )
            for c in clients
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        await record_scheduler_run("alert_scheduler")

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
