from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from config import XUI_BASE_URL, XUI_API_TOKEN

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=XUI_BASE_URL,
            headers={
                "Authorization": f"Bearer {XUI_API_TOKEN}",
                "Content-Type": "application/json",
            },
            verify=False,
            timeout=30.0,
        )
    return _client


async def _request(
    method: str,
    path: str,
    json_data: dict | None = None,
    params: dict | None = None,
) -> dict[str, Any]:
    client = _get_client()
    resp = await client.request(method, path, json=json_data, params=params)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success", False):
        msg = data.get("msg", "Unknown X-UI error")
        logger.error("X-UI API error: %s (path=%s)", msg, path)
        raise RuntimeError(f"X-UI API error: {msg}")
    return data


async def list_inbounds() -> list[dict[str, Any]]:
    data = await _request("GET", "/panel/api/inbounds/list")
    return data.get("obj", [])


async def list_clients() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        data = await _request("GET", "/panel/api/clients/list")
        obj = data.get("obj")
        if isinstance(obj, list):
            for item in obj:
                if not isinstance(item, dict):
                    continue
                email = item.get("email", "")
                if not email:
                    continue

                traffic = item.get("traffic") or {}
                up = traffic.get("up", 0) if isinstance(traffic, dict) else 0
                down = traffic.get("down", 0) if isinstance(traffic, dict) else 0
                total_gb = item.get("totalGB", 0)

                client_obj = {
                    "id": item.get("id"),
                    "email": email,
                    "subId": item.get("subId", ""),
                    "uuid": item.get("uuid", ""),
                    "limitIp": item.get("limitIp", 0),
                    "totalGB": total_gb,
                    "expiryTime": item.get("expiryTime", 0),
                    "enable": bool(item.get("enable", True)),
                    "tgId": item.get("tgId", 0),
                    "group": item.get("group", ""),
                    "comment": item.get("comment", ""),
                    "inboundIds": item.get("inboundIds", []),
                    "up": up,
                    "down": down,
                }
                result.append(client_obj)
    except Exception as e:
        logger.warning(
            "Failed to fetch clients from /api/clients/list: %s. Falling back to inbounds parsing.",
            e,
        )

    return result


async def get_inbound(inbound_id: int) -> dict[str, Any]:
    data = await _request("GET", f"/panel/api/inbounds/get/{inbound_id}")
    return data.get("obj", {})


async def add_client(
    email: str,
    total_gb: int,
    expiry_time: int,
    tg_id: int,
    inbound_ids: list[int],
    enable: bool = True,
    limit_ip: int = 0,
    group: str = "",
) -> dict[str, Any]:
    import uuid

    target_inbound_ids: list[int] = []
    try:
        server_inbounds = await list_inbounds()
        enabled_server_ids = [
            int(ib["id"]) for ib in server_inbounds if ib.get("enable", True)
        ]
        if enabled_server_ids:
            target_inbound_ids = [i for i in inbound_ids if i in enabled_server_ids]
            if not target_inbound_ids:
                target_inbound_ids = enabled_server_ids
    except Exception as e:
        logger.warning("Failed to validate inbound_ids with X-UI panel: %s", e)

    if not target_inbound_ids:
        target_inbound_ids = inbound_ids

    if not target_inbound_ids:
        raise RuntimeError("هیچ اینباند فعالی روی سرور جهت ساخت اشتراک یافت نشد.")

    sub_id = uuid.uuid4().hex[:16]

    payload = {
        "client": {
            "email": email,
            "totalGB": int(round(total_gb)),
            "expiryTime": int(expiry_time),
            "tgId": tg_id,
            "limitIp": limit_ip,
            "enable": enable,
            "subId": sub_id,
            "reset": 0,
            "comment": "",
            "security": "auto",
            "group": group,
        },
        "inboundIds": target_inbound_ids,
    }
    data = await _request("POST", "/panel/api/clients/add", json_data=payload)
    return data


async def get_client(email: str) -> dict[str, Any] | None:
    try:
        data = await _request("GET", f"/panel/api/clients/get/{email}")
        obj = data.get("obj")
        if obj is None:
            return None
        if isinstance(obj, dict) and "client" in obj:
            return obj["client"]
        return obj
    except (RuntimeError, httpx.HTTPStatusError):
        return None


async def get_client_full(email: str) -> dict[str, Any] | None:
    try:
        data = await _request("GET", f"/panel/api/clients/get/{email}")
        return data.get("obj")
    except (RuntimeError, httpx.HTTPStatusError):
        return None


