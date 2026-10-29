from __future__ import annotations

import logging
import time
from typing import Any

from aiogram import Bot

from db.models import (
    clear_notified_alerts,
    create_reserved_renewal,
    delete_reserved_renewal,
    get_reserve_renewal_config,
    get_reserved_renewal,
    get_start_first_use_config,
    get_sub_final_attachment,
)
from services import xui_api
from utils.formatting import to_persian_digits
from utils.helpers import _build_sub_link

logger = logging.getLogger(__name__)


def is_client_expired(client: dict[str, Any]) -> bool:
    total = client.get("totalGB", 0) or 0
    used = client.get("usedTraffic", 0) or 0
    expiry_time = client.get("expiryTime", 0) or 0
    now_ms = int(time.time() * 1000)

    is_volume_expired = total > 0 and used >= total
    is_time_expired = expiry_time > 0 and now_ms >= expiry_time
    return is_volume_expired or is_time_expired


async def activate_fresh_plan(
    email: str,
    duration_days: int,
    data_gb: int,
    users_count: int = 1,
    client: dict[str, Any] | None = None,
    bot: Bot | None = None,
    tg_id: int | None = None,
) -> dict[str, Any]:
    if not client:
        client = await xui_api.get_client(email)
    if not client:
        raise RuntimeError(f"Client {email} not found")

    now_ms = int(time.time() * 1000)
    start_first_use = await get_start_first_use_config()

    if start_first_use and duration_days > 0:
        new_expiry_ms = -int(duration_days * 86400 * 1000)
    elif duration_days > 0:
        new_expiry_ms = now_ms + int(duration_days * 86400 * 1000)
    else:
        new_expiry_ms = 0

    try:
        await xui_api.reset_client_traffic(email)
    except Exception as e:
        logger.warning("Failed to call reset_client_traffic for %s: %s", email, e)

    client["expiryTime"] = new_expiry_ms
    client["totalGB"] = data_gb * 1024 * 1024 * 1024
    client["up"] = 0
    client["down"] = 0
    if users_count:
        client["limitIp"] = users_count
    client["enable"] = True

    res = await xui_api.update_client(email, client)

    try:
        await clear_notified_alerts(email)
    except Exception as e:
        logger.warning("Failed to clear notified alerts for %s: %s", email, e)

    return res


class RenewalPurchaseResult(dict):
    def __init__(self, ok: bool, msg: str, is_reserved: bool):
        status = "reserved" if is_reserved else "active"
        super().__init__(status=status, success=ok, msg=msg, is_reserved=is_reserved)

    def __iter__(self):
        return iter((self["success"], self["msg"], self["is_reserved"]))


class RenewalActivationResult(dict):
    def __init__(self, ok: bool, status: str, msg: str):
        super().__init__(status=status, success=ok, msg=msg)

    def __bool__(self):
        return bool(self["success"])


async def process_renewal_purchase(
    email: str,
    tg_id: int,
    duration_days: int,
    data_gb: int,
    users_count: int = 1,
    invoice_id: str | None = None,
    bot: Bot | None = None,
) -> RenewalPurchaseResult:
    cfg = await get_reserve_renewal_config()
    reserve_enabled = cfg.get("enabled", True)

    if not reserve_enabled:
        await xui_api.renew_client(
            email=email,
            duration_days=duration_days,
            data_gb=data_gb,
            users_count=users_count,
        )
        msg = "اشتراک شما با موفقیت تمدید شد و به حجم و زمان فعلی اضافه گردید."
        return RenewalPurchaseResult(True, msg, False)

    client = await xui_api.get_client(email)
    if not client:
        await xui_api.renew_client(
            email=email,
            duration_days=duration_days,
            data_gb=data_gb,
            users_count=users_count,
        )
        return RenewalPurchaseResult(True, "اشتراک شما با موفقیت تمدید شد.", False)

    if is_client_expired(client):
        await activate_fresh_plan(
            email=email,
            duration_days=duration_days,
            data_gb=data_gb,
            users_count=users_count,
            client=client,
            bot=bot,
            tg_id=tg_id,
        )
        msg = "اشتراک منقضی‌شده شما با موفقیت تمدید و فعال گردید."
        return RenewalPurchaseResult(True, msg, False)

    await create_reserved_renewal(
        email=email,
        tg_id=tg_id,
        duration_days=duration_days,
        data_gb=data_gb,
        users_count=users_count,
        invoice_id=invoice_id,
    )
    msg = (
        "بسته تمدید با موفقیت برای شما رزرو گردید. "
        "به محض اتمام حجم یا زمان اشتراک فعلی، این بسته به صورت خودکار برای سرویس شما فعال خواهد شد."
    )

    return RenewalPurchaseResult(True, msg, True)


