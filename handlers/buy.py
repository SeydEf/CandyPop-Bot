from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    ADMIN_CHAT_ID,
    INVOICE_EXPIRY_MINUTES,
    SUB_BASE_URL,
)
from db.discounts import calculate_discount_amount, validate_discount_code
from db.models import (
    create_invoice,
    debit_wallet,
    get_balance,
    get_card_config,
    get_invoice,
    get_sub_final_attachment,
    set_invoice_message_id,
    set_invoice_receipt,
    update_invoice_status,
)
from keyboards.inline_kb import (
    admin_payment_review_keyboard,
    card_payment_keyboard,
    duration_keyboard,
    payment_method_keyboard,
    sub_config_links_keyboard,
    users_keyboard,
    volume_keyboard,
    wallet_confirm_keyboard,
)
from keyboards.reply_kb import BTN_BUY, main_menu_keyboard
from services import xui_api
from services.pricing import (
    format_volume_label,
    get_price_breakdown,
    get_pricing_config,
    is_custom_duration_allowed_for_volume,
    is_duration_allowed_for_volume,
    is_unlimited_volume,
)
from utils.formatting import (
    format_detailed_traffic,
    format_price,
    format_remaining_days,
    format_remaining_time,
    format_size_persian_gb,
    persian_to_english_digits,
    to_persian_digits,
)
from utils.helpers import (
    gb_to_bytes,
    generate_email,
)

logger = logging.getLogger(__name__)
router = Router(name="buy")


class BuyStates(StatesGroup):
    waiting_custom_gb = State()
    waiting_custom_days = State()
    waiting_discount_code = State()
    waiting_receipt = State()


async def _get_volume_step_text() -> str:
    return (
        "🚀 <b>گام ۱ از ۳: انتخاب حجم ترافیک</b>\n\n"
        "حجم مورد نظر خود را از گزینه‌های زیر انتخاب کرده یا حجم دلخواه ارسال نمایید:"
    )


@router.message(Command("buy"))
@router.message(F.text == BTN_BUY)
async def buy_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()

    from db.models import get_shop_status

    shop_status = await get_shop_status()
    if not shop_status["purchases_enabled"]:
        await message.answer(
            "⛔️ <b>فروش اشتراک جدید موقتاً غیرفعال است.</b>",
            parse_mode="HTML",
        )
        return

    text = await _get_volume_step_text()
    await message.answer(
        text,
        reply_markup=await volume_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "buy_back_volume")
async def buy_back_to_volume(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = await _get_volume_step_text()
    await callback.message.edit_text(
        text,
        reply_markup=await volume_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "buy_start")
async def buy_start_callback(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    from db.models import get_shop_status

    shop_status = await get_shop_status()
    if not shop_status["purchases_enabled"]:
        await callback.answer("⛔️ فروش اشتراک جدید موقتاً غیرفعال است.", show_alert=True)
        return

    text = await _get_volume_step_text()
    await callback.message.edit_text(
        text,
        reply_markup=await volume_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


async def _get_users_step_text(gb: int, users: int = 1) -> str:
    from services.pricing import calculate_data_price

    config = await get_pricing_config()
    user_surcharge_unit = config["user_surcharge"]
    data_price = await calculate_data_price(gb)
    extra_users = max(0, users - 1)
    users_surcharge_total = extra_users * user_surcharge_unit
    running_total = data_price + users_surcharge_total
    vol_label = await format_volume_label(gb)

    breakdown_lines = [f"• هزینه حجم: {format_price(data_price)}"]
    if users_surcharge_total > 0:
        breakdown_lines.append(
            f"• کاربر اضافه ({to_persian_digits(extra_users)} کاربر): +{format_price(users_surcharge_total)}"
        )
    breakdown_str = "\n".join(breakdown_lines)

    return (
        f"👥 <b>گام ۲ از ۳: ظرفیت کاربر همزمان</b>\n\n"
        f"📦 <b>مشخصات انتخابی:</b>\n"
        f"• حجم ترافیک: <b>{vol_label}</b>\n"
        f"• ظرفیت اتصال: <b>{to_persian_digits(users)} کاربر</b>\n\n"
        f"💳 <b>ریز محاسبات:</b>\n"
        f"{breakdown_str}\n"
        f"──────────────\n"
        f"💵 <b>جمع تا این مرحله:</b> <b>{format_price(running_total)}</b>\n\n"
        f"💡 <i>به ازای هر کاربر اضافه، مبلغ +{format_price(user_surcharge_unit)} به اشتراک افزوده می‌شود.</i>"
    )


@router.callback_query(F.data.regexp(r"^buy_vol_\d+$"))
async def buy_select_volume(callback: types.CallbackQuery) -> None:
    gb = int(callback.data.split("_")[-1])
    from services.pricing import resolve_plan_user_config

    user_cfg = await resolve_plan_user_config(gb)

    if not user_cfg["allow_selection"]:
        users = user_cfg["users"]
        text = await _get_duration_step_text(gb, users)
        await callback.message.edit_text(
            text,
            reply_markup=await duration_keyboard(gb, users, skip_users_step=True),
            parse_mode="HTML",
        )
    else:
        users = 1
        text = await _get_users_step_text(gb, users)
        await callback.message.edit_text(
            text,
            reply_markup=users_keyboard(gb, users),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "buy_vol_custom")
async def buy_custom_volume(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BuyStates.waiting_custom_gb)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="buy_back_volume"
                )
            ]
        ]
    )
    from services.pricing import get_custom_volume_range

    min_gb, max_gb = await get_custom_volume_range()

    await callback.message.edit_text(
        "✍️ <b>ورود حجم دلخواه:</b>\n\n"
        "میزان حجم مورد نظر را به گیگابایت (عدد لاتین یا فارسی) ارسال کنید.\n"
        f"🔸 <b>حداقل حجم:</b> <code>{to_persian_digits(min_gb)}</code> گیگابایت\n"
        f"🔸 <b>حداکثر حجم:</b> <code>{to_persian_digits(max_gb)}</code> گیگابایت\n"
        "🔸 <b>مثال:</b> <code>25</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BuyStates.waiting_custom_gb, F.text)
async def buy_custom_volume_input(message: types.Message, state: FSMContext) -> None:
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(
            "🚫 فرایند خرید لغو شد.",
            reply_markup=main_menu_keyboard(),
        )
        return

    try:
        raw_text = (
            persian_to_english_digits(message.text.strip()) if message.text else ""
        )
        clean_text = raw_text.replace(",", "").replace("،", "").replace(" ", "")
        gb = int(clean_text)
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="buy_back_volume"
                    )
                ]
            ]
        )
        from services.pricing import get_custom_volume_range

        min_gb, max_gb = await get_custom_volume_range()

        if gb < min_gb:
            await message.answer(
                f"⚠️ <b>حداقل حجم قابل سفارش {to_persian_digits(min_gb)} گیگابایت است.</b>\n"
                f"لطفاً عددی معادل {to_persian_digits(min_gb)} گیگابایت یا بیشتر وارد کنید.\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )
            return
        if gb > max_gb:
            await message.answer(
                f"⚠️ <b>حداکثر حجم قابل سفارش {to_persian_digits(max_gb)} گیگابایت است.</b>\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )
            return
    except (ValueError, TypeError):
        from services.pricing import get_custom_volume_range

        min_gb, _ = await get_custom_volume_range()
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="buy_back_volume"
                    )
                ]
            ]
        )
        await message.answer(
            f"⚠️ لطفاً فقط یک عدد معتبر ارسال کنید (حداقل {to_persian_digits(min_gb)} گیگابایت).\n🔸 مثال: <code>25</code>\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=cancel_kb,
            parse_mode="HTML",
        )
        return

    await state.update_data(gb=gb)
    data = await state.get_data()
    renew_email = data.get("renew_email")

    from services.pricing import resolve_plan_user_config

    user_cfg = await resolve_plan_user_config(gb)

    if renew_email:
        email = renew_email
        if not user_cfg["allow_selection"]:
            users = user_cfg["users"]
            await state.update_data(users=users)
            from keyboards.inline_kb import renew_duration_keyboard

            text = await _get_duration_step_text(gb, users)
            await message.answer(
                f"🔄 <b>تغییر پلن سرویس «{email}»</b>\n\n{text}",
                reply_markup=await renew_duration_keyboard(
                    gb, users, skip_users_step=True
                ),
                parse_mode="HTML",
            )
        else:
            users = data.get("users", 1)
            text = await _get_users_step_text(gb, users)
            from keyboards.inline_kb import renew_users_keyboard

            await message.answer(
                f"🔄 <b>تغییر پلن سرویس «{email}»</b>\n\n{text}",
                reply_markup=renew_users_keyboard(gb, users),
                parse_mode="HTML",
            )
    else:
        if not user_cfg["allow_selection"]:
            users = user_cfg["users"]
            await state.update_data(users=users)
            text = await _get_duration_step_text(gb, users)
            await message.answer(
                text,
                reply_markup=await duration_keyboard(gb, users, skip_users_step=True),
                parse_mode="HTML",
            )
        else:
            users = 1
            text = await _get_users_step_text(gb, users)
            await message.answer(
                text,
                reply_markup=users_keyboard(gb, users),
                parse_mode="HTML",
            )


