"""
Test Subscription handler.

Allows each user to claim one free test subscription (limited data & duration).
"""

from __future__ import annotations

import logging
import time

from aiogram import F, Router, types

from config import INBOUND_IDS, SUB_BASE_URL, TEST_DATA_GB, TEST_DURATION_DAYS
from db.models import create_subscription, is_test_used, set_test_used
from keyboards.reply_kb import BTN_TEST
from services import xui_api
from utils.formatting import format_size_gb
from utils.helpers import gb_to_bytes, generate_email

logger = logging.getLogger(__name__)
router = Router(name="test_sub")


@router.message(F.text == BTN_TEST)
async def test_subscription(message: types.Message) -> None:
    """Handle test subscription request."""
    if not message.from_user:
        return

    tg_id = message.from_user.id

    # Check if already used
    if await is_test_used(tg_id):
        await message.answer(
            "❌ <b>شما قبلاً از اشتراک تست استفاده کرده‌اید.</b>\n\n"
            "هر کاربر فقط یک بار می‌تواند اشتراک تست دریافت کند.",
            parse_mode="HTML",
        )
        return

    await message.answer("⏳ در حال ساخت اشتراک تست...", parse_mode="HTML")

    try:
        username = message.from_user.username
        email = generate_email(tg_id, username)
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

        # Save locally
        await create_subscription(
            tg_id=tg_id,
            email=email,
            sub_id=sub_id,
            service_name=email,
            data_gb=TEST_DATA_GB,
            duration_days=TEST_DURATION_DAYS,
            is_test=True,
        )
        await set_test_used(tg_id)

        sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

        # Get config links
        config_links = await xui_api.get_client_links(email)
        links_text = ""
        if config_links:
            links_text = "\n\n🔗 <b>لینک‌های کانفیگ:</b>\n"
            for link in config_links:
                links_text += f"<code>{link}</code>\n\n"

        await message.answer(
            f"🎁 <b>اشتراک تست شما فعال شد!</b>\n\n"
            f"📦 نام سرویس: {email}\n"
            f"📊 حجم: {format_size_gb(TEST_DATA_GB)}\n"
            f"⏱ مدت: {TEST_DURATION_DAYS} روز\n\n"
            f"🔗 لینک اشتراک:\n<code>{sub_link}</code>"
            f"{links_text}",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception("Failed to create test subscription for user %d", tg_id)
        await message.answer(
            f"❌ خطا در ساخت اشتراک تست.\nلطفاً بعداً دوباره تلاش کنید.\n\nخطا: {e}",
            parse_mode="HTML",
        )