async def activate_reserved_renewal(
    email: str,
    force: bool = False,
    bot: Bot | None = None,
) -> RenewalActivationResult:
    reserved = await get_reserved_renewal(email)
    if not reserved:
        return RenewalActivationResult(False, "no_reserved", "بسته رزروی یافت نشد.")

    client = await xui_api.get_client(email)
    if not client:
        return RenewalActivationResult(
            False, "not_found", "اشتراک در پنل سرور یافت نشد."
        )

    if not force and not is_client_expired(client):
        return RenewalActivationResult(
            False, "not_expired", "سرویس هنوز فعال است و منقضی نشده است."
        )

    cfg = await get_reserve_renewal_config()
    now_ms = int(time.time() * 1000)

    duration_days = int(reserved.get("duration_days", 30))
    data_gb = int(reserved.get("data_gb", 10))
    users_count = int(reserved.get("users_count", 1) or 1)
    tg_id = int(reserved.get("tg_id", 0))

    extra_days = 0
    if cfg.get("rollover_days", False):
        curr_expiry = client.get("expiryTime", 0) or 0
        if curr_expiry > now_ms:
            extra_days = int((curr_expiry - now_ms) // (86400 * 1000))

    total_duration_days = duration_days + extra_days
    start_first_use = await get_start_first_use_config()

    if start_first_use and total_duration_days > 0:
        new_expiry_ms = -int(total_duration_days * 86400 * 1000)
    elif total_duration_days > 0:
        new_expiry_ms = now_ms + int(total_duration_days * 86400 * 1000)
    else:
        new_expiry_ms = 0

    try:
        await xui_api.reset_client_traffic(email)
    except Exception as e:
        logger.warning("Failed to call reset_client_traffic for %s: %s", email, e)

    client["expiryTime"] = new_expiry_ms
    client["totalGB"] = data_gb * 1024 * 1024 * 1024
    client["up"] = 0
    client["down"] = 0
    client["limitIp"] = users_count
    client["enable"] = True

    await xui_api.update_client(email, client)
    await clear_notified_alerts(email)
    await delete_reserved_renewal(email)

    sub_id = client.get("subId", "")
    sub_link = _build_sub_link(sub_id)

    logger.info(
        "Activated reserved renewal for client %s: %d GB, %d days",
        email,
        data_gb,
        total_duration_days,
    )

    if bot and tg_id > 0:
        try:
            from services.pricing import format_volume_label

            vol_label = await format_volume_label(data_gb)
            msg = (
                f"🎉 <b>بسته تمدید شما با موفقیت فعال شد!</b>\n\n"
                f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                f"⏱ <b>مدت اعتبار جدید:</b> {to_persian_digits(total_duration_days)} روز\n"
                f"👥 <b>ظرفیت کاربر جدید:</b> {to_persian_digits(users_count)} کاربر\n"
                f"📊 <b>حجم ترافیک جدید:</b> {vol_label}\n"
                f"🔗 <b>لینک هوشمند اشتراک:</b>\n<code>{sub_link}</code>\n\n"
                f"🚀 <i>ترافیک و زمان جدید به سرویس شما افزوده شد. نیازی به تغییر کانفیگ‌ها نیست و اتصال دستگاه‌ها برقرار خواهد ماند.</i>"
            )

            final_attachment = await get_sub_final_attachment()
            if final_attachment:
                msg += final_attachment
            await bot.send_message(chat_id=tg_id, text=msg, parse_mode="HTML")
        except Exception as e:
            logger.warning(
                "Failed to send renewal activation notification to user %d: %s",
                tg_id,
                e,
            )

    return RenewalActivationResult(True, "activated", "بسته رزرو با موفقیت فعال شد.")
