"""
Dynamic configuration for Test Subscriptions.
Stored in SQLite settings table and cached in memory.
"""

from __future__ import annotations

import logging
from typing import Any

from config import TEST_COOLDOWN_DAYS, TEST_DATA_GB, TEST_DURATION_DAYS
from db.models import get_setting, set_setting

logger = logging.getLogger(__name__)

_test_sub_cache: dict[str, Any] | None = None


async def get_test_sub_config() -> dict[str, Any]:
    """Get active test subscription config."""
    global _test_sub_cache
    if _test_sub_cache is not None:
        return _test_sub_cache

    return await load_test_sub_config()


async def load_test_sub_config() -> dict[str, Any]:
    """Load test subscription config from database."""
    global _test_sub_cache

    gb_str = await get_setting("test_sub_gb")
    try:
        gb = float(gb_str) if gb_str else TEST_DATA_GB
    except ValueError:
        gb = TEST_DATA_GB

    dur_str = await get_setting("test_sub_duration_days")
    try:
        dur = int(dur_str) if dur_str else TEST_DURATION_DAYS
    except ValueError:
        dur = TEST_DURATION_DAYS

    cool_str = await get_setting("test_sub_cooldown_days")
    try:
        cool = int(cool_str) if cool_str else TEST_COOLDOWN_DAYS
    except ValueError:
        cool = TEST_COOLDOWN_DAYS

    _test_sub_cache = {
        "gb": gb,
        "duration_days": dur,
        "cooldown_days": cool,
    }
    return _test_sub_cache


def invalidate_test_sub_cache() -> None:
    """Invalidate memory cache."""
    global _test_sub_cache
    _test_sub_cache = None


async def update_test_sub_config(
    gb: float | None = None,
    duration_days: int | None = None,
    cooldown_days: int | None = None,
) -> None:
    """Update test subscription configuration."""
    if gb is not None:
        await set_setting("test_sub_gb", str(gb))
    if duration_days is not None:
        await set_setting("test_sub_duration_days", str(duration_days))
    if cooldown_days is not None:
        await set_setting("test_sub_cooldown_days", str(cooldown_days))

    invalidate_test_sub_cache()