async def get_clients_by_tg_id(tg_id: int) -> list[dict[str, Any]]:
    try:
        data = await _request("GET", f"/panel/api/clients/get/tgId/{tg_id}")
        obj = data.get("obj")
        if isinstance(obj, list):
            return obj
        if obj is not None:
            return [obj]
        return []
    except (RuntimeError, httpx.HTTPStatusError):
        return []


async def search_clients_all(query: str) -> list[dict[str, Any]]:
    clean_q = query.strip().lower()
    if clean_q.startswith("@"):
        clean_q = clean_q[1:]

    results: list[dict[str, Any]] = []
    seen_emails: set[str] = set()

    c = await get_client(query.strip())
    if c and c.get("email"):
        email = c["email"]
        seen_emails.add(email)
        results.append(c)

    if clean_q.isdigit():
        tg_id = int(clean_q)
        by_tg = await get_clients_by_tg_id(tg_id)
        for client in by_tg:
            if isinstance(client, dict):
                em = client.get("email") or client.get("client", {}).get("email")
                if em and em not in seen_emails:
                    seen_emails.add(em)
                    results.append(
                        client.get("client") if "client" in client else client
                    )

    try:
        inbounds = await list_inbounds()
        for ib in inbounds:
            client_stats = ib.get("clientStats") or []
            for cs in client_stats:
                em = cs.get("email", "")
                tg = str(cs.get("tgId", ""))
                if (clean_q in em.lower() or clean_q in tg) and em not in seen_emails:
                    full_c = await get_client(em)
                    if full_c:
                        seen_emails.add(em)
                        results.append(full_c)
    except Exception as e:
        logger.warning("Error searching inbounds for clients: %s", e)

    return results


