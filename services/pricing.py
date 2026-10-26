from __future__ import annotations

import json
import logging
from typing import Any

from db.models import get_setting, set_setting

logger = logging.getLogger(__name__)

DEFAULT_BASE_GB_RATE = 5_000
DEFAULT_USER_SURCHARGE = 50_000
DEFAULT_DURATION_SURCHARGES: dict[int, int] = {
    30: 0,
    60: 50_000,
    90: 100_000,
}
DEFAULT_VOLUME_TIERS: list[tuple[int, int]] = [
    (20, 5_000),
    (50, 4_500),
    (100, 4_000),
]
DEFAULT_FALLBACK_GB_RATE = 3_500
DEFAULT_CUSTOM_VOLUME_MIN_GB = 10
DEFAULT_CUSTOM_VOLUME_MAX_GB = 150

DEFAULT_VOLUME_PLANS: list[dict[str, Any]] = [
    {
        "gb": 10,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
        "user_config": {
            "mode": "inherit",
            "fixed_users": 1,
            "price_type": "inherit",
            "manual_surcharge": 0,
        },
    },
    {
        "gb": 30,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
        "user_config": {
            "mode": "inherit",
            "fixed_users": 1,
            "price_type": "inherit",
            "manual_surcharge": 0,
        },
    },
    {
        "gb": 50,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
        "user_config": {
            "mode": "inherit",
            "fixed_users": 1,
            "price_type": "inherit",
            "manual_surcharge": 0,
        },
    },
    {
        "gb": 70,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
        "user_config": {
            "mode": "inherit",
            "fixed_users": 1,
            "price_type": "inherit",
            "manual_surcharge": 0,
        },
    },
    {
        "gb": 90,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
        "user_config": {
            "mode": "inherit",
            "fixed_users": 1,
            "price_type": "inherit",
            "manual_surcharge": 0,
        },
    },
    {
        "gb": 100,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
        "user_config": {
            "mode": "inherit",
            "fixed_users": 1,
            "price_type": "inherit",
            "manual_surcharge": 0,
        },
    },
]

DEFAULT_DURATION_PLANS: list[dict[str, Any]] = [
    {"days": 30, "surcharge": 0, "enabled_buy": True, "enabled_renew": True},
    {"days": 60, "surcharge": 50_000, "enabled_buy": True, "enabled_renew": True},
    {"days": 90, "surcharge": 100_000, "enabled_buy": True, "enabled_renew": True},
]

DEFAULT_USER_SELECTION_CONFIG: dict[str, Any] = {
    "enabled": True,
    "fixed_users": 1,
    "price_type": "auto",
    "manual_surcharge": 0,
}

DEFAULT_PLAN_USER_CONFIG: dict[str, Any] = {
    "mode": "inherit",
    "fixed_users": 1,
    "price_type": "inherit",
    "manual_surcharge": 0,
}

_pricing_cache: dict[str, Any] | None = None


async def get_pricing_config() -> dict[str, Any]:
    global _pricing_cache
    if _pricing_cache is not None:
        return _pricing_cache

    return await load_pricing_config()


