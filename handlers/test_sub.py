from __future__ import annotations

import logging
import time

from aiogram import F, Router, types
from aiogram.filters import Command

from config import (
    SUB_BASE_URL,
)
from db.models import can_get_test_sub, set_test_used
from keyboards.inline_kb import sub_config_links_keyboard
from keyboards.reply_kb import BTN_TEST
from services import xui_api
from services.test_sub_config import get_test_sub_config
from utils.formatting import format_size_gb, to_persian_digits
from utils.helpers import gb_to_bytes, generate_email

logger = logging.getLogger(__name__)
router = Router(name="test_sub")


@router.message(Command("test"))
@router.message(F.text == BTN_TEST)
async def test_subscription(message: types.Message) -> None:
    if not message.from_user:
        return

    tg_id = message.from_user.id
    test_config = await get_test_sub_config()
    test_gb = test_config["gb"]
    test_duration = test_config["duration_days"]
    cooldown_days = test_config["cooldown_days"]

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
            f"هر کاربر هر {to_persian_digits(cooldown_days)} روز یک‌بار می‌تواند اشتراک تست دریافت کند.\n\n"
            f"⏱ <b>زمان باقیمانده تا دریافت بعدی:</b> {time_text}",
            parse_mode="HTML",
        )
        return

    creating_message = await message.answer(
        "⏳ در حال ساخت اشتراک تست...", parse_mode="HTML"
    )

    try:
        username = message.from_user.username
        email = generate_email(tg_id, username)
        total_bytes = gb_to_bytes(test_gb)
        expiry_ms = int((time.time() + test_duration * 86400) * 1000)

        from db.models import get_active_inbound_ids

        active_inbound_ids = await get_active_inbound_ids()

        await xui_api.add_client(
            email=email,
            total_gb=total_bytes,
            expiry_time=expiry_ms,
            tg_id=tg_id,
            inbound_ids=active_inbound_ids,
        )

        client = await xui_api.get_client(email)
        sub_id = client.get("subId", "") if client else ""

        await set_test_used(tg_id)

        sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

        text = (
            f"🎉 <b>تبریک! اشتراک شما با موفقیت فعال شد</b>\n\n"
            f"🔹 <b>شناسه سرویس:</b> <code>{email}</code>\n"
            f"⏱ <b>مدت اعتبار:</b> {test_duration} روز\n"
            f"👥 <b>تعداد کاربر همزمان:</b> {to_persian_digits(1)} کاربر\n"
            f"📊 <b>حجم اشتراک:</b> {format_size_gb(test_gb)}\n\n"
            f"🔗 <b>لینک اتصال ساب‌اسکریپشن:</b>\n<code>{sub_link}</code>\n\n"
            f"💡 <i>کافیه لینک بالا یا بارکد رو توی برنامه مورد نظرتون کپی و وارد کنید.</i>"
        )

        if sub_id:
            from aiogram.types import BufferedInputFile
            from utils.helpers import generate_qr

            qr_buf = generate_qr(sub_link)
            photo = BufferedInputFile(qr_buf.getvalue(), filename="qrcode.png")
            await message.answer_photo(
                photo=photo,
                caption=text,
                reply_markup=sub_config_links_keyboard(email),
                parse_mode="HTML",
            )
        else:
            await message.answer(
                text,
                reply_markup=sub_config_links_keyboard(email),
                parse_mode="HTML",
            )

    except Exception as e:
        logger.exception("Failed to create test subscription for user %d", tg_id)
        await message.answer(
            f"❌ خطا در ساخت اشتراک تست.\nلطفاً بعداً دوباره تلاش کنید.\n\nخطا: {e}",
            parse_mode="HTML",
        )

    await creating_message.delete()
