from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import (
    get_alert_config,
    has_notified_alert,
    record_notified_alert,
    resolve_client_tg_id,
)
from services import xui_api
from utils.formatting import format_size_gb, to_persian_digits

logger = logging.getLogger(__name__)


async def check_and_send_alerts(bot: Bot) -> None:
    try:
        config = await get_alert_config()
        min_gb = float(config["min_gb"])
        min_days = int(config["min_days"])
        auto_delete_days = int(config["auto_delete_days"])
        low_gb_enabled = bool(config.get("low_gb_enabled", True))
        expiring_days_enabled = bool(config.get("expiring_days_enabled", True))

        clients = await xui_api.list_clients()
        if not clients:
            return

        now_ms = int(time.time() * 1000)

        for client in clients:
            email = client.get("email", "")
            enable = client.get("enable", True)

            if not email or not enable:
                continue

            tg_id = await resolve_client_tg_id(client)
            if not tg_id:
                continue

            total = client.get("total", 0)
            up = client.get("up", 0)
            down = client.get("down", 0)
            used = up + down
            expiry_time = client.get("expiryTime", 0)

            is_test_sub = email.endswith("_test") or "_test" in email

            is_volume_expired = total > 0 and used >= total
            is_time_expired = expiry_time > 0 and now_ms >= expiry_time

            if is_volume_expired or is_time_expired:
                if is_test_sub:
                    try:
                        reason = (
                            "اتمام حجم ترافیک تست"
                            if is_volume_expired
                            else "پایان مهلت زمانی تست"
                        )
                        text = (
                            f"⛔️ <b>اشتراک تست رایگان شما به پایان رسید</b>\n\n"
                            f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                            f"📌 <b>علت انقضا:</b> {reason}\n\n"
                            f"مهلت استفاده از اشتراک تست به پایان رسیده و این سرویس از سرور حذف گردید. در صورت تمایل می‌توانید از بخش «🛒 خرید اشتراک» سرویس جدید تهیه کنید."
                        )
                        await bot.send_message(
                            chat_id=tg_id,
                            text=text,
                            parse_mode="HTML",
                        )
                    except (TelegramForbiddenError, TelegramBadRequest):
                        pass
                    except Exception as e:
                        logger.warning(
                            "Failed to send test sub expired notice to %s: %s", email, e
                        )

                    try:
                        logger.info("Auto-deleting expired test client %s", email)
                        await xui_api.delete_client(email)
                        from db.models import clear_notified_alerts

                        await clear_notified_alerts(email)
                    except Exception as e:
                        logger.error(
                            "Failed to delete expired test client %s: %s", email, e
                        )

                    continue

                if not await has_notified_alert(email, "expired_notice"):
                    try:
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
                        await bot.send_message(
                            chat_id=tg_id,
                            text=text,
                            reply_markup=keyboard,
                            parse_mode="HTML",
                        )
                        await record_notified_alert(email, "expired_notice")
                        logger.info(
                            "Sent expired_notice alert to %s (%s)", email, tg_id
                        )
                    except (TelegramForbiddenError, TelegramBadRequest):
                        pass
                    except Exception as e:
                        logger.warning(
                            "Failed to send expired_notice to %s: %s", email, e
                        )

            if (
                low_gb_enabled
                and total > 0
                and not is_volume_expired
                and not is_test_sub
            ):
                rem_bytes = max(0, total - used)
                rem_gb = rem_bytes / (1024**3)

                if 0 < rem_gb <= min_gb:
                    import math

                    step_gb = int(math.floor(rem_gb))
                    alert_key = f"low_gb_{step_gb}"

                    if not await has_notified_alert(email, alert_key):
                        try:
                            rem_gb_str = format_size_gb(rem_gb)
                            step_title = (
                                f"کمتر از {to_persian_digits(step_gb + 1)} گیگ"
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
                            await bot.send_message(
                                chat_id=tg_id,
                                text=text,
                                reply_markup=keyboard,
                                parse_mode="HTML",
                            )
                            await record_notified_alert(email, alert_key)
                            logger.info(
                                "Sent %s alert to %s (%s)", alert_key, email, tg_id
                            )
                        except (TelegramForbiddenError, TelegramBadRequest):
                            pass
                        except Exception as e:
                            logger.warning(
                                "Failed to send %s alert to %s: %s",
                                alert_key,
                                email,
                                e,
                            )

            if (
                expiring_days_enabled
                and expiry_time > 0
                and not is_time_expired
                and not is_test_sub
            ):
                rem_ms = expiry_time - now_ms
                rem_days = int(rem_ms / (86400 * 1000))

                if 0 <= rem_days <= min_days:
                    alert_key = f"expiring_day_{rem_days}"
                    if not await has_notified_alert(email, alert_key):
                        try:
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
                            await bot.send_message(
                                chat_id=tg_id,
                                text=text,
                                reply_markup=keyboard,
                                parse_mode="HTML",
                            )
                            await record_notified_alert(email, alert_key)
                            logger.info(
                                "Sent %s alert to %s (%s)", alert_key, email, tg_id
                            )
                        except (TelegramForbiddenError, TelegramBadRequest):
                            pass
                        except Exception as e:
                            logger.warning(
                                "Failed to send %s alert to %s: %s",
                                alert_key,
                                email,
                                e,
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
                                        f"اشتراک فوق به دلیل گذشت بیش از {to_persian_digits(auto_delete_days)} روز از تاریخ انقضا، حذف گردید."
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

        try:
            config = await get_alert_config()
            interval_minutes = int(config.get("interval_minutes", 30))
            sleep_seconds = max(60, interval_minutes * 60)
        except Exception:
            sleep_seconds = 1800

        await asyncio.sleep(sleep_seconds)
