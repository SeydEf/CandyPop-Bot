from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command

from config import BOT_NAME, SUPPORT_LINK
from db.models import get_pricing_display_config
from keyboards.reply_kb import BTN_PRICING
from services.pricing import get_pricing_config
from utils.formatting import format_price, to_persian_digits

logger = logging.getLogger(__name__)
router = Router(name="pricing")


async def build_pricing_text() -> str:
    config = await get_pricing_config()
    tiers = config["volume_tiers"]
    fallback_rate = config["fallback_gb_rate"]
    durations = config["duration_surcharges"]
    user_surcharge = config["user_surcharge"]

    tiers_text = ""
    sorted_tiers = sorted(tiers, key=lambda x: x[0])
    for i, (max_gb, rate) in enumerate(sorted_tiers):
        if i == 0:
            tiers_text += f"  • تا {to_persian_digits(max_gb)} گیگ: {format_price(rate)} به ازای هر گیگ\n"
        else:
            prev_gb = sorted_tiers[i - 1][0]
            tiers_text += f"  • از {to_persian_digits(prev_gb)} تا {to_persian_digits(max_gb)} گیگ: {format_price(rate)} به ازای هر گیگ\n"

    if sorted_tiers:
        last_gb = sorted_tiers[-1][0]
        tiers_text += f"  • بالای {to_persian_digits(last_gb)} گیگ: {format_price(fallback_rate)} به ازای هر گیگ\n"
    else:
        tiers_text += f"  • تمامی حجم‌ها: {format_price(fallback_rate)} به ازای هر گیگ\n"

    dur_60 = durations.get(60, 50000)
    dur_90 = durations.get(90, 100000)

    dur_text = (
        f" • ۱ ماهه (۳۰ روز): قیمت پایه، بدون هزینه اضافه\n"
        f" • ۲ ماهه (۶۰ روز): فقط +{format_price(dur_60)} برای ۳۰ روز بیشتر\n"
        f" • ۳ ماهه (۹۰ روز): فقط +{format_price(dur_90)} برای ۶۰ روز بیشتر\n"
    )
    user_text = (
        f" • کاربر اول: رایگان، همراه با پلن\n"
        f" • هر کاربر اضافه: فقط +{format_price(user_surcharge)}\n"
    )
    text = (
        f"💰 <b>تعرفه خدمات {BOT_NAME}</b>\n\n"
        "📊 <b>هرچه حجم بیشتر، قیمت هر گیگ کمتر!</b>\n"
        f"{tiers_text}\n"
        "⏱ <b>مدت اشتراک را انتخاب کنید</b>\n"
        f"{dur_text}\n"
        "👥 <b>تعداد کاربران همزمان</b>\n"
        f"{user_text}\n"
        "💡 <i>قیمت نهایی بر اساس حجم، مدت اشتراک و تعداد کاربران انتخابی شما هنگام سفارش محاسبه می‌شود.</i>"
    )
    return text


@router.message(Command("pricing"))
@router.message(F.text == BTN_PRICING)
async def show_pricing(message: types.Message) -> None:
    config = await get_pricing_display_config()
    if not config["enabled"]:
        disabled_text = (
            "⚠️ <b>بخش تعرفه‌ها در حال حاضر موقتاً غیرفعال می‌باشد.</b>\n\n"
            "جهت کسب اطلاعات بیشتر یا استعلام قیمت‌ها، لطفاً با پشتیبانی در ارتباط باشید:\n"
            f"👨‍💻 {SUPPORT_LINK}"
        )
        await message.answer(disabled_text, parse_mode="HTML")
        return

    mode = config.get("mode", "default")
    photo_file_id = config.get("photo_file_id")
    custom_text = config.get("custom_text")

    if mode == "photo_with_default_text" and photo_file_id:
        text = await build_pricing_text()
        await message.answer_photo(photo=photo_file_id, caption=text, parse_mode="HTML")
    elif mode == "photo_with_custom_text" and photo_file_id:
        caption = custom_text or await build_pricing_text()
        await message.answer_photo(
            photo=photo_file_id, caption=caption, parse_mode="HTML"
        )
    elif mode == "custom_text_only" and custom_text:
        await message.answer(custom_text, parse_mode="HTML")
    else:
        text = await build_pricing_text()
        await message.answer(text, parse_mode="HTML")