@router.callback_query(F.data.regexp(r"^buy_users_step_\d+_\d+$"))
async def buy_users_step(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("_")
    gb = int(parts[3])
    users = int(parts[4])
    text = await _get_users_step_text(gb, users)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=users_keyboard(gb, users),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.edit_reply_markup(reply_markup=users_keyboard(gb, users))

    await callback.answer()


@router.callback_query(F.data == "buy_noop")
async def buy_noop(callback: types.CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_back_users_\d+_\d+$"))
async def buy_back_to_users(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parts = callback.data.split("_")
    gb = int(parts[3])
    users = int(parts[4])

    from services.pricing import resolve_plan_user_config

    user_cfg = await resolve_plan_user_config(gb)
    if not user_cfg["allow_selection"]:
        from keyboards.inline_kb import volume_keyboard

        await callback.message.edit_text(
            "📦 <b>گام ۱ از ۲: انتخاب حجم ترافیک</b>\n\nلطفاً یکی از پلن‌های حجمی زیر را انتخاب کنید:",
            reply_markup=await volume_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    text = await _get_users_step_text(gb, users)
    await callback.message.edit_text(
        text,
        reply_markup=users_keyboard(gb, users),
        parse_mode="HTML",
    )
    await callback.answer()


async def _get_duration_step_text(gb: int, users: int) -> str:
    from services.pricing import calculate_data_price, resolve_plan_user_config

    config = await get_pricing_config()
    user_cfg = await resolve_plan_user_config(gb)
    data_price = await calculate_data_price(gb)
    if not user_cfg["allow_selection"] and user_cfg.get("is_manual_surcharge"):
        users_surcharge_total = user_cfg["user_surcharge"]
    else:
        extra_users = max(0, users - 1)
        users_surcharge_total = extra_users * config["user_surcharge"]
    base_sum = data_price + users_surcharge_total
    vol_label = await format_volume_label(gb)

    step_title = (
        "⏱ <b>گام ۲ از ۲: انتخاب مدت اعتبار</b>"
        if not user_cfg["allow_selection"]
        else "⏱ <b>گام ۳ از ۳: انتخاب مدت اعتبار</b>"
    )

    breakdown_lines = [f"• هزینه حجم: {format_price(data_price)}"]
    if users_surcharge_total > 0:
        extra_users = max(0, users - 1)
        if extra_users > 0:
            breakdown_lines.append(
                f"• کاربر اضافه ({to_persian_digits(extra_users)} کاربر): +{format_price(users_surcharge_total)}"
            )
        else:
            breakdown_lines.append(
                f"• هزینه ظرفیت اتصال ({to_persian_digits(users)} کاربر): +{format_price(users_surcharge_total)}"
            )
    breakdown_str = "\n".join(breakdown_lines)

    return (
        f"{step_title}\n\n"
        f"📦 <b>مشخصات انتخابی:</b>\n"
        f"• حجم ترافیک: <b>{vol_label}</b>\n"
        f"• ظرفیت اتصال: <b>{to_persian_digits(users)} کاربر</b>\n\n"
        f"💳 <b>ریز محاسبات پایه:</b>\n"
        f"{breakdown_str}\n"
        f"──────────────\n"
        f"💵 <b>مجموع پایه:</b> <b>{format_price(base_sum)}</b>\n\n"
        f"👇 مدت اعتبار مورد نظر خود را از دکمه‌های زیر انتخاب کنید:"
    )


async def _build_pre_invoice_text(
    gb: int,
    duration: int,
    users: int,
    final_price: int,
    original_price: int | None = None,
    discount_code: str | None = None,
    discount_label: str = "",
    discount_amount: int = 0,
    title: str = "📋 <b>پیش‌فاکتور خرید اشتراک</b>",
) -> str:
    bd = await get_price_breakdown(gb, duration, users)
    vol_label = await format_volume_label(gb, with_fair_policy=True)
    extra_users = max(0, users - 1)

    breakdown_lines = [f"• هزینه حجم: {format_price(bd['data_price'])}"]
    if bd["user_surcharge"] > 0:
        if extra_users > 0:
            breakdown_lines.append(
                f"• کاربر اضافه ({to_persian_digits(extra_users)} کاربر): +{format_price(bd['user_surcharge'])}"
            )
        else:
            breakdown_lines.append(
                f"• هزینه ظرفیت اتصال ({to_persian_digits(users)} کاربر): +{format_price(bd['user_surcharge'])}"
            )
    if bd["duration_surcharge"] > 0:
        breakdown_lines.append(
            f"• افزایش مدت ({to_persian_digits(duration)} روز): +{format_price(bd['duration_surcharge'])}"
        )
    if discount_amount > 0:
        lbl = (
            f" ({discount_code} | {discount_label})"
            if discount_code and discount_label
            else (f" ({discount_code})" if discount_code else "")
        )
        breakdown_lines.append(f"• تخفیف{lbl}: -{format_price(discount_amount)}")

    breakdown_str = "\n".join(breakdown_lines)

    return (
        f"{title}\n\n"
        f"📦 <b>مشخصات سرویس:</b>\n"
        f"• حجم ترافیک: <b>{vol_label}</b>\n"
        f"• ظرفیت اتصال: <b>{to_persian_digits(users)} کاربر</b>\n"
        f"• مدت اعتبار: <b>{to_persian_digits(duration)} روز</b>\n\n"
        f"💳 <b>ریز محاسبات:</b>\n"
        f"{breakdown_str}\n"
        f"──────────────\n"
        f"💎 <b>مبلغ قابل پرداخت:</b> <b>{format_price(final_price)}</b>\n\n"
        f"💳 روش پرداخت مورد نظر خود را انتخاب کنید:"
    )


@router.callback_query(F.data.regexp(r"^buy_users_confirm_\d+_\d+$"))
async def buy_users_confirm(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("_")
    gb = int(parts[3])
    users = int(parts[4])
    text = await _get_duration_step_text(gb, users)
    await callback.message.edit_text(
        text,
        reply_markup=await duration_keyboard(gb, users),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_back_duration_\d+_\d+$"))
async def buy_back_to_duration(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.clear()
    parts = callback.data.split("_")
    gb = int(parts[3])
    users = int(parts[4])
    from services.pricing import resolve_plan_user_config

    user_cfg = await resolve_plan_user_config(gb)
    skip = not user_cfg["allow_selection"]
    text = await _get_duration_step_text(gb, users)
    await callback.message.edit_text(
        text,
        reply_markup=await duration_keyboard(gb, users, skip_users_step=skip),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_dur_custom_\d+_\d+$"))
async def buy_custom_duration_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    parts = callback.data.split("_")
    gb = int(parts[3])
    users = int(parts[4])
    if not await is_custom_duration_allowed_for_volume(gb, for_renew=False):
        await callback.answer(
            "⚠️ مدت زمان دلخواه برای این پلن فعال نیست.", show_alert=True
        )
        return
    await state.set_state(BuyStates.waiting_custom_days)
    await state.update_data(custom_dur_gb=gb, custom_dur_users=users)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"buy_back_duration_{gb}_{users}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        "✍️ <b>مدت‌زمان دلخواه را وارد کنید:</b>\n\n"
        "تعداد روز اشتراک را به صورت عددی ارسال کنید.\n"
        "🔸 <b>حداقل مدت:</b> <code>30</code> روز\n"
        "🔸 <b>حداکثر مدت:</b> <code>365</code> روز (۱ سال)\n"
        "🔸 <b>مثال:</b> <code>45</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BuyStates.waiting_custom_days, F.text)
async def buy_custom_duration_input(message: types.Message, state: FSMContext) -> None:
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(
            "🚫 فرایند خرید لغو شد.",
            reply_markup=main_menu_keyboard(),
        )
        return

    data = await state.get_data()
    gb = data.get("custom_dur_gb", 30)
    users = data.get("custom_dur_users", 1)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"buy_back_duration_{gb}_{users}",
                )
            ]
        ]
    )

    try:
        raw_text = (
            persian_to_english_digits(message.text.strip()) if message.text else ""
        )
        clean_text = raw_text.replace(",", "").replace("،", "").replace(" ", "")
        days = int(clean_text)
        if days < 30:
            await message.answer(
                "⚠️ <b>حداقل مدت زمان قابل سفارش ۳۰ روز است.</b>\n"
                "لطفاً عددی معادل ۳۰ روز یا بیشتر وارد کنید.\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )
            return
        if days > 365:
            await message.answer(
                "⚠️ <b>حداکثر مدت زمان قابل سفارش ۳۶۵ روز (۱ سال) است.</b>\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )
            return
    except (ValueError, TypeError):
        await message.answer(
            "❌ لطفاً یک عدد معتبر ارسال کنید.\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=cancel_kb,
            parse_mode="HTML",
        )
        return

    await state.clear()
    bd = await get_price_breakdown(gb, days, users)
    price = bd["total_price"]

    text = await _build_pre_invoice_text(
        gb=gb, duration=days, users=users, final_price=price
    )
    card_cfg = await get_card_config()
    await message.answer(
        text,
        reply_markup=payment_method_keyboard(
            days, users, gb, price, card_enabled=card_cfg.get("enabled", True)
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.regexp(r"^buy_dur_\d+_\d+_\d+$"))
async def buy_select_duration(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("_")
    gb = int(parts[2])
    users = int(parts[3])
    duration = int(parts[4])

    if not await is_duration_allowed_for_volume(gb, duration, for_renew=False):
        await callback.answer("⚠️ این مدت زمان برای این پلن فعال نیست.", show_alert=True)
        return

    bd = await get_price_breakdown(gb, duration, users)
    price = bd["total_price"]

    text = await _build_pre_invoice_text(
        gb=gb, duration=duration, users=users, final_price=price
    )
    card_cfg = await get_card_config()
    await callback.message.edit_text(
        text,
        reply_markup=payment_method_keyboard(
            duration, users, gb, price, card_enabled=card_cfg.get("enabled", True)
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_pay_wallet_\d+_\d+_\d+_\d+$"))
async def buy_wallet_payment(callback: types.CallbackQuery) -> None:
    if not callback.from_user:
        return
    parts = callback.data.split("_")
    duration = int(parts[3])
    users = int(parts[4])
    gb = int(parts[5])
    price = int(parts[6])

    balance = await get_balance(callback.from_user.id)

    if balance < price:
        await callback.answer(
            f"❌ موجودی کیف پول شما برای این سفارش کافی نیست!\n\n"
            f"💰 موجودی فعلی: {format_price(balance)}\n"
            f"💵 مبلغ مورد نیاز: {format_price(price)}",
            show_alert=True,
        )
        return

    bd = await get_price_breakdown(gb, duration, users)
    after_balance = balance - price
    vol_label = await format_volume_label(gb, with_fair_policy=True)
    extra_users = max(0, users - 1)

    breakdown_lines = [f"• هزینه حجم: {format_price(bd['data_price'])}"]
    if bd["user_surcharge"] > 0:
        breakdown_lines.append(
            f"• کاربر اضافه ({to_persian_digits(extra_users)} کاربر): +{format_price(bd['user_surcharge'])}"
        )
    if bd["duration_surcharge"] > 0:
        breakdown_lines.append(
            f"• افزایش مدت ({to_persian_digits(duration)} روز): +{format_price(bd['duration_surcharge'])}"
        )

    breakdown_str = "\n".join(breakdown_lines)

    text = (
        f"👛 <b>تأیید پرداخت از کیف پول</b>\n\n"
        f"📦 <b>مشخصات سرویس:</b>\n"
        f"• حجم ترافیک: <b>{vol_label}</b>\n"
        f"• ظرفیت اتصال: <b>{to_persian_digits(users)} کاربر</b>\n"
        f"• مدت اعتبار: <b>{to_persian_digits(duration)} روز</b>\n\n"
        f"💳 <b>ریز محاسبات:</b>\n"
        f"{breakdown_str}\n"
        f"──────────────\n"
        f"💎 <b>مبلغ کسر از کیف پول:</b> <b>{format_price(price)}</b>\n"
        f"💰 <b>موجودی فعلی حساب:</b> {format_price(balance)}\n"
        f"📉 <b>موجودی پس از پرداخت:</b> {format_price(after_balance)}\n\n"
        f"آیا از ثبت سفارش و دریافت سرویس اطمینان دارید؟"
    )
    await callback.message.edit_text(
        text,
        reply_markup=wallet_confirm_keyboard(duration, users, gb, price),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_wallet_confirm_\d+_\d+_\d+_\d+$"))
async def buy_wallet_confirm(
    callback: types.CallbackQuery, bot: Bot, state: FSMContext
) -> None:
    if not callback.from_user:
        return
    parts = callback.data.split("_")
    duration = int(parts[3])
    users = int(parts[4])
    gb = int(parts[5])
    price = int(parts[6])
    tg_id = callback.from_user.id

    data = await state.get_data()
    discount_code = data.get("discount_code")
    original_price = data.get("original_price", price)

    try:
        new_balance = await debit_wallet(tg_id, price)
    except ValueError:
        await callback.answer("❌ موجودی کیف پول شما کافی نیست.", show_alert=True)
        return

    invoice = await create_invoice(
        tg_id=tg_id,
        amount=price,
        duration_days=duration,
        data_gb=gb,
        users_count=users,
        payment_method="wallet",
        discount_code=discount_code,
        original_amount=original_price,
    )
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()
    await update_invoice_status(
        invoice["id"],
        "approved",
        processed_at=now_iso,
        processed_by_name="کیف پول (خودکار)",
    )

    if discount_code:
        from db.discounts import increment_discount_usage

        await increment_discount_usage(discount_code, tg_id)

    from db.models import process_referral_commission

    await process_referral_commission(tg_id, price, bot)

    await callback.message.edit_text(
        "🚀 <b>در حال آماده‌سازی کانفیگ اختصاصی شما... لطفاً چند ثانیه صبر کنید.</b>",
        parse_mode="HTML",
    )

    try:
        username = callback.from_user.username
        email = generate_email(tg_id, username)
        total_bytes = gb_to_bytes(gb)

        from db.models import (
            get_active_client_group,
            get_active_inbound_ids,
            get_start_first_use_config,
        )

        start_first_use = await get_start_first_use_config()
        if start_first_use and duration > 0:
            expiry_ms = -int(duration * 86400 * 1000)
        else:
            expiry_ms = (
                int((time.time() + duration * 86400) * 1000) if duration > 0 else 0
            )

        active_group = await get_active_client_group()

        active_inbound_ids = await get_active_inbound_ids()

        await xui_api.add_client(
            email=email,
            total_gb=total_bytes,
            expiry_time=expiry_ms,
            tg_id=tg_id,
            inbound_ids=active_inbound_ids,
            limit_ip=users,
            group=active_group,
        )

        client = await xui_api.get_client(email)
        sub_id = client.get("subId", "") if client else ""

        sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

        success_text = (
            f"🎉 <b>تبریک! اشتراک شما با موفقیت فعال شد</b>\n\n"
            f"🔹 <b>شناسه سرویس:</b> <code>{email}</code>\n"
            f"⏱ <b>مدت اعتبار:</b> {duration} روز\n"
            f"👥 <b>تعداد کاربر همزمان:</b> {to_persian_digits(users)} کاربر\n"
            f"📊 <b>حجم اشتراک:</b> {await format_volume_label(gb)}\n"
            f"💳 <b>روش پرداخت:</b> کیف پول حساب\n"
            f"👛 <b>موجودی باقیمانده:</b> {format_price(new_balance)}\n\n"
            f"🔗 <b>لینک هوشمند اشتراک:</b>\n<code>{sub_link}</code>\n\n"
            f"💡 <i>برای اتصال، لینک بالا را کپی کرده یا کد QR را در نرم‌افزار خود اسکن کنید.</i>"
        )
        final_attachment = await get_sub_final_attachment()
        if final_attachment:
            success_text += final_attachment

        await state.clear()
        if sub_id:
            from aiogram.types import BufferedInputFile

            from utils.helpers import generate_qr

            qr_buf = generate_qr(sub_link)
            photo = BufferedInputFile(qr_buf.getvalue(), filename="qrcode.png")
            try:
                await callback.message.delete()
            except Exception:
                pass
            if len(success_text) <= 1024:
                await callback.message.answer_photo(
                    photo=photo,
                    caption=success_text,
                    reply_markup=sub_config_links_keyboard(email),
                    parse_mode="HTML",
                )
            else:
                await callback.message.answer_photo(photo=photo)
                await callback.message.answer(
                    success_text,
                    reply_markup=sub_config_links_keyboard(email),
                    parse_mode="HTML",
                )
        else:
            await callback.message.edit_text(
                success_text,
                reply_markup=sub_config_links_keyboard(email),
                parse_mode="HTML",
            )

    except Exception as e:
        logger.exception("Failed to create client for user %d", tg_id)
        from db.models import credit_wallet

        await credit_wallet(tg_id, price)
        await callback.message.edit_text(
            f"⚠️ <b>خطا در راه‌اندازی اشتراک:</b> مبلغ پرداختی به کیف پول شما بازگشت داده شد.\nعلت خطا: {e}",
            parse_mode="HTML",
        )

    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_discount_apply_\d+_\d+_\d+_\d+$"))
async def buy_discount_apply_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    parts = callback.data.split("_")
    duration = int(parts[3])
    users = int(parts[4])
    gb = int(parts[5])
    price = int(parts[6])

    await state.set_state(BuyStates.waiting_discount_code)
    await state.update_data(
        duration=duration,
        users=users,
        gb=gb,
        original_price=price,
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"buy_discount_cancel_{duration}_{users}_{gb}_{price}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        "🏷️ <b>کد تخفیف دارید؟</b>\n\n"
        "کد تخفیف خود را ارسال کنید تا روی مبلغ سفارش اعمال شود:\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BuyStates.waiting_discount_code, F.text)
async def buy_discount_process(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        return

    data = await state.get_data()
    duration = data.get("duration", 30)
    users = data.get("users", 1)
    gb = data.get("gb", 10)
    original_price = data.get("original_price", 0)

    if message.text.strip() == "/cancel":
        await state.clear()
        text = await _build_pre_invoice_text(
            gb=gb, duration=duration, users=users, final_price=original_price
        )
        card_cfg = await get_card_config()
        await message.answer(
            text,
            reply_markup=payment_method_keyboard(
                duration,
                users,
                gb,
                original_price,
                card_enabled=card_cfg.get("enabled", True),
            ),
            parse_mode="HTML",
        )
        return

    code = message.text.strip()

    order_context = {
        "data_gb": gb,
        "duration_days": duration,
        "users_count": users,
        "original_price": original_price,
        "is_renewal": False,
    }
    is_valid, err_msg, dc = await validate_discount_code(
        code, message.from_user.id, order_context=order_context
    )

    if not is_valid or not dc:
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"buy_discount_cancel_{duration}_{users}_{gb}_{original_price}",
                    )
                ]
            ]
        )
        await message.answer(
            f"⚠️ <b>{err_msg}</b>\n\n"
            "لطفاً کد تخفیف را مجدداً و با دقت وارد کنید.\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=cancel_kb,
            parse_mode="HTML",
        )
        return

    discount_amount, final_price = calculate_discount_amount(dc, original_price)
    clean_code = dc["code"]
    rules = dc.get("rules") or {}
    disc_type = rules.get("discount_type", "percent")
    if disc_type == "fixed":
        disc_label = f"{format_price(rules.get('amount', 0))} تخفیف نقدی"
    else:
        percent = rules.get("amount") or dc["discount_percent"]
        disc_label = f"{to_persian_digits(percent)}٪ تخفیف"

    await state.update_data(
        discount_code=clean_code,
        discount_percent=dc.get("discount_percent", 0),
        discount_amount=discount_amount,
        final_price=final_price,
        price=final_price,
    )

    text = await _build_pre_invoice_text(
        gb=gb,
        duration=duration,
        users=users,
        final_price=final_price,
        original_price=original_price,
        discount_code=clean_code,
        discount_label=disc_label,
        discount_amount=discount_amount,
        title="🎉 <b>کد تخفیف با موفقیت اعمال شد!</b>\n📋 <b>پیش‌فاکتور خرید اشتراک</b>",
    )
    card_cfg = await get_card_config()
    await message.answer(
        text,
        reply_markup=payment_method_keyboard(
            duration,
            users,
            gb,
            final_price,
            has_discount=True,
            card_enabled=card_cfg.get("enabled", True),
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.regexp(r"^buy_discount_cancel_\d+_\d+_\d+_\d+$"))
async def buy_discount_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split("_")
    duration = int(parts[3])
    users = int(parts[4])
    gb = int(parts[5])
    _price = int(parts[6])

    await state.clear()
    bd = await get_price_breakdown(gb, duration, users)
    original_price = bd["total_price"]

    text = await _build_pre_invoice_text(
        gb=gb, duration=duration, users=users, final_price=original_price
    )
    card_cfg = await get_card_config()
    await callback.message.edit_text(
        text,
        reply_markup=payment_method_keyboard(
            duration,
            users,
            gb,
            original_price,
            card_enabled=card_cfg.get("enabled", True),
        ),
        parse_mode="HTML",
    )
    await callback.answer("عملیات لغو شد.")


@router.callback_query(F.data.regexp(r"^buy_discount_remove_\d+_\d+_\d+_\d+$"))
async def buy_discount_remove(callback: types.CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split("_")
    duration = int(parts[3])
    users = int(parts[4])
    gb = int(parts[5])
    _ = int(parts[6])

    await state.update_data(discount_code=None, discount_percent=None, final_price=None)
    bd = await get_price_breakdown(gb, duration, users)
    original_price = bd["total_price"]

    text = await _build_pre_invoice_text(
        gb=gb, duration=duration, users=users, final_price=original_price
    )
    card_cfg = await get_card_config()
    await callback.message.edit_text(
        text,
        reply_markup=payment_method_keyboard(
            duration,
            users,
            gb,
            original_price,
            card_enabled=card_cfg.get("enabled", True),
        ),
        parse_mode="HTML",
    )
    await callback.answer("✅ کد تخفیف با موفقیت حذف شد.")


@router.callback_query(F.data.regexp(r"^buy_pay_card_\d+_\d+_\d+_\d+$"))
async def buy_card_payment(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return

    card_cfg = await get_card_config()
    if not card_cfg.get("enabled", True):
        await callback.answer(
            "⚠️ روش پرداخت کارت به کارت در حال حاضر غیرفعال است.",
            show_alert=True,
        )
        return

    parts = callback.data.split("_")
    duration = int(parts[3])
    users = int(parts[4])
    gb = int(parts[5])
    price = int(parts[6])
    tg_id = callback.from_user.id

    data = await state.get_data()
    discount_code = data.get("discount_code")
    original_price = data.get("original_price", price)
    final_price = data.get("final_price")

    payable_amount = (
        final_price if (discount_code and final_price is not None) else price
    )

    from db.models import generate_unique_card_amount

    card_payable_amount = await generate_unique_card_amount(payable_amount)
    unique_delta = card_payable_amount - payable_amount

    invoice = await create_invoice(
        tg_id=tg_id,
        amount=card_payable_amount,
        duration_days=duration,
        data_gb=gb,
        users_count=users,
        payment_method="card",
        discount_code=discount_code,
        original_amount=original_price,
    )
    invoice_id = invoice["id"]

    bd = await get_price_breakdown(gb, duration, users)
    extra_users = max(0, users - 1)
    vol_label = await format_volume_label(gb, with_fair_policy=True)
    notice_line = ""

    breakdown_lines = [f"• هزینه حجم: {format_price(bd['data_price'])}"]
    if bd["user_surcharge"] > 0:
        breakdown_lines.append(
            f"• کاربر اضافه ({to_persian_digits(extra_users)} کاربر): +{format_price(bd['user_surcharge'])}"
        )
    if bd["duration_surcharge"] > 0:
        breakdown_lines.append(
            f"• افزایش مدت ({to_persian_digits(duration)} روز): +{format_price(bd['duration_surcharge'])}"
        )
    if discount_code and original_price > payable_amount:
        discount_amount = original_price - payable_amount
        breakdown_lines.append(
            f"• تخفیف ({discount_code}): -{format_price(discount_amount)}"
        )
    if unique_delta > 0:
        breakdown_lines.append(
            f"• شناسه اختصاصی واریز: +{to_persian_digits(unique_delta)} تومان"
        )
        notice_line = f"⚠️ <i>(لطفا دقیقا مبلغ <code>{card_payable_amount}</code> را انتقال دهید و آن را به هیچ‌وجه رُند نکنید تا سفارش شما فورا و خودکار تایید شود.)</i>\n\n"

    breakdown_str = "\n".join(breakdown_lines)

    card_config = await get_card_config()
    card_number = card_config["card_number"]
    card_holder = card_config["card_holder"]

    from db.models import get_receipt_config

    receipt_cfg = await get_receipt_config()
    expiry_minutes = receipt_cfg.get("expiry_minutes", INVOICE_EXPIRY_MINUTES)

    text = (
        f"💳 <b>سفارش شماره {invoice_id} ثبت شد</b>\n\n"
        f"📦 <b>مشخصات سرویس:</b>\n"
        f"• حجم ترافیک: <b>{vol_label}</b>\n"
        f"• ظرفیت اتصال: <b>{to_persian_digits(users)} کاربر</b>\n"
        f"• مدت اعتبار: <b>{to_persian_digits(duration)} روز</b>\n\n"
        f"💳 <b>ریز محاسبات:</b>\n"
        f"{breakdown_str}\n"
        f"──────────────\n"
        f"💎 <b>مبلغ قابل پرداخت:</b> <code>{card_payable_amount}</code> تومان\n"
        f"{notice_line}"
        f"💳 <b>شماره کارت جهت واریز:</b>\n<code>{card_number}</code>\n"
        f"👤 <b>به نام:</b> {card_holder}\n\n"
        f"⏳ <b>مهلت پرداخت: {to_persian_digits(expiry_minutes)} دقیقه</b>\n\n"
        f"✨ <i>پس از انتقال وجه، روی دکمه «✅ ارسال رسید پرداختی» بزنید و تصویر فیش واریز را ارسال کنید.</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=card_payment_keyboard(
            invoice_id, card_number, card_payable_amount
        ),
        parse_mode="HTML",
    )
    await callback.answer()

    asyncio.create_task(
        _expire_invoice_after(invoice_id, callback, expiry_minutes * 60)
    )


async def _expire_invoice_after(
    invoice_id: str,
    callback: types.CallbackQuery,
    seconds: int,
) -> None:
    await asyncio.sleep(seconds)
    invoice = await get_invoice(invoice_id)
    if (
        invoice
        and invoice["status"] == "pending"
        and not invoice.get("receipt_file_id")
        and not invoice.get("receipt_text")
    ):
        await update_invoice_status(invoice_id, "expired")
        try:
            if callback.message:
                await callback.message.edit_text(
                    f"⌛️ <b>فاکتور شماره {invoice_id} منقضی شد.</b>\n\n"
                    "مهلت پرداخت به پایان رسید. در صورت تمایل می‌توانید سفارش جدیدی ثبت کنید.",
                    parse_mode="HTML",
                )
        except Exception:
            pass


@router.callback_query(F.data.regexp(r"^copy_card_"))
async def copy_card_number(callback: types.CallbackQuery) -> None:
    card_config = await get_card_config()
    card_number = card_config["card_number"]
    await callback.answer(f"شماره کارت کپی شد: {card_number}", show_alert=True)


@router.callback_query(F.data.regexp(r"^copy_amount_"))
async def copy_amount(callback: types.CallbackQuery) -> None:
    invoice_id = callback.data.split("_")[-1]
    invoice = await get_invoice(invoice_id)
    if invoice:
        await callback.answer(
            f"مبلغ قابل پرداخت: {format_price(invoice['amount'])}", show_alert=True
        )
    else:
        await callback.answer("⚠️ فاکتور مورد نظر یافت نشد.", show_alert=True)


@router.callback_query(F.data.regexp(r"^paid_"))
async def paid_button(callback: types.CallbackQuery, state: FSMContext) -> None:
    invoice_id = callback.data.split("_")[-1]
    invoice = await get_invoice(invoice_id)

    if not invoice:
        await callback.answer("❌ متأسفانه فاکتور پیدا نشد.", show_alert=True)
        return

    if invoice["status"] == "cancelled":
        await callback.answer(
            "⚠️ این فاکتور قبلاً لغو شده است و امکان پرداخت آن وجود ندارد. لطفاً مجدداً سفارش ثبت فرمایید.",
            show_alert=True,
        )
        return

    if invoice["status"] == "expired":
        await callback.answer(
            "⚠️ مهلت پرداخت این فاکتور به پایان رسیده و منقضی شده است. لطفاً مجدداً سفارش ثبت فرمایید.",
            show_alert=True,
        )
        return

    if invoice["status"] != "pending":
        await callback.answer(
            "⚠️ این فاکتور قبلاً پردازش شده یا نامعتبر است.", show_alert=True
        )
        return

    from db.models import get_receipt_config

    receipt_cfg = await get_receipt_config()
    overall_enabled = receipt_cfg["overall_enabled"]
    photo_enabled = receipt_cfg["photo_enabled"]
    text_enabled = receipt_cfg["text_enabled"]
    disabled_action = receipt_cfg["disabled_action"]
    disabled_text = receipt_cfg["disabled_text"]

    if not overall_enabled or (not photo_enabled and not text_enabled):
        card_config = await get_card_config()
        card_number = card_config["card_number"]
        amount = invoice["amount"]
        return_kb = card_payment_keyboard(invoice_id, card_number, amount)

        if disabled_action == "alert":
            await callback.answer(disabled_text, show_alert=True)
        elif disabled_action == "message":
            await callback.message.edit_text(
                f"{disabled_text}\n\n🧾 شماره فاکتور: <code>{invoice_id}</code>",
                reply_markup=return_kb,
                parse_mode="HTML",
            )
            await callback.answer()
        else:
            await callback.answer(disabled_text, show_alert=True)
            await callback.message.edit_text(
                f"{disabled_text}\n\n🧾 شماره فاکتور: <code>{invoice_id}</code>",
                reply_markup=return_kb,
                parse_mode="HTML",
            )
        return

    if receipt_cfg.get("auto_verify_delay_enabled", True):
        created_at_raw = invoice.get("created_at")
        if created_at_raw:
            try:
                created_str = str(created_at_raw)
                if created_str.endswith("Z"):
                    created_str = created_str[:-1] + "+00:00"
                created_dt = datetime.fromisoformat(created_str)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)

                now_utc = datetime.now(timezone.utc)
                elapsed_seconds = (now_utc - created_dt).total_seconds()
                delay_minutes = receipt_cfg.get("auto_verify_delay_minutes", 5)
                delay_seconds = delay_minutes * 60
                remaining_seconds = int(delay_seconds - elapsed_seconds)

                if remaining_seconds > 0:
                    time_str = format_remaining_time(remaining_seconds)
                    template = receipt_cfg.get("auto_verify_delay_text") or (
                        "در انتظار پرداخت توسط شما\n"
                        "پرداخت به صورت خودکار تایید می‌شود. در صورت تایید نشدن بعد از {time} اقدام به ارسال رسید فرمایید تا توسط مدیریت بررسی و تایید شود."
                    )
                    alert_text = template.replace("{time}", time_str)
                    await callback.answer(alert_text, show_alert=True)
                    return
            except Exception as e:
                logger.error("Error calculating auto verify delay: %s", e)

    await state.set_state(BuyStates.waiting_receipt)
    await state.update_data(invoice_id=invoice_id)

    if photo_enabled and not text_enabled:
        guide_detail = (
            "لطفاً <b>تصویر فیش یا رسید واریز بانکی</b> خود را ارسال کنید.\n"
            "<i>(ارسال کد متنی در حال حاضر غیرفعال است)</i>"
        )
    elif text_enabled and not photo_enabled:
        guide_detail = (
            "لطفاً <b>شماره پیگیری یا مشخصات واریز</b> خود را به صورت متنی ارسال کنید.\n"
            "<i>(ارسال تصویر فیش در حال حاضر غیرفعال است)</i>"
        )
    else:
        guide_detail = "لطفاً تصویر رسید واریز یا شماره پیگیری تراکنش خود را ارسال کنید."

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف از خرید",
                    callback_data=f"cancel_inv_{invoice_id}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"📸 <b>ارسال رسید</b>\n\n"
        f"{guide_detail}\n\n"
        f"💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


async def _build_admin_invoice_notification_text(
    invoice: dict[str, Any],
    user_id: int,
    username: str | None,
    receipt_type: str = "card",
    receipt_text: str | None = None,
) -> str:
    target_email = invoice.get("target_email")
    is_topup = target_email == "TOPUP" or (
        invoice.get("duration_days") == 0 and invoice.get("data_gb") == 0
    )
    receipt_label = "متنی" if receipt_type == "text" else "کارت به کارت"

    if is_topup:
        admin_text = (
            f"👛 <b>درخواست افزایش موجودی کیف پول ({receipt_label})</b>\n\n"
            f"🆔 فاکتور: <code>{invoice['id']}</code>\n"
            f"👤 کاربر: <code>{user_id}</code>"
        )
        if username:
            admin_text += f" (@{username})"
        admin_text += (
            f"\n\n💰 <b>مبلغ افزایش موجودی:</b> {format_price(invoice['amount'])}\n"
        )
        if receipt_type == "text" and receipt_text:
            admin_text += f"\n📝 متن رسید:\n<code>{receipt_text}</code>"
        return admin_text

    is_renewal = bool(target_email and target_email != "TOPUP")
    users_count = invoice.get("users_count", 1)

    disc_code = invoice.get("discount_code")
    orig_amount = invoice.get("original_amount") or invoice["amount"]
    disc_info = ""
    has_discount = False
    if orig_amount > invoice["amount"]:
        has_discount = True
        disc_info = f"💵 <b>مبلغ:</b> <s>{format_price(orig_amount)}</s>\n"
        if disc_code:
            disc_info += f"🏷️ <b>کد تخفیف:</b> <code>{disc_code}</code>\n"
        elif is_renewal:
            disc_info += "🎁 <b>تخفیف ویژه تمدید اشتراک</b>\n"
        else:
            disc_info += "🎁 <b>تخفیف پیش‌فرض</b>\n"

    if has_discount:
        price_line = f"💰 <b>مبلغ واریزی نهایی (تخفیف‌خورده):</b> <b>{format_price(invoice['amount'])}</b>\n"
    else:
        price_line = (
            f"💰 <b>مبلغ واریزی:</b> <b>{format_price(invoice['amount'])}</b>\n"
        )

    if is_renewal:
        title = f"🔄 <b>درخواست تأیید تمدید اشتراک ({receipt_label})</b>"
        client_info_text = ""
        try:
            client = await xui_api.get_client(target_email)
            client_full = await xui_api.get_client_full(target_email)
            if client:
                total_bytes = client.get("totalGB", 0)
                expiry_ms = client.get("expiryTime", 0)
                used_traffic = client_full.get("usedTraffic", 0) if client_full else 0
                remaining = max(0, total_bytes - used_traffic) if total_bytes > 0 else 0

                total_gb_int = (
                    int(round(total_bytes / (1024**3))) if total_bytes > 0 else 0
                )
                is_sub_unlimited = (total_bytes == 0) or (
                    await is_unlimited_volume(total_gb_int)
                )

                if is_sub_unlimited:
                    if total_bytes > 0:
                        usage_str = f"{format_detailed_traffic(used_traffic)} / ♾️ نامحدود (سقف {format_size_persian_gb(total_bytes)})"
                        remaining_str = (
                            f"{format_detailed_traffic(remaining)} (سقف منصفانه)"
                        )
                    else:
                        usage_str = (
                            f"{format_detailed_traffic(used_traffic)} / ♾️ نامحدود"
                        )
                        remaining_str = "♾️ نامحدود"
                elif total_bytes > 0:
                    usage_str = f"{format_detailed_traffic(used_traffic)} / {format_size_persian_gb(total_bytes)}"
                    remaining_str = format_detailed_traffic(remaining)
                else:
                    usage_str = f"{format_detailed_traffic(used_traffic)} / نامحدود"
                    remaining_str = "نامحدود"

                limit_ip = client.get("limitIp", 0)
                users_str = (
                    f"{to_persian_digits(limit_ip)} کاربر"
                    if limit_ip > 0
                    else "نامحدود"
                )
                days_str = format_remaining_days(expiry_ms)

                from db.models import get_ip_violation

                is_enabled = bool(client.get("enable", True))
                ip_rec = await get_ip_violation(target_email)
                is_ip_suspended = bool(ip_rec.get("suspended", 0)) if ip_rec else False

                if is_ip_suspended:
                    status_str = "⛔️ مسدودشده (تخطی از سقف IP)"
                elif is_enabled:
                    status_str = "🟢 فعال"
                else:
                    status_str = "🔴 غیرفعال"

                sub_type_line = (
                    "📦 <b>نوع اشتراک فعلی:</b> ♾️ نامحدود\n" if is_sub_unlimited else ""
                )

                client_info_text = (
                    f"📋 <b>وضعیت فعلی اشتراک (جهت تمدید):</b>\n"
                    f"🏷 <b>نام سرویس:</b> <code>{target_email}</code>\n"
                    f"⚡️ <b>وضعیت:</b> {status_str}\n"
                    f"{sub_type_line}"
                    f"👥 <b>ظرفیت کاربر:</b> {users_str}\n"
                    f"📊 <b>میزان مصرف:</b> {usage_str}\n"
                    f"🔋 <b>ترافیک باقیمانده:</b> {remaining_str}\n"
                    f"⏳ <b>اعتبار باقیمانده:</b> {days_str}\n\n"
                )
            else:
                client_info_text = (
                    f"📋 <b>وضعیت فعلی اشتراک (جهت تمدید):</b>\n"
                    f"🏷 <b>نام سرویس:</b> <code>{target_email}</code> (اطلاعات در سرور یافت نشد)\n\n"
                )
        except Exception as e:
            logger.warning(
                "Failed to fetch client info for renewal target_email %s: %s",
                target_email,
                e,
            )
            client_info_text = (
                f"📋 <b>وضعیت فعلی اشتراک (جهت تمدید):</b>\n"
                f"🏷 <b>نام سرویس:</b> <code>{target_email}</code>\n\n"
            )

        order_header = "📦 <b>مشخصات بسته جدید تمدید:</b>\n"
    else:
        title = f"🔔 <b>درخواست تأیید پرداخت اشتراک ({receipt_label})</b>"
        client_info_text = ""
        order_header = "📦 <b>جزئیات سفارش:</b>\n"

    admin_text = (
        f"{title}\n\n"
        f"🆔 فاکتور: <code>{invoice['id']}</code>\n"
        f"👤 کاربر: <code>{user_id}</code>"
    )
    if username:
        admin_text += f" (@{username})"

    admin_text += f"\n\n{client_info_text}"
    admin_text += (
        f"{order_header}"
        f"⏱ مدت: {invoice['duration_days']} روز\n"
        f"👤 تعداد کاربر: {to_persian_digits(users_count)} کاربر\n"
        f"📊 حجم: {await format_volume_label(invoice['data_gb'], with_real_gb=True)}\n"
        f"{disc_info}"
        f"{price_line}"
    )

    if receipt_type == "text" and receipt_text:
        admin_text += f"\n📝 متن رسید:\n<code>{receipt_text}</code>"

    return admin_text


@router.message(BuyStates.waiting_receipt, F.photo)
async def receive_receipt_photo(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not message.from_user or not message.photo:
        return

    data = await state.get_data()
    invoice_id = data.get("invoice_id")
    if not invoice_id:
        await state.clear()
        return

    from db.models import get_receipt_config

    receipt_cfg = await get_receipt_config()
    if not receipt_cfg["overall_enabled"] or not receipt_cfg["photo_enabled"]:
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف از خرید",
                        callback_data="buy_cancel",
                    )
                ]
            ]
        )
        if receipt_cfg["text_enabled"]:
            await message.answer(
                "⚠️ <b>امکان ارسال تصویر رسید در حال حاضر غیرفعال است.</b>\n\n"
                "لطفاً شماره پیگیری یا مشخصات واریز را به صورت <b>متنی</b> ارسال کنید.\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )
        else:
            await message.answer(
                f"{receipt_cfg['disabled_text']}",
                parse_mode="HTML",
            )
        return

    invoice = await get_invoice(invoice_id)
    if not invoice or invoice["status"] != "pending":
        await message.answer("⚠️ این فاکتور منقضی شده یا پیش‌تر پردازش شده است.")
        await state.clear()
        return

    file_id = message.photo[-1].file_id
    await set_invoice_receipt(invoice_id, file_id=file_id)
    await update_invoice_status(invoice_id, "under_review")
    await state.clear()

    is_topup = invoice.get("target_email") == "TOPUP" or (
        invoice.get("duration_days") == 0 and invoice.get("data_gb") == 0
    )
    is_renewal = bool(
        invoice.get("target_email") and invoice.get("target_email") != "TOPUP"
    )

    if is_topup:
        user_msg = (
            "🎉 <b>رسید پرداخت شما با موفقیت دریافت شد!</b>\n\n"
            "درخواست افزایش موجودی کیف پول در صف بررسی پشتیبانی قرار گرفت. "
            "به‌محض تایید، موجودی حساب شما شارژ می‌شود. 👛"
        )
    elif is_renewal:
        user_msg = (
            "🎉 <b>رسید پرداخت شما با موفقیت دریافت شد!</b>\n\n"
            "درخواست تمدید اشتراک در صف بررسی پشتیبانی قرار گرفت. "
            "به‌محض تایید، سرویس شما تمدید خواهد شد. 🔄"
        )
    else:
        user_msg = (
            "🎉 <b>رسید پرداخت شما با موفقیت دریافت شد!</b>\n\n"
            "سفارش شما در صف بررسی پشتیبانی قرار گرفت. "
            "به‌محض تایید، مشخصات اتصال اشتراک برای شما ارسال می‌شود. 🚀"
        )

    await message.answer(
        user_msg,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )

    admin_text = await _build_admin_invoice_notification_text(
        invoice=invoice,
        user_id=message.from_user.id,
        username=message.from_user.username,
        receipt_type="card",
    )

    from db.models import get_all_admins, has_admin_permission

    admin_ids = [ADMIN_CHAT_ID]
    for adm in await get_all_admins():
        a_id = adm["tg_id"]
        if a_id > 0 and a_id not in admin_ids:
            if await has_admin_permission(a_id, "approve_invoices"):
                admin_ids.append(a_id)

    primary_msg_id = 0
    for a_id in admin_ids:
        try:
            sent_msg = await bot.send_photo(
                chat_id=a_id,
                photo=file_id,
                caption=admin_text,
                reply_markup=admin_payment_review_keyboard(invoice_id),
                parse_mode="HTML",
            )
            if primary_msg_id == 0:
                primary_msg_id = sent_msg.message_id
        except Exception as e:
            logger.warning("Failed to send invoice review photo to %d: %s", a_id, e)

    if primary_msg_id > 0:
        await set_invoice_message_id(invoice_id, primary_msg_id)


@router.message(BuyStates.waiting_receipt, F.text)
async def receive_receipt_text(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not message.from_user or not message.text:
        return

    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(
            "❌ فرآیند ارسال رسید لغو شد.",
            reply_markup=main_menu_keyboard(),
        )
        return

    data = await state.get_data()
    invoice_id = data.get("invoice_id")
    if not invoice_id:
        await state.clear()
        return

    from db.models import get_receipt_config

    receipt_cfg = await get_receipt_config()
    if not receipt_cfg["overall_enabled"] or not receipt_cfg["text_enabled"]:
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف از خرید",
                        callback_data="buy_cancel",
                    )
                ]
            ]
        )
        if receipt_cfg["photo_enabled"]:
            await message.answer(
                "⚠️ <b>امکان ارسال متنی رسید در حال حاضر غیرفعال است.</b>\n\n"
                "لطفاً <b>تصویر فیش یا رسید واریز بانکی</b> را ارسال کنید.\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )
        else:
            await message.answer(
                f"{receipt_cfg['disabled_text']}",
                parse_mode="HTML",
            )
        return

    invoice = await get_invoice(invoice_id)
    if not invoice or invoice["status"] != "pending":
        await message.answer("⚠️ این فاکتور منقضی شده یا پیش‌تر پردازش شده است.")
        await state.clear()
        return

    receipt_text = message.text.strip()
    await set_invoice_receipt(invoice_id, text=receipt_text)
    await update_invoice_status(invoice_id, "under_review")
    await state.clear()

    is_topup = invoice.get("target_email") == "TOPUP" or (
        invoice.get("duration_days") == 0 and invoice.get("data_gb") == 0
    )
    is_renewal = bool(
        invoice.get("target_email") and invoice.get("target_email") != "TOPUP"
    )

    if is_topup:
        user_msg = (
            "🎉 <b>اطلاعات پرداخت شما با موفقیت دریافت شد!</b>\n\n"
            "درخواست افزایش موجودی کیف پول در صف بررسی پشتیبانی قرار گرفت. "
            "به‌محض تایید، موجودی حساب شما شارژ می‌شود. 👛"
        )
    elif is_renewal:
        user_msg = (
            "🎉 <b>اطلاعات پرداخت شما با موفقیت دریافت شد!</b>\n\n"
            "درخواست تمدید اشتراک در صف بررسی پشتیبانی قرار گرفت. "
            "به‌محض تایید، سرویس شما تمدید خواهد شد. 🔄"
        )
    else:
        user_msg = (
            "🎉 <b>اطلاعات پرداخت شما با موفقیت دریافت شد!</b>\n\n"
            "سفارش شما در صف بررسی پشتیبانی قرار گرفت. "
            "به‌محض تایید، مشخصات اتصال اشتراک برای شما ارسال می‌شود. 🚀"
        )

    await message.answer(
        user_msg,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )

    admin_text = await _build_admin_invoice_notification_text(
        invoice=invoice,
        user_id=message.from_user.id,
        username=message.from_user.username,
        receipt_type="text",
        receipt_text=receipt_text,
    )

    from db.models import get_all_admins, has_admin_permission

    admin_ids = [ADMIN_CHAT_ID]
    for adm in await get_all_admins():
        a_id = adm["tg_id"]
        if a_id > 0 and a_id not in admin_ids:
            if await has_admin_permission(a_id, "approve_invoices"):
                admin_ids.append(a_id)

    primary_msg_id = 0
    for a_id in admin_ids:
        try:
            sent_msg = await bot.send_message(
                chat_id=a_id,
                text=admin_text,
                reply_markup=admin_payment_review_keyboard(invoice_id),
                parse_mode="HTML",
            )
            if primary_msg_id == 0:
                primary_msg_id = sent_msg.message_id
        except Exception as e:
            logger.warning("Failed to send invoice review text to %d: %s", a_id, e)

    if primary_msg_id > 0:
        await set_invoice_message_id(invoice_id, primary_msg_id)


@router.callback_query(F.data.startswith("cancel_inv_") | (F.data == "buy_cancel"))
async def buy_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    invoice_id = None
    if callback.data and callback.data.startswith("cancel_inv_"):
        invoice_id = callback.data[len("cancel_inv_") :]
    if not invoice_id:
        data = await state.get_data()
        invoice_id = data.get("invoice_id")
    if not invoice_id and callback.from_user:
        from db.models import get_latest_card_invoice_for_user

        latest = await get_latest_card_invoice_for_user(callback.from_user.id)
        if latest and latest.get("status") == "pending":
            invoice_id = latest["id"]

    if invoice_id:
        from db.models import update_invoice_status

        await update_invoice_status(invoice_id, "cancelled")

    await state.clear()
    msg_inv = f" شماره <code>{invoice_id}</code>" if invoice_id else ""
    await callback.message.edit_text(
        f"❌ فاکتور{msg_inv} با موفقیت لغو شد.\n\n"
        "در هر زمان می‌توانید از منوی ربات اقدام به ثبت سفارش یا تمدید فرمایید.",
        parse_mode="HTML",
    )
    await callback.answer("عملیات لغو شد.")


def _is_likely_receipt_text(text: str) -> bool:
    from keyboards.reply_kb import (
        BTN_BUY,
        BTN_GUIDE,
        BTN_INCREASE_WALLET,
        BTN_INVITE,
        BTN_MY_SUBS,
        BTN_PRICING,
        BTN_PROFILE,
        BTN_SUPPORT,
        BTN_TEST,
    )
    from utils.formatting import persian_to_english_digits

    clean_text = text.strip()
    if not clean_text or clean_text.startswith("/"):
        return False

    standard_buttons = {
        BTN_BUY,
        BTN_GUIDE,
        BTN_INCREASE_WALLET,
        BTN_INVITE,
        BTN_MY_SUBS,
        BTN_PRICING,
        BTN_PROFILE,
        BTN_SUPPORT,
        BTN_TEST,
    }
    if clean_text in standard_buttons:
        return False

    digits_only = [c for c in persian_to_english_digits(clean_text) if c.isdigit()]
    keywords = ["پیگیری", "تراکنش", "واریز", "کارت", "رسید", "پرداخت"]
    if len(digits_only) >= 5 or any(kw in clean_text for kw in keywords):
        return True
    return False


async def _handle_unpaid_receipt_reminder(message: types.Message) -> bool:
    if not message.from_user:
        return False

    from db.models import (
        get_latest_card_invoice_for_user,
        get_receipt_config,
        is_invoice_expired,
    )
    from utils.formatting import format_price

    receipt_cfg = await get_receipt_config()
    if not receipt_cfg.get("reminder_unpaid_enabled", True):
        return False

    invoice = await get_latest_card_invoice_for_user(message.from_user.id)
    if not invoice:
        return False

    invoice_id = invoice["id"]
    status = invoice.get("status", "")

    if status == "cancelled":
        await message.answer(
            f"⚠️ <b>فاکتور کارت به کارت شما لغو شده است.</b>\n\n"
            f"🧾 <b>شماره فاکتور:</b> <code>{invoice_id}</code>\n\n"
            f"این فاکتور قبلاً توسط شما لغو شده و امکان پرداخت یا ارسال فیش برای آن وجود ندارد. لطفاً جهت خرید یا تمدید، مجدداً از منوی ربات سفارش جدیدی ثبت فرمایید.",
            parse_mode="HTML",
        )
        return True

    if status != "pending" or is_invoice_expired(invoice):
        await message.answer(
            f"⚠️ <b>فاکتور کارت به کارت شما منقضی یا نامعتبر شده است.</b>\n\n"
            f"🧾 <b>شماره فاکتور:</b> <code>{invoice_id}</code>\n\n"
            f"مهلت پرداخت این فاکتور به پایان رسیده است. لطفاً جهت خرید یا تمدید، مجدداً از منوی ربات اقدام فرمایید.",
            parse_mode="HTML",
        )
        return True

    raw_template = receipt_cfg.get("reminder_unpaid_text") or ""
    amount_str = format_price(invoice.get("amount", 0))

    reminder_msg = raw_template.replace("{invoice_id}", invoice_id).replace(
        "{amount}", amount_str
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ پرداخت کردم",
                    callback_data=f"paid_{invoice_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف از خرید",
                    callback_data=f"cancel_inv_{invoice_id}",
                )
            ],
        ]
    )

    await message.answer(
        reminder_msg,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return True


@router.message(StateFilter(None), F.photo | F.document)
async def unhandled_receipt_media_reminder(
    message: types.Message, state: FSMContext
) -> None:
    await _handle_unpaid_receipt_reminder(message)


@router.message(
    StateFilter(None),
    F.text,
    lambda msg: bool(msg.text and _is_likely_receipt_text(msg.text)),
)
async def unhandled_receipt_text_reminder(
    message: types.Message, state: FSMContext
) -> None:
    await _handle_unpaid_receipt_reminder(message)
