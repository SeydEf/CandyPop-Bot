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


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