async def update_client(
    email: str,
    client_data: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(client_data)

    payload.pop("id", None)
    payload.pop("allowedIPs", None)

    if "email" not in payload:
        payload["email"] = email

    data = await _request(
        "POST", f"/panel/api/clients/update/{email}", json_data=payload
    )
    return data


async def delete_client(email: str) -> dict[str, Any]:
    data = await _request("POST", f"/panel/api/clients/del/{email}")
    return data


async def renew_client(
    email: str,
    duration_days: int,
    data_gb: int,
    users_count: int = 1,
) -> dict[str, Any]:
    client = await get_client(email)
    if not client:
        raise RuntimeError(f"Client {email} not found")

    import time

    now_ms = int(time.time() * 1000)
    current_expiry = client.get("expiryTime", 0) or 0
    added_ms = duration_days * 86400 * 1000

    if current_expiry > now_ms:
        new_expiry_ms = current_expiry + added_ms
    else:
        new_expiry_ms = now_ms + added_ms

    current_total_bytes = client.get("totalGB", 0) or 0
    added_bytes = data_gb * 1024 * 1024 * 1024
    new_total_bytes = current_total_bytes + added_bytes

    client["expiryTime"] = new_expiry_ms
    client["totalGB"] = new_total_bytes
    if users_count:
        client["limitIp"] = users_count
    client["enable"] = True

    res = await update_client(email, client)

    try:
        from db.models import clear_notified_alerts

        await clear_notified_alerts(email)
    except Exception as e:
        logger.warning("Failed to clear notified alerts for %s: %s", email, e)

    return res


async def reset_client_traffic(email: str) -> dict[str, Any]:
    data = await _request("POST", f"/panel/api/clients/resetTraffic/{email}")
    return data


async def get_client_traffic(email: str) -> dict[str, Any] | None:
    try:
        data = await _request("GET", f"/panel/api/clients/traffic/{email}")
        return data.get("obj")
    except (RuntimeError, httpx.HTTPStatusError):
        return None


async def get_sub_links(sub_id: str) -> list[str]:
    try:
        data = await _request("GET", f"/panel/api/clients/subLinks/{sub_id}")
        return data.get("obj", [])
    except (RuntimeError, httpx.HTTPStatusError):
        return []


async def get_client_links(email: str) -> list[str]:
    try:
        data = await _request("GET", f"/panel/api/clients/links/{email}")
        return data.get("obj", [])
    except (RuntimeError, httpx.HTTPStatusError):
        return []


async def get_all_client_ips() -> dict[str, list[str]]:
    ip_map: dict[str, list[str]] = {}
    try:
        data = await _request("GET", "/panel/api/server/clientIps")
        obj = data.get("obj")
        if isinstance(obj, list):
            for item in obj:
                if not isinstance(item, dict):
                    continue
                email = item.get("clientEmail")
                if not email:
                    continue

                raw_ips = item.get("ips")
                ip_list: list[str] = []

                if isinstance(raw_ips, list):
                    for entry in raw_ips:
                        if isinstance(entry, dict):
                            ip_val = entry.get("ip")
                            if ip_val and str(ip_val).strip():
                                ip_list.append(str(ip_val).strip())
                        elif entry and str(entry).strip():
                            ip_list.append(str(entry).strip())
                elif isinstance(raw_ips, str) and raw_ips:
                    ip_list = [p.strip() for p in raw_ips.split(",") if p.strip()]

                ip_map[email] = ip_list
    except Exception as e:
        logger.warning("Failed to fetch all client IPs from X-UI: %s", e)

    return ip_map


async def set_inbound_enable(inbound_id: int, enable: bool) -> dict[str, Any]:
    payload = {"enable": enable}
    data = await _request(
        "POST", f"/panel/api/inbounds/setEnable/{inbound_id}", json_data=payload
    )
    return data


async def list_client_groups() -> list[dict[str, Any]]:
    try:
        data = await _request("GET", "/panel/api/clients/groups")
        return data.get("obj", [])
    except Exception as e:
        logger.error("Failed to list client groups: %s", e)
        return []


async def create_client_group(name: str) -> dict[str, Any]:
    payload = {"name": name}
    data = await _request("POST", "/panel/api/clients/groups/create", json_data=payload)
    return data


async def rename_client_group(old_name: str, new_name: str) -> dict[str, Any]:
    payload = {"oldName": old_name, "newName": new_name}
    data = await _request("POST", "/panel/api/clients/groups/rename", json_data=payload)
    return data


async def delete_client_group(name: str) -> dict[str, Any]:
    payload = {"name": name}
    data = await _request("POST", "/panel/api/clients/groups/delete", json_data=payload)
    return data


async def bulk_grant_volume(extra_gb: int, bot: Any | None = None) -> tuple[int, int]:
    clients = await list_clients()
    if not clients:
        return 0, 0

    success_count = 0
    fail_count = 0
    extra_bytes = extra_gb * (1024**3)

    from db.models import resolve_client_tg_id
    from utils.formatting import format_size_gb

    for c in clients:
        email = c.get("email", "")
        if not email:
            continue

        try:
            client_full = await get_client(email)
            if not client_full:
                fail_count += 1
                continue

            current_gb_bytes = client_full.get("totalGB", 0)
            new_gb_bytes = current_gb_bytes + extra_bytes

            update_data = dict(client_full)
            update_data["totalGB"] = new_gb_bytes
            await update_client(email, update_data)

            success_count += 1

            if bot:
                tg_id = await resolve_client_tg_id(client_full)
                if tg_id > 0:
                    try:
                        gift_msg = (
                            f"🎁 <b>اطلاعیه هدیه ویژه مدیریت!</b>\n\n"
                            f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                            f"📊 <b>حجم هدیه افزوده شده:</b> <b>+{format_size_gb(extra_gb)}</b>\n\n"
                            f"اعتبار حجم جدید با موفقیت به سرویس شما اضافه شد."
                        )
                        await bot.send_message(
                            chat_id=tg_id, text=gift_msg, parse_mode="HTML"
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("Failed to grant bulk volume to %s: %s", email, e)
            fail_count += 1

    return success_count, fail_count


async def bulk_grant_duration(
    extra_days: int, bot: Any | None = None
) -> tuple[int, int]:
    clients = await list_clients()
    if not clients:
        return 0, 0

    success_count = 0
    fail_count = 0
    extra_ms = extra_days * 86400 * 1000

    from db.models import resolve_client_tg_id
    from utils.formatting import to_persian_digits

    for c in clients:
        email = c.get("email", "")
        if not email:
            continue

        try:
            client_full = await get_client(email)
            if not client_full:
                fail_count += 1
                continue

            current_expiry = client_full.get("expiryTime", 0)
            now_ms = int(time.time() * 1000)

            if current_expiry and current_expiry > now_ms:
                new_expiry = current_expiry + extra_ms
            else:
                new_expiry = now_ms + extra_ms

            update_data = dict(client_full)
            update_data["expiryTime"] = new_expiry
            await update_client(email, update_data)

            success_count += 1

            if bot:
                tg_id = await resolve_client_tg_id(client_full)
                if tg_id > 0:
                    try:
                        gift_msg = (
                            f"🎁 <b>اطلاعیه هدیه ویژه مدیریت!</b>\n\n"
                            f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                            f"⏱ <b>مدت تمدید هدیه:</b> <b>+{to_persian_digits(extra_days)} روز</b>\n\n"
                            f"زمان اعتبار جدید با موفقیت به سرویس شما اضافه شد."
                        )
                        await bot.send_message(
                            chat_id=tg_id, text=gift_msg, parse_mode="HTML"
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("Failed to grant bulk duration to %s: %s", email, e)
            fail_count += 1

    return success_count, fail_count


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
