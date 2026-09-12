from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import ADMIN_CHAT_ID, XUI_BASE_URL
from db.models import (
    get_all_admins,
    get_inbound_monitor_config,
    has_admin_permission,
)
from services import xui_api
from utils.formatting import format_datetime, to_persian_digits

logger = logging.getLogger(__name__)

_inbound_status_cache: dict[int, dict[str, Any]] = {}


def get_default_target_host() -> str:
    if not XUI_BASE_URL:
        return "127.0.0.1"
    try:
        parsed = urlparse(XUI_BASE_URL)
        host = parsed.hostname
        if host:
            return host
    except Exception as e:
        logger.warning("Failed to parse hostname from XUI_BASE_URL: %s", e)
    return "127.0.0.1"


async def check_tcp_port(
    host: str, port: int, timeout: float = 5.0
) -> tuple[bool, float, str]:
    t0 = time.perf_counter()
    try:
        conn = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        return True, latency_ms, "OK"
    except asyncio.TimeoutError:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return False, latency_ms, "Timeout"
    except OSError as e:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        err_msg = e.strerror or str(e)
        return False, latency_ms, err_msg
    except Exception as e:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return False, latency_ms, str(e)


async def check_inbounds_health(
    target_ids: list[int] | None = None,
    target_host: str | None = None,
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    cfg = await get_inbound_monitor_config()
    default_host = get_default_target_host()

    req_timeout = timeout if timeout is not None else cfg.get("timeout_seconds", 5.0)

    try:
        all_inbounds = await xui_api.list_inbound_options()
    except Exception as e:
        logger.error("Failed to list inbounds from X-UI API: %s", e)
        return []

    if not all_inbounds:
        return []

    filter_ids = (
        target_ids if target_ids is not None else cfg.get("monitored_inbound_ids", [])
    )
    filter_set = set(filter_ids) if filter_ids else None

    inbounds_to_check: list[dict[str, Any]] = []
    for ib in all_inbounds:
        ib_id = ib.get("id")
        if filter_set is not None and ib_id not in filter_set:
            continue
        inbounds_to_check.append(ib)

    results: list[dict[str, Any]] = []

    async def _test_one(ib: dict[str, Any]) -> dict[str, Any]:
        ib_id = int(ib.get("id", 0))
        remark = ib.get("remark") or ib.get("tag") or f"Inbound #{ib_id}"
        protocol = str(ib.get("protocol", "")).upper()
        port = int(ib.get("port", 0))
        is_enabled = bool(ib.get("enable", True))

        share_addr = (ib.get("shareAddr") or "").strip()
        node_addr = (ib.get("nodeAddress") or "").strip()
        custom_cfg_host = (cfg.get("target_host") or "").strip()

        inbound_host = (
            (target_host or "").strip()
            or share_addr
            or node_addr
            or custom_cfg_host
            or default_host
        )

        if not is_enabled:
            return {
                "id": ib_id,
                "remark": remark,
                "protocol": protocol,
                "port": port,
                "enable": False,
                "is_ok": False,
                "latency_ms": 0.0,
                "error": "اینباند در پنل غیرفعال است",
                "host": inbound_host,
            }

        if port <= 0:
            return {
                "id": ib_id,
                "remark": remark,
                "protocol": protocol,
                "port": port,
                "enable": True,
                "is_ok": False,
                "latency_ms": 0.0,
                "error": "پورت نامعتبر است",
                "host": inbound_host,
            }

        is_ok, latency, err = await check_tcp_port(
            inbound_host, port, timeout=req_timeout
        )
        return {
            "id": ib_id,
            "remark": remark,
            "protocol": protocol,
            "port": port,
            "enable": True,
            "is_ok": is_ok,
            "latency_ms": latency,
            "error": err,
            "host": inbound_host,
        }

    tasks = [_test_one(ib) for ib in inbounds_to_check]
    if tasks:
        results = await asyncio.gather(*tasks)

    return sorted(results, key=lambda x: x["id"])


async def get_authorized_admin_ids() -> list[int]:
    admin_ids: list[int] = []
    if ADMIN_CHAT_ID > 0:
        admin_ids.append(ADMIN_CHAT_ID)

    try:
        admins = await get_all_admins()
        for adm in admins:
            a_id = adm.get("tg_id", 0)
            if a_id > 0 and a_id not in admin_ids:
                if await has_admin_permission(a_id, "inbound_alerts"):
                    admin_ids.append(a_id)
    except Exception as e:
        logger.error("Failed to fetch admin list for inbound alerts: %s", e)

    return admin_ids


async def notify_admins(bot: Bot, text: str) -> None:
    admin_ids = await get_authorized_admin_ids()
    for a_id in admin_ids:
        try:
            await bot.send_message(chat_id=a_id, text=text, parse_mode="HTML")
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        except Exception as e:
            logger.warning(
                "Failed to send inbound monitor alert to admin %d: %s", a_id, e
            )


async def check_and_notify_inbounds(bot: Bot) -> None:
    results = await check_inbounds_health()
    if not results:
        return

    now_ts = time.time()
    now_str = format_datetime(now_ts, with_seconds=True)

    for item in results:
        ib_id = item["id"]
        remark = item["remark"]
        protocol = item["protocol"]
        port = item["port"]
        host = item["host"]
        is_ok = item["is_ok"]
        is_enabled = item["enable"]
        err = item["error"]
        latency = item["latency_ms"]

        if not is_enabled:
            continue

        prev_state = _inbound_status_cache.get(ib_id)

        if not is_ok:
            if prev_state is None or not prev_state.get("is_down", False):
                _inbound_status_cache[ib_id] = {
                    "is_down": True,
                    "last_error": err,
                    "last_check": now_ts,
                }
                text = (
                    f"🚨 <b>هشدار فوری: قطعی اینباند سرور!</b>\n\n"
                    f"🏷 <b>نام اینباند:</b> {remark}\n"
                    f"🔢 <b>شناسه اینباند:</b> #{to_persian_digits(ib_id)}\n"
                    f"🌐 <b>پروتکل:</b> <code>{protocol}</code>\n"
                    f"🔌 <b>پورت:</b> <code>{to_persian_digits(port)}</code>\n"
                    f"🖥 <b>آدرس سرور:</b> <code>{host}</code>\n"
                    f"❌ <b>علت خطا:</b> <code>{err}</code>\n"
                    f"📅 <b>زمان ثبت:</b> {now_str}\n\n"
                    f"⚠️ <i>ارتباط با این پورت بر روی سرور برقرار نشد یا سرویس پاسخگو نیست. لطفاً وضعیت سرور و هسته Xray را بررسی نمایید.</i>"
                )
                logger.warning(
                    "Inbound #%d (%s:%d) went DOWN! Notifying admins.",
                    ib_id,
                    host,
                    port,
                )
                await notify_admins(bot, text)
            else:
                _inbound_status_cache[ib_id]["last_check"] = now_ts
                _inbound_status_cache[ib_id]["last_error"] = err
        else:
            if prev_state is not None and prev_state.get("is_down", False):
                _inbound_status_cache[ib_id] = {
                    "is_down": False,
                    "last_error": "",
                    "last_check": now_ts,
                }
                text = (
                    f"✅ <b>رفع قطعی: اتصال موفق اینباند</b>\n\n"
                    f"🏷 <b>نام اینباند:</b> {remark}\n"
                    f"🔢 <b>شناسه اینباند:</b> #{to_persian_digits(ib_id)}\n"
                    f"🌐 <b>پروتکل:</b> <code>{protocol}</code>\n"
                    f"🔌 <b>پورت:</b> <code>{to_persian_digits(port)}</code>\n"
                    f"🖥 <b>آدرس سرور:</b> <code>{host}</code>\n"
                    f"⚡️ <b>زمان پاسخگویی (Ping):</b> <b>{to_persian_digits(round(latency))} ms</b>\n"
                    f"📅 <b>زمان اتصال:</b> {now_str}\n\n"
                    f"🟢 <i>ارتباط با پورت اینباند مجدداً برقرار شد و به مدار بازگشت.</i>"
                )
                logger.info(
                    "Inbound #%d (%s:%d) RECOVERED! Notifying admins.",
                    ib_id,
                    host,
                    port,
                )
                await notify_admins(bot, text)
            else:
                _inbound_status_cache[ib_id] = {
                    "is_down": False,
                    "last_error": "",
                    "last_check": now_ts,
                }


async def start_inbound_monitor_scheduler(bot: Bot) -> None:
    logger.info("Starting Inbound Health Monitor scheduler...")
    await asyncio.sleep(20)

    while True:
        try:
            cfg = await get_inbound_monitor_config()
            if cfg.get("enabled", False):
                await check_and_notify_inbounds(bot)
        except Exception as e:
            logger.error("Unexpected error in Inbound Monitor loop: %s", e)

        try:
            cfg = await get_inbound_monitor_config()
            interval = int(cfg.get("interval_seconds", 60))
            sleep_seconds = max(5, interval)
        except Exception:
            sleep_seconds = 60

        await asyncio.sleep(sleep_seconds)
