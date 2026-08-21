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

    _pricing_cache = {
        "base_gb_rate": base_rate,
        "user_surcharge": user_surcharge,
        "duration_surcharges": duration_surcharges,
        "volume_tiers": volume_tiers,
        "fallback_gb_rate": fallback_rate,
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
    invalidate_pricing_cache()


async def calculate_data_price(gb: int) -> int:
    if gb <= 0:
        return 0

    config = await get_pricing_config()
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

    duration_surcharges: dict[int, int] = config["duration_surcharges"]
    dur_surcharge = duration_surcharges.get(duration_days, 0)

    user_surcharge: int = config["user_surcharge"]
    extra_users = max(0, users_count - 1)
    users_total_surcharge = extra_users * user_surcharge

    return data_price + dur_surcharge + users_total_surcharge


async def get_price_breakdown(
    gb: int, duration_days: int, users_count: int
) -> dict[str, Any]:
    config = await get_pricing_config()

    data_price = await calculate_data_price(gb)
    dur_surcharge = config["duration_surcharges"].get(duration_days, 0)
    extra_users = max(0, users_count - 1)
    user_surcharge = extra_users * config["user_surcharge"]
    total_price = data_price + dur_surcharge + user_surcharge

    return {
        "data_price": data_price,
        "duration_surcharge": dur_surcharge,
        "user_surcharge": user_surcharge,
        "total_price": total_price,
    }
