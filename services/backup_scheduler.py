from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot
from aiogram.types import FSInputFile

from config import DB_PATH
from db.models import (
    get_admins_with_permission,
    get_backup_config,
    get_database_stats,
    record_backup_event,
    set_backup_config,
)
from utils.formatting import format_datetime, to_persian_digits

logger = logging.getLogger(__name__)


def _create_backup_archive_sync(temp_dir: str) -> tuple[str, int]:
    db_filename = os.path.basename(DB_PATH) or "candypop.db"
    base_name = os.path.splitext(db_filename)[0]

    backup_db_path = os.path.join(temp_dir, f"{base_name}.db")
    dump_sql_path = os.path.join(temp_dir, f"{base_name}_dump.sql")

    src_con = sqlite3.connect(DB_PATH)
    dest_con = sqlite3.connect(backup_db_path)
    try:
        src_con.backup(dest_con)
    finally:
        src_con.close()

    try:
        with open(dump_sql_path, "w", encoding="utf-8") as f:
            for line in dest_con.iterdump():
                f.write(f"{line}\n")
    finally:
        dest_con.close()

    tz_iran = timezone(timedelta(hours=3, minutes=30))
    now_iran = datetime.now(tz_iran)
    date_stamp = now_iran.strftime("%Y-%m-%d_%H-%M")
    zip_filename = f"{base_name}_backup_{date_stamp}.zip"
    zip_path = os.path.join(temp_dir, zip_filename)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(backup_db_path, arcname=f"{base_name}.db")
        zf.write(dump_sql_path, arcname=f"{base_name}_dump.sql")

    size_bytes = os.path.getsize(zip_path)
    return zip_path, size_bytes


