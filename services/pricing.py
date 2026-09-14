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

DEFAULT_VOLUME_PLANS: list[dict[str, Any]] = [
    {
        "gb": 10,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
    },
    {
        "gb": 30,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
    },
    {
        "gb": 50,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
    },
    {
        "gb": 70,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
    },
    {
        "gb": 90,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
    },
    {
        "gb": 100,
        "price_type": "auto",
        "price": 0,
        "button_text": "",
        "enabled_buy": True,
        "enabled_renew": True,
    },
]

DEFAULT_DURATION_PLANS: list[dict[str, Any]] = [
    {"days": 30, "surcharge": 0, "enabled_buy": True, "enabled_renew": True},
    {"days": 60, "surcharge": 50_000, "enabled_buy": True, "enabled_renew": True},
    {"days": 90, "surcharge": 100_000, "enabled_buy": True, "enabled_renew": True},
]

_pricing_cache: dict[str, Any] | None = None


async def get_pricing_config() -> dict[str, Any]:
    global _pricing_cache
    if _pricing_cache is not None:
        return _pricing_cache

    return await load_pricing_config()


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
        "custom_duration_enabled": custom_dur_enabled,
        "custom_duration_buy_enabled": custom_dur_buy_enabled,
        "custom_duration_renew_enabled": custom_dur_renew_enabled,
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
        }
        for p in plans
        if int(p.get("gb", 0)) > 0
    ]
    await set_setting("pricing_volume_plans", json.dumps(cleaned))
    invalidate_pricing_cache()


def format_volume_button_text(plan: dict[str, Any], data_price: int) -> str:
    from utils.formatting import format_price

    btn_text = str(plan.get("button_text", "")).strip()
    if not btn_text:
        return f"📊 {plan.get('gb', 0)} گیگ ({format_price(data_price)})"

    raw_price_str = f"{data_price:,}"
    price_str = format_price(data_price)
    gb_str = str(plan.get("gb", 0))

    return (
        btn_text.replace("{gb}", gb_str)
        .replace("{price}", price_str)
        .replace("{raw_price}", raw_price_str)
    )


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
    config = await get_pricing_config()

    data_price = await calculate_data_price(gb)
    dur_surcharge = await calculate_duration_surcharge(duration_days)

    user_surcharge: int = config["user_surcharge"]
    extra_users = max(0, users_count - 1)
    users_total_surcharge = extra_users * user_surcharge

    return data_price + dur_surcharge + users_total_surcharge


async def get_price_breakdown(
    gb: int, duration_days: int, users_count: int
) -> dict[str, Any]:
    config = await get_pricing_config()

    data_price = await calculate_data_price(gb)
    dur_surcharge = await calculate_duration_surcharge(duration_days)
    extra_users = max(0, users_count - 1)
    user_surcharge = extra_users * config["user_surcharge"]
    total_price = data_price + dur_surcharge + user_surcharge

    return {
        "data_price": data_price,
        "duration_surcharge": dur_surcharge,
        "user_surcharge": user_surcharge,
        "total_price": total_price,
    }
