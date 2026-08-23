from __future__ import annotations

import logging
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
    payload = {
        "client": {
            "email": email,
            "totalGB": int(round(total_gb)),
            "expiryTime": int(expiry_time),
            "tgId": tg_id,
            "limitIp": limit_ip,
            "enable": enable,
            "subId": "",
            "reset": 0,
            "comment": "",
            "security": "auto",
            "group": group,
        },
        "inboundIds": inbound_ids,
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
    new_expiry_ms = now_ms + (duration_days * 86400 * 1000)

    total_bytes = data_gb * 1024 * 1024 * 1024

    client["expiryTime"] = new_expiry_ms
    client["totalGB"] = total_bytes
    client["limitIp"] = users_count
    client["enable"] = True

    res = await update_client(email, client)

    try:
        await reset_client_traffic(email)
    except Exception:
        logger.exception("Failed to reset traffic stats for %s", email)

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


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
