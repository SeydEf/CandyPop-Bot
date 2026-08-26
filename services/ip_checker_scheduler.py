from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_CHAT_ID, SUPPORT_LINK
from db.models import (
    get_all_admins,
    get_all_ip_violations_dict,
    get_ip_checker_config,
    has_admin_permission,
    record_ip_violation,
    resolve_client_tg_id,
    set_ip_suspended,
)
from services import xui_api
from utils.formatting import to_persian_digits

logger = logging.getLogger(__name__)

CONCURRENCY_LIMIT = 10


async def _process_client_ip_limit(
    client: dict[str, Any],
    all_client_ips: dict[str, list[str]],
    violations_dict: dict[str, dict[str, Any]],
    bot: Bot,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        email = client.get("email", "")
        enable = client.get("enable", True)
        limit_ip = client.get("limitIp", 0)

        if not email or not enable or limit_ip <= 0:
            return

        rec = violations_dict.get(email)
        if rec and rec.get("suspended"):
            return

        connected_ips = all_client_ips.get(email, [])
        ip_count = len(connected_ips)

        if ip_count > limit_ip:
            strikes, total_incidents = await record_ip_violation(email)
            tg_id = await resolve_client_tg_id(client)

            if strikes < 3:
                if tg_id > 0:
                    try:
                        logger.info(
                            "Sent IP limit warning (Strike %d) to %s (%s)",
                            strikes,
                            email,
                            tg_id,
                        )

                        text = (
                            f"⚠️ <b>هشدار تخلف از سقف اتصال همزمان (دستگاه/IP)</b>\n\n"
                            f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                            f"📡 <b>تعداد اتصالات همزمان فعلی:</b> <b>{to_persian_digits(ip_count)} دستگاه/IP</b>\n"
                            f"⛔️ <b>سقف مجاز:</b> <b>{to_persian_digits(limit_ip)} دستگاه</b>\n"
                            f"🚨 <b>تعداد اخطار ثبت‌شده:</b> <b>{to_persian_digits(strikes)} از ۳ اخطار</b>\n\n"
                            f"تعداد اتصالات همزمان شما بیشتر از سقف مجاز است. لطفاً اتصالات اضافی را قطع کنید. در صورت دریافت ۳ اخطار، سرویس شما مسدود خواهد شد."
                        )

                        await bot.send_message(
                            chat_id=tg_id, text=text, parse_mode="HTML"
                        )
                    except (TelegramForbiddenError, TelegramBadRequest):
                        pass
                    except Exception as e:
                        logger.warning(
                            "Failed to send IP limit warning to %s: %s", email, e
                        )
            else:
                try:
                    logger.info(
                        "3-Strike IP limit exceeded for client %s (%d/%d IPs). Suspending client...",
                        email,
                        ip_count,
                        limit_ip,
                    )
                    client_full = await xui_api.get_client(email)
                    if client_full:
                        update_data = dict(client_full)
                        update_data["enable"] = False
                        await xui_api.update_client(email, update_data)

                    await set_ip_suspended(email, True)

                    if tg_id > 0:
                        try:
                            susp_text = (
                                f"⛔️ <b>مسدودی سرویس به دلیل تخلف از سقف کاربران همزمان</b>\n\n"
                                f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n\n"
                                f"اشتراک شما به دلیل تخلف بیش از حد مجاز (۳ اخطار) در استفاده همزمان دستگاه‌ها مسدود گردید.\n"
                                f"جهت بررسی و رفع مسدودی، لطفاً با پشتیبانی به شناسه {SUPPORT_LINK} در ارتباط باشید."
                            )
                            await bot.send_message(
                                chat_id=tg_id, text=susp_text, parse_mode="HTML"
                            )
                        except Exception:
                            pass

                    user_info = (
                        f"<code>{tg_id}</code>"
                        if tg_id > 0
                        else "بدون شناسه تلگرام (ثبت خارج ربات)"
                    )
                    admin_text = (
                        f"🚨 <b>اطلاعیه مسدودی خودکار سرویس (تخلف IP)</b>\n\n"
                        f"👤 <b>شناسه کاربر:</b> {user_info}\n"
                        f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                        f"📡 <b>تعداد اتصالات همزمان:</b> <b>{to_persian_digits(ip_count)} از {to_persian_digits(limit_ip)} IP</b>\n"
                        f"🚨 <b>کل سوابق مسدودی IP این حساب:</b> <b>{to_persian_digits(total_incidents)} بار</b>\n\n"
                        f"لطفاً جهت تعیین تکلیف سرویس یکی از گزینه‌های زیر را انتخاب نمایید:"
                    )
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="✅ رفع مسدودی و فعال‌سازی",
                                    callback_data=f"admin_ip_reactivate_{email}",
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="🗑 حذف کامل سرویس",
                                    callback_data=f"admin_ip_delete_{email}",
                                )
                            ],
                        ]
                    )

                    admin_ids = [ADMIN_CHAT_ID]
                    for adm in await get_all_admins():
                        a_id = adm["tg_id"]
                        if a_id > 0 and a_id not in admin_ids:
                            if await has_admin_permission(a_id, "alerts"):
                                admin_ids.append(a_id)

                    for a_id in admin_ids:
                        try:
                            await bot.send_message(
                                chat_id=a_id,
                                text=admin_text,
                                reply_markup=keyboard,
                                parse_mode="HTML",
                            )
                        except Exception as e:
                            logger.warning(
                                "Failed to send IP suspension alert to admin %d: %s",
                                a_id,
                                e,
                            )

                except Exception as e:
                    logger.error("Failed to suspend client %s: %s", email, e)


async def check_and_process_ip_limits(bot: Bot) -> None:
    try:
        cfg = await get_ip_checker_config()
        if not cfg.get("enabled", True):
            return

        clients = await xui_api.list_clients()
        if not clients:
            return

        all_client_ips = await xui_api.get_all_client_ips()
        violations_dict = await get_all_ip_violations_dict()

        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        tasks = [
            _process_client_ip_limit(
                client=c,
                all_client_ips=all_client_ips,
                violations_dict=violations_dict,
                bot=bot,
                semaphore=semaphore,
            )
            for c in clients
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    except Exception as e:
        logger.error("Error in IP limit checker scan: %s", e)


async def start_ip_checker_scheduler(bot: Bot) -> None:
    logger.info("Starting automated IP Limit Checker scheduler...")
    await asyncio.sleep(15)

    while True:
        try:
            await check_and_process_ip_limits(bot)
        except Exception as e:
            logger.error("Unexpected error in IP Limit Checker loop: %s", e)

        try:
            cfg = await get_ip_checker_config()
            interval_minutes = int(cfg.get("interval_minutes", 5))
            sleep_seconds = max(60, interval_minutes * 60)
        except Exception:
            sleep_seconds = 300

        await asyncio.sleep(sleep_seconds)