def _format_size_readable(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        val = size_bytes / (1024 * 1024)
        return f"{to_persian_digits(f'{val:.2f}')} مگابایت"
    elif size_bytes >= 1024:
        val = size_bytes / 1024
        return f"{to_persian_digits(f'{val:.1f}')} کیلوبایت"
    return f"{to_persian_digits(size_bytes)} بایت"


def format_backup_caption(
    stats: dict[str, Any],
    size_bytes: int,
    trigger: str = "auto",
    admin_name: str = "",
) -> str:
    tz_iran = timezone(timedelta(hours=3, minutes=30))
    now_iran = datetime.now(tz_iran)
    formatted_time = format_datetime(now_iran, with_seconds=True)

    if trigger == "auto":
        trigger_text = "🤖 خودکار (زمان‌بندی‌شده)"
    else:
        admin_info = f" (توسط: {admin_name})" if admin_name else ""
        trigger_text = f"👤 دستی{admin_info}"

    users_cnt = to_persian_digits(stats.get("users_count", 0))
    banned_cnt = to_persian_digits(stats.get("banned_users_count", 0))
    invoices_cnt = to_persian_digits(stats.get("invoices_count", 0))
    approved_cnt = to_persian_digits(stats.get("approved_invoices_count", 0))
    pending_cnt = to_persian_digits(stats.get("pending_invoices_count", 0))
    rejected_cnt = to_persian_digits(stats.get("rejected_invoices_count", 0))
    balance_cnt = to_persian_digits(f"{stats.get('wallets_total_balance', 0):,}")
    admins_cnt = to_persian_digits(stats.get("admins_count", 0))
    discounts_cnt = to_persian_digits(stats.get("active_discounts_count", 0))
    renewals_cnt = to_persian_digits(stats.get("reserved_renewals_count", 0))
    size_text = _format_size_readable(size_bytes)

    caption = (
        "📦 <b>نسخه پشتیبان پایگاه داده CandyPop</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 <b>زمان تهیه:</b> {formatted_time}\n"
        f"⚙️ <b>نوع پشتیبان‌گیری:</b> {trigger_text}\n"
        f"💾 <b>حجم فایل فشرده:</b> {size_text}\n\n"
        "📊 <b>وضعیت کلی سیستم و پایگاه‌داده:</b>\n"
        f"• 👥 کل کاربران: <b>{users_cnt}</b> نفر (مسدود: {banned_cnt})\n"
        f"• 🧾 کل فاکتورها: <b>{invoices_cnt}</b> (موفق: <b>{approved_cnt}</b> | در انتظار: <b>{pending_cnt}</b> | ردشده: <b>{rejected_cnt}</b>)\n"
        f"• 💰 مجموع موجودی کیف‌پول‌ها: <b>{balance_cnt} تومان</b>\n"
        f"• 👑 مدیران ربات: <b>{admins_cnt}</b> نفر\n"
        f"• 🏷 کدهای تخفیف فعال: <b>{discounts_cnt}</b> عدد\n"
        f"• 📦 تمدیدهای رزروشده: <b>{renewals_cnt}</b> مورد\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>این فایل فشرده (ZIP) شامل فایل خام دیتابیس (SQLite .db) و اسکریپت بازسازی کامل (SQL Dump) است.</i>"
    )
    return caption


async def create_and_send_backup(
    bot: Bot,
    trigger: str = "auto",
    admin_id: int | None = None,
    admin_name: str = "",
) -> tuple[bool, str]:
    if not os.path.exists(DB_PATH):
        err = f"Database file not found at {DB_PATH}"
        logger.error(err)
        return False, err

    temp_dir = tempfile.mkdtemp(prefix="candypop_backup_")
    try:
        loop = asyncio.get_running_loop()
        zip_path, size_bytes = await loop.run_in_executor(
            None, _create_backup_archive_sync, temp_dir
        )

        stats = await get_database_stats()
        caption = format_backup_caption(
            stats=stats,
            size_bytes=size_bytes,
            trigger=trigger,
            admin_name=admin_name,
        )

        doc = FSInputFile(zip_path)

        if admin_id:
            recipients = [admin_id]
        else:
            recipients = await get_admins_with_permission("backup")

        if not recipients:
            logger.warning("No recipients configured for database backup.")
            return False, "هیچ دریافت‌کننده‌ای یافت نشد."

        sent_count = 0
        for target_chat_id in recipients:
            try:
                await bot.send_document(
                    chat_id=target_chat_id,
                    document=doc,
                    caption=caption,
                    parse_mode="HTML",
                )
                sent_count += 1
            except Exception as e:
                logger.error("Failed to send backup to chat %d: %s", target_chat_id, e)

        await record_backup_event(
            trigger=trigger,
            size_bytes=size_bytes,
            status="success" if sent_count > 0 else "failed",
            by_admin=admin_id,
            by_admin_name=admin_name,
        )
        return sent_count > 0, ""
    except Exception as e:
        logger.error("Error creating or sending backup: %s", e, exc_info=True)
        await record_backup_event(
            trigger=trigger,
            size_bytes=0,
            status="failed",
            by_admin=admin_id,
            by_admin_name=admin_name,
            error_msg=str(e),
        )
        return False, str(e)
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


async def start_backup_scheduler(bot: Bot) -> None:
    logger.info("Starting automated database backup scheduler...")
    await asyncio.sleep(15)

    while True:
        try:
            config = await get_backup_config()
            if config.get("auto_enabled", True):
                tz_iran = timezone(timedelta(hours=3, minutes=30))
                now_iran = datetime.now(tz_iran)

                schedule_time = config.get("schedule_time", "00:00")
                try:
                    parts = schedule_time.split(":")
                    target_h, target_m = int(parts[0]), int(parts[1])
                except Exception:
                    target_h, target_m = 0, 0

                freq = config.get("frequency", "24h")
                if freq == "6h":
                    valid_hours = [(target_h + i * 6) % 24 for i in range(4)]
                elif freq == "12h":
                    valid_hours = [(target_h + i * 12) % 24 for i in range(2)]
                else:
                    valid_hours = [target_h % 24]

                if now_iran.hour in valid_hours and now_iran.minute == target_m:
                    slot_id = f"{now_iran.strftime('%Y-%m-%d')}_{now_iran.hour:02d}_{target_m:02d}"
                    if slot_id != config.get("last_run_slot"):
                        logger.info(
                            "Triggering scheduled database backup (slot: %s)...",
                            slot_id,
                        )
                        success, err = await create_and_send_backup(bot, trigger="auto")
                        if success:
                            await set_backup_config(last_run_slot=slot_id)
                            logger.info(
                                "Scheduled database backup completed successfully."
                            )
                        else:
                            logger.error("Scheduled database backup failed: %s", err)
        except Exception as e:
            logger.error("Unexpected error in backup scheduler loop: %s", e)

        await asyncio.sleep(30)