def _clean_durations_dict(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    res: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            res[str(k)] = {
                "enabled_buy": bool(v.get("enabled_buy", True)),
                "enabled_renew": bool(v.get("enabled_renew", True)),
                "price_type": "manual" if v.get("price_type") == "manual" else "auto",
                "price": max(0, int(v.get("price", 0))),
            }
    return res


def _clean_user_selection_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(DEFAULT_USER_SELECTION_CONFIG)
    enabled = bool(raw.get("enabled", True))
    try:
        fixed_users = max(1, int(raw.get("fixed_users", 1)))
    except (ValueError, TypeError):
        fixed_users = 1
    price_type = str(raw.get("price_type", "auto"))
    if price_type not in ("auto", "manual"):
        price_type = "auto"
    try:
        manual_surcharge = max(0, int(raw.get("manual_surcharge", 0)))
    except (ValueError, TypeError):
        manual_surcharge = 0
    return {
        "enabled": enabled,
        "fixed_users": fixed_users,
        "price_type": price_type,
        "manual_surcharge": manual_surcharge,
    }


def _clean_plan_user_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(DEFAULT_PLAN_USER_CONFIG)
    mode = str(raw.get("mode", "inherit"))
    if mode not in ("inherit", "enabled", "disabled"):
        mode = "inherit"
    try:
        fixed_users = max(1, int(raw.get("fixed_users", 1)))
    except (ValueError, TypeError):
        fixed_users = 1
    price_type = str(raw.get("price_type", "inherit"))
    if price_type not in ("inherit", "auto", "manual"):
        price_type = "inherit"
    try:
        manual_surcharge = max(0, int(raw.get("manual_surcharge", 0)))
    except (ValueError, TypeError):
        manual_surcharge = 0
    return {
        "mode": mode,
        "fixed_users": fixed_users,
        "price_type": price_type,
        "manual_surcharge": manual_surcharge,
    }


async def load_pricing_config() -> dict[str, Any]:
    global _pricing_cache

    base_rate_str = await get_setting("pricing_base_gb_rate")
    base_rate = int(base_rate_str) if base_rate_str else DEFAULT_BASE_GB_RATE

    user_surcharge_str = await get_setting("pricing_user_surcharge")
    user_surcharge = (
        int(user_surcharge_str) if user_surcharge_str else DEFAULT_USER_SURCHARGE
    )

    dur_str = await get_setting("pricing_duration_surcharges")
    if dur_str:
        try:
            raw_dur = json.loads(dur_str)
            duration_surcharges = {int(k): int(v) for k, v in raw_dur.items()}
        except Exception:
            duration_surcharges = dict(DEFAULT_DURATION_SURCHARGES)
    else:
        duration_surcharges = dict(DEFAULT_DURATION_SURCHARGES)

    dur_plans_str = await get_setting("pricing_duration_plans")
    if dur_plans_str:
        try:
            raw_dur_plans = json.loads(dur_plans_str)
            duration_plans = [
                {
                    "days": int(p.get("days", 0)),
                    "surcharge": int(p.get("surcharge", 0)),
                    "enabled_buy": bool(p.get("enabled_buy", True)),
                    "enabled_renew": bool(p.get("enabled_renew", True)),
                }
                for p in raw_dur_plans
                if int(p.get("days", 0)) > 0
            ]
        except Exception:
            duration_plans = [dict(p) for p in DEFAULT_DURATION_PLANS]
    else:
        duration_plans = [dict(p) for p in DEFAULT_DURATION_PLANS]

    for dp in duration_plans:
        duration_surcharges[dp["days"]] = dp["surcharge"]

    vol_plans_str = await get_setting("pricing_volume_plans")
    if vol_plans_str:
        try:
            raw_vol_plans = json.loads(vol_plans_str)
            volume_plans = [
                {
                    "gb": int(p.get("gb", 0)),
                    "price_type": str(p.get("price_type", "auto")),
                    "price": int(p.get("price", 0)),
                    "button_text": str(p.get("button_text", "")).strip(),
                    "enabled_buy": bool(p.get("enabled_buy", True)),
                    "enabled_renew": bool(p.get("enabled_renew", True)),
                    "is_unlimited": bool(p.get("is_unlimited", False)),
                    "durations": _clean_durations_dict(p.get("durations")),
                    "user_config": _clean_plan_user_config(p.get("user_config")),
                }
                for p in raw_vol_plans
                if int(p.get("gb", 0)) > 0
            ]
        except Exception:
            volume_plans = [dict(p) for p in DEFAULT_VOLUME_PLANS]
    else:
        volume_plans = [dict(p) for p in DEFAULT_VOLUME_PLANS]

    custom_vol_str = await get_setting("pricing_custom_volume_enabled", "1")
    custom_vol_enabled = custom_vol_str != "0"
    custom_vol_buy_str = await get_setting("pricing_custom_volume_buy_enabled")
    custom_vol_buy_enabled = (
        custom_vol_buy_str != "0"
        if custom_vol_buy_str is not None
        else custom_vol_enabled
    )
    custom_vol_renew_str = await get_setting("pricing_custom_volume_renew_enabled")
    custom_vol_renew_enabled = (
        custom_vol_renew_str != "0"
        if custom_vol_renew_str is not None
        else custom_vol_enabled
    )

    custom_dur_str = await get_setting("pricing_custom_duration_enabled", "1")
    custom_dur_enabled = custom_dur_str != "0"
    custom_dur_buy_str = await get_setting("pricing_custom_duration_buy_enabled")
    custom_dur_buy_enabled = (
        custom_dur_buy_str != "0"
        if custom_dur_buy_str is not None
        else custom_dur_enabled
    )
    custom_dur_renew_str = await get_setting("pricing_custom_duration_renew_enabled")
    custom_dur_renew_enabled = (
        custom_dur_renew_str != "0"
        if custom_dur_renew_str is not None
        else custom_dur_enabled
    )

    tiers_str = await get_setting("pricing_volume_tiers")
    if tiers_str:
        try:
            raw_tiers = json.loads(tiers_str)
            volume_tiers = [
                (int(tier[0]), int(tier[1])) for tier in raw_tiers.get("tiers", [])
            ]
            fallback_rate = int(
                raw_tiers.get("fallback_rate", DEFAULT_FALLBACK_GB_RATE)
            )
        except Exception:
            volume_tiers = list(DEFAULT_VOLUME_TIERS)
            fallback_rate = DEFAULT_FALLBACK_GB_RATE
    else:
        volume_tiers = list(DEFAULT_VOLUME_TIERS)
        fallback_rate = DEFAULT_FALLBACK_GB_RATE

    tiers_enabled_str = await get_setting("pricing_volume_tiers_enabled", "1")
    volume_tiers_enabled = tiers_enabled_str != "0"

    custom_vol_min_str = await get_setting("pricing_custom_volume_min_gb")
    try:
        custom_vol_min = (
            int(custom_vol_min_str)
            if custom_vol_min_str
            else DEFAULT_CUSTOM_VOLUME_MIN_GB
        )
    except (ValueError, TypeError):
        custom_vol_min = DEFAULT_CUSTOM_VOLUME_MIN_GB

    custom_vol_max_str = await get_setting("pricing_custom_volume_max_gb")
    try:
        custom_vol_max = (
            int(custom_vol_max_str)
            if custom_vol_max_str
            else DEFAULT_CUSTOM_VOLUME_MAX_GB
        )
    except (ValueError, TypeError):
        custom_vol_max = DEFAULT_CUSTOM_VOLUME_MAX_GB

    user_sel_str = await get_setting("pricing_user_selection_config")
    if user_sel_str:
        try:
            raw_user_sel = json.loads(user_sel_str)
            user_sel_cfg = _clean_user_selection_config(raw_user_sel)
        except Exception:
            user_sel_cfg = dict(DEFAULT_USER_SELECTION_CONFIG)
    else:
        user_sel_cfg = dict(DEFAULT_USER_SELECTION_CONFIG)

    _pricing_cache = {
        "base_gb_rate": base_rate,
        "user_surcharge": user_surcharge,
        "duration_surcharges": duration_surcharges,
        "volume_tiers": volume_tiers,
        "fallback_gb_rate": fallback_rate,
        "volume_tiers_enabled": volume_tiers_enabled,
        "volume_plans": volume_plans,
        "duration_plans": duration_plans,
        "custom_volume_enabled": custom_vol_enabled,
        "custom_volume_buy_enabled": custom_vol_buy_enabled,
        "custom_volume_renew_enabled": custom_vol_renew_enabled,
        "custom_volume_min_gb": custom_vol_min,
        "custom_volume_max_gb": custom_vol_max,
        "custom_duration_enabled": custom_dur_enabled,
        "custom_duration_buy_enabled": custom_dur_buy_enabled,
        "custom_duration_renew_enabled": custom_dur_renew_enabled,
        "user_selection_config": user_sel_cfg,
    }
    return _pricing_cache


def invalidate_pricing_cache() -> None:
    global _pricing_cache
    _pricing_cache = None


async def update_base_gb_rate(rate: int) -> None:
    config = await get_pricing_config()
    old_base = config.get("base_gb_rate", DEFAULT_BASE_GB_RATE)
    if old_base <= 0:
        old_base = DEFAULT_BASE_GB_RATE

    ratio = rate / old_base

    await set_setting("pricing_base_gb_rate", str(rate))

    tiers_str = await get_setting("pricing_volume_tiers")
    if tiers_str:
        try:
            raw_tiers = json.loads(tiers_str)
            existing_tiers = raw_tiers.get("tiers", [])
            fallback = raw_tiers.get("fallback_rate", DEFAULT_FALLBACK_GB_RATE)

            new_tiers = []
            for item in existing_tiers:
                gb_limit = int(item[0])
                tier_rate = int(item[1])
                new_tier_rate = max(1, int(round(tier_rate * ratio)))
                new_tiers.append([gb_limit, new_tier_rate])

            if new_tiers:
                new_tiers[0][1] = rate

            new_fallback = max(1, int(round(fallback * ratio)))

            updated_data = {
                "tiers": new_tiers,
                "fallback_rate": new_fallback,
            }
            await set_setting("pricing_volume_tiers", json.dumps(updated_data))
        except Exception as e:
            logger.exception("Error scaling volume tiers on base rate change: %s", e)
    else:
        new_tiers = [
            [20, rate],
            [50, max(1, int(round(4_500 * ratio)))],
            [100, max(1, int(round(4_000 * ratio)))],
        ]
        new_fallback = max(1, int(round(DEFAULT_FALLBACK_GB_RATE * ratio)))
        tiers_data = {
            "tiers": new_tiers,
            "fallback_rate": new_fallback,
        }
        await set_setting("pricing_volume_tiers", json.dumps(tiers_data))

    invalidate_pricing_cache()


async def update_user_surcharge(surcharge: int) -> None:
    await set_setting("pricing_user_surcharge", str(surcharge))
    invalidate_pricing_cache()


async def update_duration_surcharge(duration_days: int, surcharge: int) -> None:
    config = await get_pricing_config()
    durations = dict(config["duration_surcharges"])
    durations[duration_days] = surcharge
    await set_setting("pricing_duration_surcharges", json.dumps(durations))
    invalidate_pricing_cache()


async def update_volume_tiers(tiers: list[tuple[int, int]], fallback_rate: int) -> None:
    data = {
        "tiers": [[gb, rate] for gb, rate in tiers],
        "fallback_rate": fallback_rate,
    }
    await set_setting("pricing_volume_tiers", json.dumps(data))
    if tiers:
        await set_setting("pricing_base_gb_rate", str(tiers[0][1]))
    invalidate_pricing_cache()


async def set_volume_tiers_enabled(enabled: bool) -> None:
    await set_setting("pricing_volume_tiers_enabled", "1" if enabled else "0")
    invalidate_pricing_cache()


async def get_volume_plans() -> list[dict[str, Any]]:
    config = await get_pricing_config()
    return [dict(p) for p in config.get("volume_plans", DEFAULT_VOLUME_PLANS)]


async def set_volume_plans(plans: list[dict[str, Any]]) -> None:
    cleaned = [
        {
            "gb": int(p.get("gb", 0)),
            "price_type": str(p.get("price_type", "auto")),
            "price": int(p.get("price", 0)),
            "button_text": str(p.get("button_text", "")).strip(),
            "enabled_buy": bool(p.get("enabled_buy", True)),
            "enabled_renew": bool(p.get("enabled_renew", True)),
            "is_unlimited": bool(p.get("is_unlimited", False)),
            "durations": _clean_durations_dict(p.get("durations")),
            "user_config": _clean_plan_user_config(p.get("user_config")),
        }
        for p in plans
        if int(p.get("gb", 0)) > 0
    ]
    await set_setting("pricing_volume_plans", json.dumps(cleaned))
    invalidate_pricing_cache()


async def get_user_selection_config() -> dict[str, Any]:
    config = await get_pricing_config()
    return dict(config.get("user_selection_config", DEFAULT_USER_SELECTION_CONFIG))


async def set_user_selection_config(cfg: dict[str, Any]) -> None:
    cleaned = _clean_user_selection_config(cfg)
    await set_setting("pricing_user_selection_config", json.dumps(cleaned))
    invalidate_pricing_cache()


async def resolve_plan_user_config(plan_gb: int | None = None) -> dict[str, Any]:
    global_cfg = await get_user_selection_config()
    global_enabled = bool(global_cfg.get("enabled", True))
    global_fixed_users = max(1, int(global_cfg.get("fixed_users", 1)))
    global_price_type = str(global_cfg.get("price_type", "auto"))
    global_manual_surch = max(0, int(global_cfg.get("manual_surcharge", 0)))

    plan_found = False
    mode = "inherit"
    allow_selection = global_enabled
    fixed_users = global_fixed_users
    price_type = global_price_type
    manual_surch = global_manual_surch

    if plan_gb is not None:
        vol_plans = await get_volume_plans()
        for p in vol_plans:
            if p.get("gb") == plan_gb:
                plan_found = True
                u_cfg = p.get("user_config", {})
                mode = str(u_cfg.get("mode", "inherit"))
                if mode not in ("inherit", "enabled", "disabled"):
                    mode = "inherit"

                if mode == "enabled":
                    allow_selection = True
                    fixed_users = 1
                    price_type = "auto"
                    manual_surch = 0
                elif mode == "disabled":
                    allow_selection = False
                    fixed_users = max(
                        1, int(u_cfg.get("fixed_users", global_fixed_users))
                    )
                    p_ptype = str(u_cfg.get("price_type", "inherit"))
                    if p_ptype == "manual":
                        price_type = "manual"
                        manual_surch = max(0, int(u_cfg.get("manual_surcharge", 0)))
                    elif p_ptype == "auto":
                        price_type = "auto"
                        manual_surch = 0
                    else:  # inherit
                        price_type = global_price_type
                        manual_surch = global_manual_surch
                else:  # inherit
                    allow_selection = global_enabled
                    fixed_users = global_fixed_users
                    price_type = global_price_type
                    manual_surch = global_manual_surch
                break

    if allow_selection:
        user_surcharge = 0
        is_manual = False
    else:
        if price_type == "manual":
            user_surcharge = manual_surch
            is_manual = True
        else:
            cfg = await get_pricing_config()
            user_surcharge = max(0, fixed_users - 1) * cfg.get(
                "user_surcharge", DEFAULT_USER_SURCHARGE
            )
            is_manual = False

    return {
        "allow_selection": allow_selection,
        "users": fixed_users if not allow_selection else 1,
        "user_surcharge": user_surcharge,
        "is_manual_surcharge": is_manual,
        "price_type": price_type,
        "mode": mode if plan_found else "inherit",
    }


def format_volume_button_text(plan: dict[str, Any], data_price: int) -> str:
    from utils.formatting import format_price

    is_unlimited = bool(plan.get("is_unlimited", False))
    btn_text = str(plan.get("button_text", "")).strip()
    if not btn_text:
        if is_unlimited:
            return f"♾️ نامحدود ({format_price(data_price)})"
        return f"📊 {plan.get('gb', 0)} گیگ ({format_price(data_price)})"

    raw_price_str = f"{data_price:,}"
    price_str = format_price(data_price)
    gb_str = "نامحدود" if is_unlimited else str(plan.get("gb", 0))

    return (
        btn_text.replace("{gb}", gb_str)
        .replace("{price}", price_str)
        .replace("{raw_price}", raw_price_str)
    )


async def is_unlimited_volume(gb: int) -> bool:
    vol_plans = await get_volume_plans()
    return any(p.get("gb") == gb and p.get("is_unlimited") for p in vol_plans)


async def format_volume_label(
    gb: int,
    with_real_gb: bool = False,
    with_fair_policy: bool = False,
) -> str:
    from utils.formatting import format_size_gb, to_persian_digits

    if await is_unlimited_volume(gb):
        if with_fair_policy:
            return f"♾️ نامحدود (سیاست مصرف منصفانه {to_persian_digits(gb)} گیگابایت)"
        if with_real_gb:
            return f"♾️ نامحدود ({to_persian_digits(gb)} گیگ)"
        return "♾️ نامحدود"
    return format_size_gb(gb)


async def get_duration_plans() -> list[dict[str, Any]]:
    config = await get_pricing_config()
    return [dict(p) for p in config.get("duration_plans", DEFAULT_DURATION_PLANS)]


async def set_duration_plans(plans: list[dict[str, Any]]) -> None:
    cleaned = [
        {
            "days": int(p.get("days", 0)),
            "surcharge": int(p.get("surcharge", 0)),
            "enabled_buy": bool(p.get("enabled_buy", True)),
            "enabled_renew": bool(p.get("enabled_renew", True)),
        }
        for p in plans
        if int(p.get("days", 0)) > 0
    ]
    await set_setting("pricing_duration_plans", json.dumps(cleaned))
    dur_map = {p["days"]: p["surcharge"] for p in cleaned}
    await set_setting("pricing_duration_surcharges", json.dumps(dur_map))
    invalidate_pricing_cache()


async def is_custom_volume_enabled() -> bool:
    config = await get_pricing_config()
    return bool(config.get("custom_volume_enabled", True))


async def is_custom_volume_buy_enabled() -> bool:
    config = await get_pricing_config()
    return bool(
        config.get(
            "custom_volume_buy_enabled", config.get("custom_volume_enabled", True)
        )
    )


async def is_custom_volume_renew_enabled() -> bool:
    config = await get_pricing_config()
    return bool(
        config.get(
            "custom_volume_renew_enabled", config.get("custom_volume_enabled", True)
        )
    )


async def set_custom_volume_enabled(enabled: bool) -> None:
    await set_setting("pricing_custom_volume_enabled", "1" if enabled else "0")
    await set_setting("pricing_custom_volume_buy_enabled", "1" if enabled else "0")
    await set_setting("pricing_custom_volume_renew_enabled", "1" if enabled else "0")
    invalidate_pricing_cache()


async def set_custom_volume_buy_enabled(enabled: bool) -> None:
    await set_setting("pricing_custom_volume_buy_enabled", "1" if enabled else "0")
    invalidate_pricing_cache()


async def set_custom_volume_renew_enabled(enabled: bool) -> None:
    await set_setting("pricing_custom_volume_renew_enabled", "1" if enabled else "0")
    invalidate_pricing_cache()


async def get_custom_volume_range() -> tuple[int, int]:
    config = await get_pricing_config()
    min_gb = config.get("custom_volume_min_gb", DEFAULT_CUSTOM_VOLUME_MIN_GB)
    max_gb = config.get("custom_volume_max_gb", DEFAULT_CUSTOM_VOLUME_MAX_GB)
    return min_gb, max_gb


async def set_custom_volume_min_gb(min_gb: int) -> None:
    await set_setting("pricing_custom_volume_min_gb", str(min_gb))
    invalidate_pricing_cache()


async def set_custom_volume_max_gb(max_gb: int) -> None:
    await set_setting("pricing_custom_volume_max_gb", str(max_gb))
    invalidate_pricing_cache()


async def is_duration_allowed_for_volume(
    gb: int, days: int, for_renew: bool = False
) -> bool:
    vol_plans = await get_volume_plans()
    for p in vol_plans:
        if p.get("gb") == gb:
            durations = p.get("durations", {})
            k = str(days)
            if k in durations:
                return bool(
                    durations[k].get(
                        "enabled_renew" if for_renew else "enabled_buy", True
                    )
                )
            return True
    return True


async def is_custom_duration_allowed_for_volume(
    gb: int, for_renew: bool = False
) -> bool:
    vol_plans = await get_volume_plans()
    for p in vol_plans:
        if p.get("gb") == gb:
            durations = p.get("durations", {})
            if "custom" in durations:
                return bool(
                    durations["custom"].get(
                        "enabled_renew" if for_renew else "enabled_buy", True
                    )
                )
            return True
    return True


async def toggle_volume_plan_duration(
    plan_idx: int, duration_key: str, mode: str
) -> tuple[bool, str]:
    vol_plans = await get_volume_plans()
    if not (0 <= plan_idx < len(vol_plans)):
        return False, "not_found"

    plan = vol_plans[plan_idx]
    durations = dict(plan.get("durations", {}))
    cur_entry = dict(
        durations.get(duration_key, {"enabled_buy": True, "enabled_renew": True})
    )

    target_key = "enabled_buy" if mode == "buy" else "enabled_renew"
    cur_val = cur_entry.get(target_key, True)

    if cur_val:
        dur_plans = await get_duration_plans()
        all_keys = [str(d.get("days", 0)) for d in dur_plans] + ["custom"]
        active_count = 0
        for k in all_keys:
            d_val = durations.get(k, {}).get(target_key, True)
            if d_val:
                active_count += 1
        if active_count <= 1:
            return False, "last_active"

    new_val = not cur_val
    cur_entry[target_key] = new_val
    durations[duration_key] = cur_entry

    vol_plans[plan_idx]["durations"] = durations
    await set_volume_plans(vol_plans)
    return True, "ok"


async def reset_volume_plan_durations(plan_idx: int) -> None:
    vol_plans = await get_volume_plans()
    if not (0 <= plan_idx < len(vol_plans)):
        return
    vol_plans[plan_idx]["durations"] = {}
    await set_volume_plans(vol_plans)


async def enable_all_volume_plan_durations(plan_idx: int) -> None:
    dur_plans = await get_duration_plans()
    vol_plans = await get_volume_plans()
    if not (0 <= plan_idx < len(vol_plans)):
        return
    durations = dict(vol_plans[plan_idx].get("durations", {}))
    for d in dur_plans:
        k = str(d.get("days", 0))
        cur = dict(durations.get(k, {}))
        cur["enabled_buy"] = True
        cur["enabled_renew"] = True
        durations[k] = cur
    c_cur = dict(durations.get("custom", {}))
    c_cur["enabled_buy"] = True
    c_cur["enabled_renew"] = True
    durations["custom"] = c_cur
    vol_plans[plan_idx]["durations"] = durations
    await set_volume_plans(vol_plans)


async def get_volume_plan_duration_override(
    gb: int, duration_days: int
) -> dict[str, Any] | None:
    vol_plans = await get_volume_plans()
    for p in vol_plans:
        if p.get("gb") == gb:
            durations = p.get("durations", {})
            k = str(duration_days)
            if k in durations:
                entry = durations[k]
                if entry.get("price_type") == "manual":
                    return entry
    return None


async def get_volume_plan_duration_surcharge(gb: int, duration_days: int) -> int:
    override = await get_volume_plan_duration_override(gb, duration_days)
    if override and override.get("price_type") == "manual":
        return max(0, int(override.get("price", 0)))
    return await calculate_duration_surcharge(duration_days)


async def set_volume_plan_duration_price(
    plan_idx: int, duration_key: str, price_type: str, price: int = 0
) -> bool:
    vol_plans = await get_volume_plans()
    if not (0 <= plan_idx < len(vol_plans)):
        return False

    plan = vol_plans[plan_idx]
    durations = dict(plan.get("durations", {}))
    cur_entry = dict(
        durations.get(
            duration_key,
            {
                "enabled_buy": True,
                "enabled_renew": True,
                "price_type": "auto",
                "price": 0,
            },
        )
    )

    cur_entry["price_type"] = "manual" if price_type == "manual" else "auto"
    cur_entry["price"] = max(0, price) if price_type == "manual" else 0
    durations[duration_key] = cur_entry

    vol_plans[plan_idx]["durations"] = durations
    await set_volume_plans(vol_plans)
    return True


async def is_custom_duration_enabled() -> bool:
    config = await get_pricing_config()
    return bool(config.get("custom_duration_enabled", True))


async def is_custom_duration_buy_enabled() -> bool:
    config = await get_pricing_config()
    return bool(
        config.get(
            "custom_duration_buy_enabled", config.get("custom_duration_enabled", True)
        )
    )


async def is_custom_duration_renew_enabled() -> bool:
    config = await get_pricing_config()
    return bool(
        config.get(
            "custom_duration_renew_enabled", config.get("custom_duration_enabled", True)
        )
    )


async def set_custom_duration_enabled(enabled: bool) -> None:
    await set_setting("pricing_custom_duration_enabled", "1" if enabled else "0")
    await set_setting("pricing_custom_duration_buy_enabled", "1" if enabled else "0")
    await set_setting("pricing_custom_duration_renew_enabled", "1" if enabled else "0")
    invalidate_pricing_cache()


async def set_custom_duration_buy_enabled(enabled: bool) -> None:
    await set_setting("pricing_custom_duration_buy_enabled", "1" if enabled else "0")
    invalidate_pricing_cache()


async def set_custom_duration_renew_enabled(enabled: bool) -> None:
    await set_setting("pricing_custom_duration_renew_enabled", "1" if enabled else "0")
    invalidate_pricing_cache()


async def move_volume_plan(index: int, direction: int) -> bool:
    plans = await get_volume_plans()
    target_idx = index + direction
    if 0 <= index < len(plans) and 0 <= target_idx < len(plans):
        plans[index], plans[target_idx] = plans[target_idx], plans[index]
        await set_volume_plans(plans)
        return True
    return False


async def move_duration_plan(index: int, direction: int) -> bool:
    plans = await get_duration_plans()
    target_idx = index + direction
    if 0 <= index < len(plans) and 0 <= target_idx < len(plans):
        plans[index], plans[target_idx] = plans[target_idx], plans[index]
        await set_duration_plans(plans)
        return True
    return False


async def calculate_duration_surcharge(duration_days: int) -> int:
    dur_plans = await get_duration_plans()
    if not dur_plans:
        return 0

    for p in dur_plans:
        if p["days"] == duration_days:
            return p.get("surcharge", 0)

    sorted_plans = sorted(dur_plans, key=lambda x: x["days"])
    if duration_days <= sorted_plans[0]["days"]:
        return sorted_plans[0].get("surcharge", 0)

    if duration_days >= sorted_plans[-1]["days"]:
        last = sorted_plans[-1]
        prev = sorted_plans[-2] if len(sorted_plans) > 1 else None
        if prev and (last["days"] - prev["days"]) > 0:
            daily_rate = (last["surcharge"] - prev["surcharge"]) / (
                last["days"] - prev["days"]
            )
            extra_days = duration_days - last["days"]
            return int(round(last["surcharge"] + extra_days * daily_rate))
        elif last["days"] > 0:
            daily_rate = last["surcharge"] / last["days"]
            return int(round(daily_rate * duration_days))
        return last.get("surcharge", 0)

    for i in range(len(sorted_plans) - 1):
        p1 = sorted_plans[i]
        p2 = sorted_plans[i + 1]
        if p1["days"] <= duration_days <= p2["days"]:
            span = p2["days"] - p1["days"]
            if span > 0:
                ratio = (duration_days - p1["days"]) / span
                surcharge = p1["surcharge"] + ratio * (
                    p2["surcharge"] - p1["surcharge"]
                )
                return int(round(surcharge))
            return p1.get("surcharge", 0)

    return 0


async def reset_pricing_config_to_defaults() -> None:
    await set_setting("pricing_base_gb_rate", str(DEFAULT_BASE_GB_RATE))
    await set_setting("pricing_user_surcharge", str(DEFAULT_USER_SURCHARGE))
    await set_setting(
        "pricing_duration_surcharges", json.dumps(DEFAULT_DURATION_SURCHARGES)
    )
    tiers_data = {
        "tiers": [[gb, rate] for gb, rate in DEFAULT_VOLUME_TIERS],
        "fallback_rate": DEFAULT_FALLBACK_GB_RATE,
    }
    await set_setting("pricing_volume_tiers", json.dumps(tiers_data))
    await set_setting("pricing_volume_tiers_enabled", "1")
    await set_setting("pricing_volume_plans", json.dumps(DEFAULT_VOLUME_PLANS))
    await set_setting("pricing_duration_plans", json.dumps(DEFAULT_DURATION_PLANS))
    await set_setting("pricing_custom_volume_enabled", "1")
    await set_setting("pricing_custom_duration_enabled", "1")
    await set_setting("pricing_custom_volume_min_gb", str(DEFAULT_CUSTOM_VOLUME_MIN_GB))
    await set_setting("pricing_custom_volume_max_gb", str(DEFAULT_CUSTOM_VOLUME_MAX_GB))
    await set_setting(
        "pricing_user_selection_config", json.dumps(DEFAULT_USER_SELECTION_CONFIG)
    )
    invalidate_pricing_cache()


async def calculate_data_price(gb: int) -> int:
    if gb <= 0:
        return 0

    vol_plans = await get_volume_plans()
    for p in vol_plans:
        if p["gb"] == gb and p.get("price_type") == "manual" and p.get("price", 0) > 0:
            return int(p["price"])

    config = await get_pricing_config()
    if not config.get("volume_tiers_enabled", True):
        return gb * config["base_gb_rate"]

    tiers: list[tuple[int, int]] = config["volume_tiers"]
    fallback_rate: int = config["fallback_gb_rate"]

    sorted_tiers = sorted(tiers, key=lambda x: x[0])
    rate = fallback_rate

    for max_gb, tier_rate in sorted_tiers:
        if gb <= max_gb:
            rate = tier_rate
            break

    return gb * rate


async def calculate_total_price(gb: int, duration_days: int, users_count: int) -> int:
    breakdown = await get_price_breakdown(gb, duration_days, users_count)
    return breakdown["total_price"]


async def get_price_breakdown(
    gb: int, duration_days: int, users_count: int
) -> dict[str, Any]:
    config = await get_pricing_config()
    user_cfg = await resolve_plan_user_config(gb)

    if not user_cfg["allow_selection"] and user_cfg.get("is_manual_surcharge"):
        user_surcharge = user_cfg["user_surcharge"]
    else:
        extra_users = max(0, users_count - 1)
        user_surcharge = extra_users * config["user_surcharge"]

    data_price = await calculate_data_price(gb)
    dur_surcharge = await get_volume_plan_duration_surcharge(gb, duration_days)
    total_price = data_price + dur_surcharge + user_surcharge

    return {
        "data_price": data_price,
        "duration_surcharge": dur_surcharge,
        "user_surcharge": user_surcharge,
        "total_price": total_price,
    }
