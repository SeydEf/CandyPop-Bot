"""
Test Subscription handler.

Allows each user to claim one free test subscription (limited data & duration).
"""

from __future__ import annotations

import logging
import time

from aiogram import F, Router, types

from config import (
    INBOUND_IDS,
    SUB_BASE_URL,
    TEST_COOLDOWN_DAYS,
    TEST_DATA_GB,
    TEST_DURATION_DAYS,
)
from db.models import can_get_test_sub, set_test_used
from keyboards.inline_kb import sub_config_links_keyboard
from keyboards.reply_kb import BTN_TEST
from services import xui_api
from utils.formatting import format_size_gb, to_persian_digits
from utils.helpers import gb_to_bytes, generate_email

logger = logging.getLogger(__name__)
router = Router(name="test_sub")


@router.message(F.text == BTN_TEST)
async def test_subscription(message: types.Message) -> None:
    """Handle test subscription request."""
    if not message.from_user:
        return

    tg_id = message.from_user.id

    # Check cooldown / test usage
    can_claim, rem_days, rem_hours = await can_get_test_sub(tg_id)
    if not can_claim:
        time_parts = []
        if rem_days > 0:
            time_parts.append(f"{to_persian_digits(rem_days)} روز")
        if rem_hours > 0 or rem_days == 0:
            time_parts.append(f"{to_persian_digits(rem_hours)} ساعت")
        time_text = " و ".join(time_parts)

        await message.answer(
            f"❌ <b>امکان دریافت اشتراک تست وجود ندارد.</b>\n\n"
            f"هر کاربر هر {to_persian_digits(TEST_COOLDOWN_DAYS)} روز یک‌بار می‌تواند اشتراک تست دریافت کند.\n\n"
            f"⏱ <b>زمان باقیمانده تا دریافت بعدی:</b> {time_text}",
            parse_mode="HTML",
        )
        return

    await message.answer("⏳ در حال ساخت اشتراک تست...", parse_mode="HTML")

    try:
        username = message.from_user.username
        email = generate_email(tg_id, username, test=True)
        total_bytes = gb_to_bytes(TEST_DATA_GB)
        expiry_ms = int((time.time() + TEST_DURATION_DAYS * 86400) * 1000)

        # Create client in X-UI
        await xui_api.add_client(
            email=email,
            total_gb=total_bytes,
            expiry_time=expiry_ms,
            tg_id=tg_id,
            inbound_ids=INBOUND_IDS,
        )

        # Get subId
        client = await xui_api.get_client(email)
        sub_id = client.get("subId", "") if client else ""

        # Mark test as used
        await set_test_used(tg_id)

        sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

        await message.answer(
            f"🎁 <b>اشتراک تست شما فعال شد!</b>\n\n"
            f"📦 نام سرویس: {email}\n"
            f"⏱ مدت: {TEST_DURATION_DAYS} روز\n"
            f"👤 تعداد کاربر: {to_persian_digits(1)} کاربر\n"
            f"📊 حجم: {format_size_gb(TEST_DATA_GB)}\n\n"
            f"🔗 لینک اشتراک:\n<code>{sub_link}</code>",
            reply_markup=sub_config_links_keyboard(email),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception("Failed to create test subscription for user %d", tg_id)
        await message.answer(
            f"❌ خطا در ساخت اشتراک تست.\nلطفاً بعداً دوباره تلاش کنید.\n\nخطا: {e}",
            parse_mode="HTML",
        )
