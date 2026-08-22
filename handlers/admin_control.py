from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_CHAT_ID
from db.models import reset_all_test_subs
from services.pricing import (
    get_pricing_config,
    reset_pricing_config_to_defaults,
    update_base_gb_rate,
    update_duration_surcharge,
    update_user_surcharge,
    update_volume_tiers,
)
from services.test_sub_config import (
    get_test_sub_config,
    update_test_sub_config,
)
from utils.formatting import (
    format_price,
    format_size_gb,
    persian_to_english_digits,
    to_persian_digits,
)

logger = logging.getLogger(__name__)
router = Router(name="admin_control")


class AdminControlStates(StatesGroup):
    waiting_base_rate = State()
    waiting_user_surcharge = State()
    waiting_dur_60 = State()
    waiting_dur_90 = State()
    waiting_tiers_text = State()
    waiting_test_gb = State()
    waiting_test_dur = State()
    waiting_test_cooldown = State()

    waiting_disc_manual_code = State()
    waiting_disc_auto_length = State()
    waiting_disc_percent = State()
    waiting_disc_max_uses = State()
    waiting_disc_edit_percent = State()
    waiting_disc_edit_max_uses = State()

    waiting_ref_percent = State()

    waiting_group_create_name = State()
    waiting_group_rename_name = State()

    waiting_card_number = State()
    waiting_card_holder = State()


def _is_admin(event: types.CallbackQuery | types.Message) -> bool:
    return event.from_user is not None and event.from_user.id == ADMIN_CHAT_ID


async def _build_pricing_panel() -> tuple[str, InlineKeyboardMarkup]:
    config = await get_pricing_config()
    test_config = await get_test_sub_config()
    from db.models import get_card_config, get_referral_config

    ref_config = await get_referral_config()
    card_config = await get_card_config()

    card_num = card_config["card_number"] or "تنظیم نشده"
    card_own = card_config["card_holder"] or "تنظیم نشده"

    base_rate = config["base_gb_rate"]
    user_surcharge = config["user_surcharge"]
    dur_surcharges: dict[int, int] = config["duration_surcharges"]
    volume_tiers: list[tuple[int, int]] = config["volume_tiers"]
    fallback_rate: int = config["fallback_gb_rate"]

    test_gb = test_config["gb"]
    test_dur = test_config["duration_days"]
    test_cool = test_config["cooldown_days"]

    ref_status = "🟢 فعال" if ref_config["enabled"] else "🔴 غیرفعال"
    ref_percent = to_persian_digits(ref_config["percent"])

    tiers_text = ""
    for max_gb, rate in sorted(volume_tiers, key=lambda x: x[0]):
        tiers_text += (
            f"  • تا {to_persian_digits(max_gb)} گیگ: {format_price(rate)} / GB\n"
        )
    last_max = volume_tiers[-1][0] if volume_tiers else 100
    tiers_text += f"  • بالای {to_persian_digits(last_max)} گیگ: {format_price(fallback_rate)} / GB\n"

    dur_text = (
        f"  • ۳۰ روز: +{format_price(dur_surcharges.get(30, 0))}\n"
        f"  • ۶۰ روز: +{format_price(dur_surcharges.get(60, 0))}\n"
        f"  • ۹۰ روز: +{format_price(dur_surcharges.get(90, 0))}\n"
    )

    test_text = (
        f"  • حجم: {format_size_gb(test_gb)}\n"
        f"  • مدت: {to_persian_digits(test_dur)} روز\n"
        f"  • کول‌داون: {to_persian_digits(test_cool)} روز\n"
    )

    ref_text = f"  • وضعیت: {ref_status}\n  • پورسانت پاداش: {ref_percent}٪\n"

    text = (
        f"⚙️ <b>پنل مدیریت و تنظیمات ربات</b>\n\n"
        f"💵 <b>نرخ پایه هر گیگ:</b> {format_price(base_rate)}\n"
        f"👤 <b>هزینه هر کاربر اضافه:</b> +{format_price(user_surcharge)}\n\n"
        f"💳 <b>کارت جهت واریز:</b> <code>{card_num}</code> ({card_own})\n\n"
        f"⏱ <b>حق‌الزحمه مدت زمان:</b>\n{dur_text}\n"
        f"📊 <b>پله‌های تخفیف حجم:</b>\n{tiers_text}\n"
        f"🎁 <b>اشتراک تست رایگان:</b>\n{test_text}\n"
        f"👥 <b>سیستم زیرمجموعه‌گیری:</b>\n{ref_text}"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 تغییر نرخ پایه (هر گیگ)",
                    callback_data="admin_price_base",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👤 تغییر هزینه کاربر اضافه",
                    callback_data="admin_price_user",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏱ تغییر حق‌الزحمه مدت زمان",
                    callback_data="admin_price_dur_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 تغییر پله‌های تخفیف حجم",
                    callback_data="admin_price_tiers",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎁 تنظیمات اشتراک تست",
                    callback_data="admin_test_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏷️ مدیریت کدهای تخفیف",
                    callback_data="admin_discounts_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💳 تنظیمات شماره کارت و صاحب کارت",
                    callback_data="admin_card_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 تنظیمات سیستم زیرمجموعه‌گیری",
                    callback_data="admin_ref_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📡 مدیریت اینباندها (Inbounds)",
                    callback_data="admin_inbounds_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="➕ ساخت اشتراک سفارشی جدید",
                    callback_data="admin_create_sub_start",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔍 جستجوی کاربر و مدیریت اشتراک",
                    callback_data="admin_search_start",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 مدیریت گروه‌های مشتری (Groups)",
                    callback_data="admin_groups_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="♻️ بازنشانی تست همه کاربران",
                    callback_data="admin_test_reset_all",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 بازنشانی قیمت‌ها به پیش‌فرض",
                    callback_data="admin_price_reset",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ بستن پنل",
                    callback_data="admin_price_close",
                ),
            ],
        ]
    )
    return text, keyboard


@router.message(Command("control", "admin_control"))
async def admin_pricing_cmd(message: types.Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.clear()
    text, keyboard = await _build_pricing_panel()
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "admin_price_main")
async def admin_pricing_main(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return
    await state.clear()
    text, keyboard = await _build_pricing_panel()
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_price_close")
async def admin_pricing_close(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.delete()


@router.callback_query(F.data == "admin_price_base")
async def admin_price_base_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_base_rate)
    await callback.message.edit_text(
        "💵 <b>نرخ پایه جدید هر گیگ (به تومان) را وارد کنید:</b>\n"
        "مثال: <code>5000</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_base_rate, F.text)
async def admin_price_base_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = (
            persian_to_english_digits(message.text.strip())
            .replace(",", "")
            .replace("،", "")
            .replace(" ", "")
        )
        val = int(clean)
        if val <= 0:
            raise ValueError
        await update_base_gb_rate(val)
        await state.clear()
        text, keyboard = await _build_pricing_panel()
        await message.answer(
            f"✅ نرخ پایه به <b>{format_price(val)}</b> تغییر یافت.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except ValueError:
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر به تومان وارد کنید. مثال: <code>5000</code>",
            parse_mode="HTML",
        )


@router.callback_query(F.data == "admin_price_user")
async def admin_price_user_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_user_surcharge)
    await callback.message.edit_text(
        "👤 <b>هزینه اضافه به ازای هر کاربر اضافه (به تومان) را وارد کنید:</b>\n"
        "مثال: <code>50000</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_user_surcharge, F.text)
async def admin_price_user_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = (
            persian_to_english_digits(message.text.strip())
            .replace(",", "")
            .replace("،", "")
            .replace(" ", "")
        )
        val = int(clean)
        if val < 0:
            raise ValueError
        await update_user_surcharge(val)
        await state.clear()
        text, keyboard = await _build_pricing_panel()
        await message.answer(
            f"✅ هزینه کاربر اضافه به <b>+{format_price(val)}</b> تغییر یافت.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except ValueError:
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید. مثال: <code>50000</code>",
            parse_mode="HTML",
        )


@router.callback_query(F.data == "admin_price_dur_menu")
async def admin_price_dur_menu(callback: types.CallbackQuery) -> None:
    if not _is_admin(callback):
        return
    config = await get_pricing_config()
    durs = config["duration_surcharges"]

    text = (
        "⏱ <b>تنظیم حق‌الزحمه مدت زمان اشتراک</b>\n\n"
        f"• ۶۰ روزه: +{format_price(durs.get(60, 0))}\n"
        f"• ۹۰ روزه: +{format_price(durs.get(90, 0))}\n\n"
        "مدت مورد نظر را جهت ویرایش انتخاب کنید:"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"۶۰ روزه (+{format_price(durs.get(60, 0))})",
                    callback_data="admin_price_dur_60",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"۹۰ روزه (+{format_price(durs.get(90, 0))})",
                    callback_data="admin_price_dur_90",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل اصلی",
                    callback_data="admin_price_main",
                )
            ],
        ]
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_price_dur_60")
async def admin_price_dur_60_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_dur_60)
    await callback.message.edit_text(
        "⏱ <b>مبلغ اضافه برای اشتراک ۶۰ روزه (به تومان) را وارد کنید:</b>\n"
        "مثال: <code>50000</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_dur_60, F.text)
async def admin_price_dur_60_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = (
            persian_to_english_digits(message.text.strip())
            .replace(",", "")
            .replace("،", "")
            .replace(" ", "")
        )
        val = int(clean)
        if val < 0:
            raise ValueError
        await update_duration_surcharge(60, val)
        await state.clear()
        text, keyboard = await _build_pricing_panel()
        await message.answer(
            f"✅ مبلغ اضافه اشتراک ۶۰ روزه به <b>+{format_price(val)}</b> تغییر یافت.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید.", parse_mode="HTML")


@router.callback_query(F.data == "admin_price_dur_90")
async def admin_price_dur_90_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_dur_90)
    await callback.message.edit_text(
        "⏱ <b>مبلغ اضافه برای اشتراک ۹۰ روزه (به تومان) را وارد کنید:</b>\n"
        "مثال: <code>100000</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_dur_90, F.text)
async def admin_price_dur_90_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = (
            persian_to_english_digits(message.text.strip())
            .replace(",", "")
            .replace("،", "")
            .replace(" ", "")
        )
        val = int(clean)
        if val < 0:
            raise ValueError
        await update_duration_surcharge(90, val)
        await state.clear()
        text, keyboard = await _build_pricing_panel()
        await message.answer(
            f"✅ مبلغ اضافه اشتراک ۹۰ روزه به <b>+{format_price(val)}</b> تغییر یافت.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید.", parse_mode="HTML")


@router.callback_query(F.data == "admin_price_tiers")
async def admin_price_tiers_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_tiers_text)
    await callback.message.edit_text(
        "📊 <b>تنظیم پله‌های تخفیف حجم</b>\n\n"
        "لطفاً پله‌های تخفیف را سطر به سطر به فرمت <code>سقف_حجم:نرخ_هرگیگ</code> وارد کنید.\n"
        "سطر آخر را با <code>default:نرخ_بالای_آخرین_پله</code> وارد نمایید.\n\n"
        "<b>مثال:</b>\n"
        "<code>20:5000\n"
        "50:4500\n"
        "100:4000\n"
        "default:3500</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_tiers_text, F.text)
async def admin_price_tiers_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    raw_lines = [
        line.strip()
        for line in persian_to_english_digits(message.text.strip()).split("\n")
        if line.strip()
    ]
    parsed_tiers: list[tuple[int, int]] = []
    fallback_rate = 3500

    try:
        for line in raw_lines:
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower()
            v_int = int(v.strip().replace(",", "").replace("،", "").replace(" ", ""))

            if k == "default":
                fallback_rate = v_int
            else:
                gb_int = int(k)
                parsed_tiers.append((gb_int, v_int))

        if not parsed_tiers:
            raise ValueError("No valid tiers parsed")

        parsed_tiers.sort(key=lambda x: x[0])
        await update_volume_tiers(parsed_tiers, fallback_rate)
        await state.clear()
        text, keyboard = await _build_pricing_panel()
        await message.answer(
            f"✅ پله‌های تخفیف حجم با موفقیت به روزرسانی شدند!\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    except Exception:
        await message.answer(
            "❌ فرمت وارد شده نامعتبر است.\n"
            "لطفاً مطابق مثال ارسال کنید:\n"
            "<code>20:5000\n50:4500\n100:4000\ndefault:3500</code>",
            parse_mode="HTML",
        )


@router.callback_query(F.data == "admin_price_reset")
async def admin_price_reset(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return
    await reset_pricing_config_to_defaults()
    await state.clear()
    text, keyboard = await _build_pricing_panel()
    await callback.message.edit_text(
        f"✅ تمامی تنظیمات قیمت‌گذاری به پیش‌فرض سیستم بازنشانی شدند.\n\n{text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_test_menu")
async def admin_test_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return
    await state.clear()

    test_config = await get_test_sub_config()
    gb = test_config["gb"]
    dur = test_config["duration_days"]
    cool = test_config["cooldown_days"]

    text = (
        "🎁 <b>تنظیمات اشتراک تست رایگان</b>\n\n"
        f"📊 <b>حجم اولیه:</b> {format_size_gb(gb)}\n"
        f"⏱ <b>مدت زمان اعتبار:</b> {to_persian_digits(dur)} روز\n"
        f"🔄 <b>فاصله زمانی دریافت مجدد (کول‌داون):</b> {to_persian_digits(cool)} روز\n\n"
        "گزینه مورد نظر را جهت ویرایش انتخاب کنید:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 تغییر حجم تست (GB)", callback_data="admin_test_gb"
                ),
                InlineKeyboardButton(
                    text="⏱ تغییر مدت (روز)", callback_data="admin_test_dur"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 تغییر کول‌داون (روز)", callback_data="admin_test_cooldown"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
                ),
            ],
        ]
    )

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_test_gb")
async def admin_test_gb_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_test_gb)
    await callback.message.edit_text(
        "📊 <b>حجم اشتراک تست را به گیگابایت (اعشاری یا صحیح) وارد کنید:</b>\n"
        "مثال: <code>0.5</code> یا <code>1</code> یا <code>2</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_test_gb, F.text)
async def admin_test_gb_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        raw = persian_to_english_digits(message.text.strip())
        val = float(raw)
        if val <= 0:
            raise ValueError
        await update_test_sub_config(gb=val)
        await state.clear()
        panel_text, keyboard = await _build_pricing_panel()
        await message.answer(
            f"✅ حجم اشتراک تست به <b>{format_size_gb(val)}</b> تغییر یافت.\n\n{panel_text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except ValueError:
        await message.answer(
            "❌ لطفاً یک عدد معتبر وارد کنید. مثال: <code>0.5</code>",
            parse_mode="HTML",
        )


@router.callback_query(F.data == "admin_test_dur")
async def admin_test_dur_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_test_dur)
    await callback.message.edit_text(
        "⏱ <b>مدت زمان اعتبار اشتراک تست را به روز وارد کنید:</b>\n"
        "مثال: <code>1</code> یا <code>2</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_test_dur, F.text)
async def admin_test_dur_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        raw = persian_to_english_digits(message.text.strip())
        val = int(raw)
        if val <= 0:
            raise ValueError
        await update_test_sub_config(duration_days=val)
        await state.clear()
        panel_text, keyboard = await _build_pricing_panel()
        await message.answer(
            f"✅ مدت زمان اشتراک تست به <b>{to_persian_digits(val)} روز</b> تغییر یافت.\n\n{panel_text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید.", parse_mode="HTML")


@router.callback_query(F.data == "admin_test_cooldown")
async def admin_test_cooldown_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_test_cooldown)
    await callback.message.edit_text(
        "🔄 <b>فاصله زمانی دریافت مجدد (کول‌داون) را به روز وارد کنید:</b>\n"
        "مثال: <code>14</code> یا <code>7</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_test_cooldown, F.text)
async def admin_test_cooldown_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        raw = persian_to_english_digits(message.text.strip())
        val = int(raw)
        if val < 0:
            raise ValueError
        await update_test_sub_config(cooldown_days=val)
        await state.clear()
        panel_text, keyboard = await _build_pricing_panel()
        await message.answer(
            f"✅ کول‌داون اشتراک تست به <b>{to_persian_digits(val)} روز</b> تغییر یافت.\n\n{panel_text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید.", parse_mode="HTML")


@router.callback_query(F.data == "admin_test_reset_all")
async def admin_test_reset_all(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.clear()
    count = await reset_all_test_subs()
    panel_text, keyboard = await _build_pricing_panel()
    from utils.helpers import safe_edit_text

    await safe_edit_text(
        callback.message,
        f"✅ امکان دریافت اشتراک تست برای <b>{to_persian_digits(count)} کاربر</b> با موفقیت بازنشانی شد.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_discounts_menu")
async def admin_discounts_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.clear()

    from db.discounts import list_discount_codes

    codes = await list_discount_codes()

    text = f"🏷️ <b>مدیریت کدهای تخفیف</b>\n\nتعداد کدهای موجود: {to_persian_digits(len(codes))}\n\n"

    if codes:
        for dc in codes:
            status_emoji = "🟢" if dc["is_active"] else "🔴"
            max_uses_str = (
                "بی‌نهایت"
                if (
                    dc["max_uses"] is None
                    or dc["max_uses"] <= 0
                    or dc["max_uses"] == -1
                )
                else f"{to_persian_digits(dc['max_uses'])}"
            )
            used_str = to_persian_digits(dc["used_count"])
            percent_str = to_persian_digits(dc["discount_percent"])

            text += (
                f"🔹 <b>{dc['code']}</b> — {percent_str}٪ تخفیف | "
                f"استفاده: {used_str}/{max_uses_str} | وضعیت: {status_emoji}\n"
            )
        text += "\nجهت مشاهده جزئیات یا ویرایش، کد مورد نظر را انتخاب کنید:"
    else:
        text += "<i>هیچ کد تخفیفی ثبت نشده است.</i>"

    keyboard_rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="➕ ساخت کد تخفیف جدید", callback_data="admin_disc_create_menu"
            )
        ]
    ]

    for dc in codes[:10]:
        status_symbol = "🟢" if dc["is_active"] else "🔴"
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status_symbol} {dc['code']} ({to_persian_digits(dc['discount_percent'])}%)",
                    callback_data=f"admin_disc_view_{dc['code']}",
                )
            ]
        )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
            )
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_disc_create_menu")
async def admin_disc_create_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.clear()

    text = (
        "➕ <b>ایجاد کد تخفیف جدید</b>\n\n"
        "نوع تعریف کد تخفیف را انتخاب کنید:\n"
        "• <b>کد اختصاصی:</b> وارد کردن عبارات دلخواه (مثلاً VIP20)\n"
        "• <b>تولید خودکار:</b> ساخت کد تصادفی با طول مشخص"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ ورود دستی کد", callback_data="admin_disc_create_manual"
                ),
                InlineKeyboardButton(
                    text="🎲 تولید خودکار کد", callback_data="admin_disc_create_auto"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت", callback_data="admin_discounts_menu"
                ),
            ],
        ]
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_disc_create_manual")
async def admin_disc_create_manual_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_disc_manual_code)
    await callback.message.edit_text(
        "✏️ <b>کد تخفیف اختصاصی را وارد کنید:</b>\n"
        "مثال: <code>SUMMER2026</code> یا <code>VIP50</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_manual_code, F.text)
async def admin_disc_create_manual_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    code = message.text.strip().upper()
    if len(code) < 2 or len(code) > 30:
        await message.answer("❌ طول کد تخفیف باید بین ۲ تا ۳۰ کاراکتر باشد.")
        return

    from db.discounts import get_discount_code

    existing = await get_discount_code(code)
    if existing:
        await message.answer(
            "❌ این کد تخفیف قبلاً ثبت شده است. لطفاً کد دیگری وارد کنید."
        )
        return

    await state.update_data(new_code=code)
    await state.set_state(AdminControlStates.waiting_disc_percent)
    await message.answer(
        f"✅ کد <code>{code}</code> انتخاب شد.\n\n"
        "📊 <b>درصد تخفیف را بین ۱ تا ۱۰۰ وارد کنید:</b>\n"
        "مثال: <code>20</code>",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_disc_create_auto")
async def admin_disc_create_auto_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_disc_auto_length)
    await callback.message.edit_text(
        "🎲 <b>طول کاراکترهای کد تصادفی را وارد کنید (بين ۴ تا ۱۶):</b>\n"
        "مثال: <code>8</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_auto_length, F.text)
async def admin_disc_create_auto_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        length = int(clean)
        if length < 4 or length > 16:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد بین ۴ تا ۱۶ وارد کنید.")
        return

    from db.discounts import generate_random_code, get_discount_code

    code = generate_random_code(length)
    while await get_discount_code(code):
        code = generate_random_code(length)

    await state.update_data(new_code=code)
    await state.set_state(AdminControlStates.waiting_disc_percent)
    await message.answer(
        f"🎲 کد تصادفی <code>{code}</code> تولید گردید.\n\n"
        "📊 <b>درصد تخفیف را بین ۱ تا ۱۰۰ وارد کنید:</b>\n"
        "مثال: <code>20</code>",
        parse_mode="HTML",
    )


@router.message(AdminControlStates.waiting_disc_percent, F.text)
async def admin_disc_create_percent_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        percent = int(clean)
        if percent < 1 or percent > 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح بین ۱ تا ۱۰۰ وارد کنید.")
        return

    await state.update_data(new_percent=percent)
    await state.set_state(AdminControlStates.waiting_disc_max_uses)
    await message.answer(
        f"✅ میزان تخفیف: <b>{to_persian_digits(percent)}٪</b>\n\n"
        "🔢 <b>حداکثر تعداد استفاده از این کد را وارد کنید:</b>\n"
        "(برای <b>استفاده بی‌نهایت</b> عدد <code>0</code> را ارسال کنید)\n"
        "مثال: <code>50</code> یا <code>0</code>",
        parse_mode="HTML",
    )


@router.message(AdminControlStates.waiting_disc_max_uses, F.text)
async def admin_disc_create_max_uses_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        max_uses = int(clean)
        if max_uses < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید (0 برای بی‌نهایت).")
        return

    data = await state.get_data()
    code = data["new_code"]
    percent = data["new_percent"]
    uses_value = -1 if max_uses == 0 else max_uses

    from db.discounts import create_discount_code

    success = await create_discount_code(code, percent, uses_value)
    await state.clear()

    if success:
        panel_text, keyboard = await _build_pricing_panel()
        max_str = "بی‌نهایت" if uses_value == -1 else f"{to_persian_digits(uses_value)}"
        await message.answer(
            f"✅ کد تخفیف <code>{code}</code> با <b>{to_persian_digits(percent)}٪</b> تخفیف "
            f"و ظرفیت <b>{max_str}</b> ساخته شد!\n\n{panel_text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await message.answer("❌ خطا در ثبت کد تخفیف. ممکن است کد تکراری باشد.")


@router.callback_query(F.data.startswith("admin_disc_view_"))
async def admin_disc_view(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return
    await state.clear()

    code = callback.data[len("admin_disc_view_") :]
    from db.discounts import get_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await callback.answer("❌ کد تخفیف یافت نشد.", show_alert=True)
        return

    status_str = "🟢 فعال" if dc["is_active"] else "🔴 غیرفعال"
    max_uses_str = (
        "بی‌نهایت"
        if (dc["max_uses"] is None or dc["max_uses"] <= 0 or dc["max_uses"] == -1)
        else f"{to_persian_digits(dc['max_uses'])}"
    )

    text = (
        f"🏷️ <b>جزئیات کد تخفیف:</b> <code>{dc['code']}</code>\n\n"
        f"📊 <b>میزان تخفیف:</b> {to_persian_digits(dc['discount_percent'])}٪\n"
        f"🔢 <b>میزان استفاده:</b> {to_persian_digits(dc['used_count'])} از {max_uses_str}\n"
        f"🔘 <b>وضعیت:</b> {status_str}\n\n"
        "جهت تغییر ویژگی‌ها یا حذف، گزینه مورد نظر را انتخاب کنید:"
    )

    toggle_btn_text = "🔴 غیرفعال‌سازی" if dc["is_active"] else "🟢 فعال‌سازی"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_btn_text,
                    callback_data=f"admin_disc_toggle_{dc['code']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 تغییر درصد تخفیف",
                    callback_data=f"admin_disc_edit_p_{dc['code']}",
                ),
                InlineKeyboardButton(
                    text="⏱ تغییر سقف استفاده",
                    callback_data=f"admin_disc_edit_m_{dc['code']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑️ حذف کد تخفیف", callback_data=f"admin_disc_del_{dc['code']}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به لیست کدهای تخفیف",
                    callback_data="admin_discounts_menu",
                ),
            ],
        ]
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_disc_toggle_"))
async def admin_disc_toggle(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return

    code = callback.data[len("admin_disc_toggle_") :]
    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await callback.answer("❌ کد تخفیف یافت نشد.", show_alert=True)
        return

    new_status = not bool(dc["is_active"])
    await update_discount_code(code, is_active=new_status)

    status_msg = "فعال" if new_status else "غیرفعال"
    await callback.answer(f"✅ کد تخفیف {code} {status_msg} شد.")

    await admin_disc_view(callback, state)


@router.callback_query(F.data.startswith("admin_disc_del_"))
async def admin_disc_delete(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return

    code = callback.data[len("admin_disc_del_") :]
    from db.discounts import delete_discount_code

    deleted = await delete_discount_code(code)
    if deleted:
        await callback.answer(f"✅ کد تخفیف {code} حذف شد.", show_alert=True)
    else:
        await callback.answer("❌ خطا در حذف کد تخفیف.", show_alert=True)

    await admin_discounts_menu(callback, state)


@router.callback_query(F.data.startswith("admin_disc_edit_p_"))
async def admin_disc_edit_percent_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    code = callback.data[len("admin_disc_edit_p_") :]
    await state.set_state(AdminControlStates.waiting_disc_edit_percent)
    await state.update_data(edit_code=code)
    await callback.message.edit_text(
        f"📊 <b>درصد جدید تخفیف را برای کد <code>{code}</code> (۱ تا ۱۰۰) وارد کنید:</b>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_edit_percent, F.text)
async def admin_disc_edit_percent_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        val = int(clean)
        if val < 1 or val > 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح بین ۱ تا ۱۰۰ وارد کنید.")
        return

    data = await state.get_data()
    code = data.get("edit_code")
    if not code:
        await state.clear()
        return

    from db.discounts import update_discount_code

    await update_discount_code(code, discount_percent=val)
    await state.clear()
    panel_text, keyboard = await _build_pricing_panel()
    await message.answer(
        f"✅ درصد تخفیف کد <code>{code}</code> به <b>{to_persian_digits(val)}٪</b> تغییر یافت.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_disc_edit_m_"))
async def admin_disc_edit_max_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    code = callback.data[len("admin_disc_edit_m_") :]
    await state.set_state(AdminControlStates.waiting_disc_edit_max_uses)
    await state.update_data(edit_code=code)
    await callback.message.edit_text(
        f"⏱ <b>حداکثر سقف استفاده جدید برای کد <code>{code}</code> را وارد کنید:</b>\n"
        "(جهت استفاده <b>بی‌نهایت</b> عدد <code>0</code> را وارد کنید)\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_edit_max_uses, F.text)
async def admin_disc_edit_max_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        val = int(clean)
        if val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید (0 برای بی‌نهایت).")
        return

    data = await state.get_data()
    code = data.get("edit_code")
    if not code:
        await state.clear()
        return

    from db.discounts import update_discount_code

    uses_value = -1 if val == 0 else val
    await update_discount_code(code, max_uses=uses_value)
    await state.clear()
    panel_text, keyboard = await _build_pricing_panel()
    max_str = "بی‌نهایت" if uses_value == -1 else f"{to_persian_digits(uses_value)}"
    await message.answer(
        f"✅ سقف استفاده از کد <code>{code}</code> به <b>{max_str}</b> تغییر یافت.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_ref_menu")
async def admin_ref_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return
    await state.clear()

    from db.models import get_referral_config

    config = await get_referral_config()
    status_str = "🟢 فعال" if config["enabled"] else "🔴 غیرفعال"
    toggle_text = "🔴 غیرفعال‌سازی سیستم" if config["enabled"] else "🟢 فعال‌سازی سیستم"
    percent_str = to_persian_digits(config["percent"])

    text = (
        f"👥 <b>تنظیمات سیستم زیرمجموعه‌گیری (دعوت از دوستان)</b>\n\n"
        f"🔘 <b>وضعیت فعلی سیستم:</b> {status_str}\n"
        f"📊 <b>درصد پورسانت فعلی:</b> <b>{percent_str}٪</b>\n\n"
        f"جهت تغییر هر یک از موارد، گزینه مربوطه را انتخاب کنید:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_text, callback_data="admin_ref_toggle"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 تغییر درصد پورسانت",
                    callback_data="admin_ref_edit_percent",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
                ),
            ],
        ]
    )

    from utils.helpers import safe_edit_text

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_ref_toggle")
async def admin_ref_toggle(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return

    from db.models import get_referral_config, set_referral_config

    config = await get_referral_config()
    new_status = not config["enabled"]
    await set_referral_config(enabled=new_status)

    msg = "فعال" if new_status else "غیرفعال"
    await callback.answer(f"✅ سیستم زیرمجموعه‌گیری {msg} گردید.")

    await admin_ref_menu(callback, state)


@router.callback_query(F.data == "admin_ref_edit_percent")
async def admin_ref_edit_percent_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    await state.set_state(AdminControlStates.waiting_ref_percent)
    await callback.message.edit_text(
        "📊 <b>درصد جدید پورسانت دعوت (۱ تا ۱۰۰) را وارد کنید:</b>\n"
        "مثال: <code>15</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_ref_percent, F.text)
async def admin_ref_edit_percent_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        val = int(clean)
        if val < 1 or val > 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح بین ۱ تا ۱۰۰ وارد کنید.")
        return

    from db.models import set_referral_config

    await set_referral_config(percent=val)
    await state.clear()
    panel_text, keyboard = await _build_pricing_panel()
    await message.answer(
        f"✅ درصد پورسانت زیرمجموعه‌گیری به <b>{to_persian_digits(val)}٪</b> تغییر یافت.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_inbounds_menu")
async def admin_inbounds_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return
    await state.clear()

    from db.models import get_active_inbound_ids
    from services import xui_api

    inbounds = await xui_api.list_inbounds()
    assigned_ids = set(await get_active_inbound_ids())

    if not inbounds:
        text = "📡 <b>هیچ اینباندی روی سرور یافت نشد یا ارتباط با پنل برقرار نیست.</b>"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
                    )
                ]
            ]
        )
        from utils.helpers import safe_edit_text

        await safe_edit_text(
            callback.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await callback.answer()
        return

    inbound_lines = []
    keyboard_rows = []

    for ib in inbounds:
        ib_id = ib.get("id")
        remark = ib.get("remark") or ib.get("tag") or f"Inbound #{ib_id}"
        protocol = ib.get("protocol", "").upper()
        port = ib.get("port", 0)
        is_enabled = ib.get("enable", True)
        is_assigned = ib_id in assigned_ids

        status_icon = "🟢 فعال" if is_enabled else "🔴 غیرفعال"
        assign_icon = "⭐️ اختصاصی مشتری" if is_assigned else "⚪️ غیر اختصاصی"

        inbound_lines.append(
            f"• <b>#{ib_id} | {remark}</b> ({protocol}:{port})\n"
            f"  وضعیت پنل: {status_icon} | وضعیت مشتری: {assign_icon}"
        )

        enable_btn_text = (
            f"🔴 غیرفعال‌سازی #{ib_id}" if is_enabled else f"🟢 فعال‌سازی #{ib_id}"
        )
        assign_btn_text = (
            f"⭐️ لغو اختصاص #{ib_id}" if is_assigned else f"➕ اختصاص به مشتری #{ib_id}"
        )

        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=enable_btn_text,
                    callback_data=f"admin_inbound_toggle_enable_{ib_id}",
                ),
                InlineKeyboardButton(
                    text=assign_btn_text,
                    callback_data=f"admin_inbound_toggle_assign_{ib_id}",
                ),
            ]
        )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
            )
        ]
    )

    text = (
        "📡 <b>مدیریت اینباندهای سرور (Inbounds)</b>\n\n"
        "در این بخش می‌توانید وضعیت فعال/غیرفعال بودن هر اینباند در پنل X-UI و همچنین تعیین اینباندهای اختصاصی برای مشتریان را مدیریت کنید.\n\n"
        + "\n\n".join(inbound_lines)
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    from utils.helpers import safe_edit_text

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_inbound_toggle_enable_"))
async def admin_inbound_toggle_enable(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    inbound_id = int(callback.data.split("_")[-1])
    from services import xui_api

    inbound = await xui_api.get_inbound(inbound_id)
    if not inbound:
        await callback.answer("❌ اینباند یافت نشد.", show_alert=True)
        return

    current_enable = inbound.get("enable", True)
    new_enable = not current_enable

    await xui_api.set_inbound_enable(inbound_id, new_enable)

    msg = "فعال" if new_enable else "غیرفعال"
    await callback.answer(f"✅ اینباند #{inbound_id} {msg} گردید.", show_alert=True)

    await admin_inbounds_menu(callback, state)


@router.callback_query(F.data.startswith("admin_inbound_toggle_assign_"))
async def admin_inbound_toggle_assign(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    inbound_id = int(callback.data.split("_")[-1])
    from db.models import toggle_assigned_inbound_id

    new_list = await toggle_assigned_inbound_id(inbound_id)

    is_assigned = inbound_id in new_list
    status_str = (
        "به مشتریان اختصاص یافت" if is_assigned else "از اختصاص مشتریان خارج شد"
    )
    await callback.answer(f"✅ اینباند #{inbound_id} {status_str}.", show_alert=True)

    await admin_inbounds_menu(callback, state)


@router.callback_query(F.data == "admin_groups_menu")
async def admin_groups_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return
    await state.clear()

    from db.models import get_active_client_group
    from services import xui_api

    groups = await xui_api.list_client_groups()
    active_group = await get_active_client_group()

    group_lines = []
    keyboard_rows = [
        [
            InlineKeyboardButton(
                text="➕ ایجاد گروه جدید", callback_data="admin_group_create"
            )
        ]
    ]

    active_info = (
        f"⭐️ <b>گروه فعال برای خریدهای جدید:</b> <code>{active_group}</code>"
        if active_group
        else "⚪️ <b>گروه فعال برای خریدهای جدید:</b> هیچ گروهی انتخاب نشده است (بدون گروه)"
    )

    if groups:
        for grp in groups:
            g_name = grp.get("name", "")
            if not g_name:
                continue
            m_count = grp.get("memberCount", 0)
            is_active = g_name == active_group
            star = " ⭐️ (گروه فعال)" if is_active else ""

            group_lines.append(
                f"• <b>{g_name}</b> ({to_persian_digits(m_count)} عضو){star}"
            )

            row = []
            if not is_active:
                row.append(
                    InlineKeyboardButton(
                        text="⭐️ انتخاب برای خرید",
                        callback_data=f"admin_group_select_{g_name}",
                    )
                )
            else:
                row.append(
                    InlineKeyboardButton(
                        text="❌ لغو انتخاب",
                        callback_data="admin_group_deselect",
                    )
                )

            row.append(
                InlineKeyboardButton(
                    text="✏️ ویرایش نام",
                    callback_data=f"admin_group_rename_{g_name}",
                )
            )
            row.append(
                InlineKeyboardButton(
                    text="🗑 حذف",
                    callback_data=f"admin_group_delete_{g_name}",
                )
            )
            keyboard_rows.append(row)
    else:
        group_lines.append("<i>هیچ گروهی روی پنل تعریف نشده است.</i>")

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
            )
        ]
    )

    text = (
        "👥 <b>مدیریت گروه‌های مشتری (Client Groups)</b>\n\n"
        "در این بخش می‌توانید گروه‌های مشتریان را تعریف و مدیریت کنید. "
        "همچنین می‌توانید <b>تنها یک گروه</b> را به عنوان گروه فعال انتخاب کنید تا کلیه خریدهای جدید مشتریان به طور خودکار عضو آن گروه شوند.\n\n"
        f"{active_info}\n\n"
        "<b>لیست گروه‌ها:</b>\n" + "\n".join(group_lines)
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    from utils.helpers import safe_edit_text

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_group_create")
async def admin_group_create_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    await state.set_state(AdminControlStates.waiting_group_create_name)
    await callback.message.edit_text(
        "👥 <b>نام گروه جدید را وارد کنید:</b>\n"
        "مثال: <code>VIP</code> یا <code>Customers</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_group_create_name, F.text)
async def admin_group_create_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    g_name = message.text.strip()
    from services import xui_api

    try:
        await xui_api.create_client_group(g_name)
        await state.clear()
        panel_text, keyboard = await _build_pricing_panel()
        await message.answer(
            f"✅ گروه جدید <b>{g_name}</b> با موفقیت ایجاد گردید.\n\n{panel_text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Failed to create group %s: %s", g_name, e)
        await message.answer(f"❌ خطا در ساخت گروه: {e}")


@router.callback_query(F.data.startswith("admin_group_select_"))
async def admin_group_select(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return

    g_name = callback.data[len("admin_group_select_") :]
    from db.models import set_active_client_group

    await set_active_client_group(g_name)
    await callback.answer(
        f"✅ گروه «{g_name}» به عنوان گروه فعال خریدهای جدید انتخاب شد.",
        show_alert=True,
    )
    await admin_groups_menu(callback, state)


@router.callback_query(F.data == "admin_group_deselect")
async def admin_group_deselect(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    from db.models import set_active_client_group

    await set_active_client_group("")
    await callback.answer(
        "✅ اختصاص گروه برای خریدهای جدید لغو گردید.", show_alert=True
    )
    await admin_groups_menu(callback, state)


@router.callback_query(F.data.startswith("admin_group_rename_"))
async def admin_group_rename_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    old_name = callback.data[len("admin_group_rename_") :]
    await state.update_data(old_group_name=old_name)
    await state.set_state(AdminControlStates.waiting_group_rename_name)
    await callback.message.edit_text(
        f"✏️ <b>نام جدید برای گروه «{old_name}» را وارد کنید:</b>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_group_rename_name, F.text)
async def admin_group_rename_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    new_name = message.text.strip()
    data = await state.get_data()
    old_name = data.get("old_group_name")

    if not old_name:
        await state.clear()
        return

    from db.models import get_active_client_group, set_active_client_group
    from services import xui_api

    try:
        await xui_api.rename_client_group(old_name, new_name)
        active_group = await get_active_client_group()
        if active_group == old_name:
            await set_active_client_group(new_name)

        await state.clear()
        panel_text, keyboard = await _build_pricing_panel()
        await message.answer(
            f"✅ نام گروه <b>{old_name}</b> به <b>{new_name}</b> تغییر یافت.\n\n{panel_text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Failed to rename group %s: %s", old_name, e)
        await message.answer(f"❌ خطا در ویرایش نام گروه: {e}")


@router.callback_query(F.data.startswith("admin_group_delete_"))
async def admin_group_delete(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return

    g_name = callback.data[len("admin_group_delete_") :]
    from db.models import get_active_client_group, set_active_client_group
    from services import xui_api

    try:
        await xui_api.delete_client_group(g_name)
        active_group = await get_active_client_group()
        if active_group == g_name:
            await set_active_client_group("")

        await callback.answer(f"✅ گروه «{g_name}» حذف گردید.", show_alert=True)
    except Exception as e:
        logger.error("Failed to delete group %s: %s", g_name, e)
        await callback.answer(f"❌ خطا در حذف گروه: {e}", show_alert=True)

    await admin_groups_menu(callback, state)


@router.callback_query(F.data == "admin_card_menu")
async def admin_card_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return
    await state.clear()

    from db.models import get_card_config

    card_config = await get_card_config()
    card_number = card_config["card_number"] or "تنظیم نشده"
    card_holder = card_config["card_holder"] or "تنظیم نشده"

    text = (
        f"💳 <b>تنظیمات کارت بانکی جهت واریز کارت به کارت</b>\n\n"
        f"🔢 <b>شماره کارت فعلی:</b> <code>{card_number}</code>\n"
        f"👤 <b>نام صاحب کارت فعلی:</b> <b>{card_holder}</b>\n\n"
        f"لطفاً یکی از گزینه‌های زیر را برای تغییر انتخاب کنید:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔢 تغییر شماره کارت",
                    callback_data="admin_card_edit_number",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 تغییر نام صاحب کارت",
                    callback_data="admin_card_edit_holder",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
                )
            ],
        ]
    )

    from utils.helpers import safe_edit_text

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_card_edit_number")
async def admin_card_edit_number_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    await state.set_state(AdminControlStates.waiting_card_number)
    await callback.message.edit_text(
        "🔢 <b>شماره کارت ۱۶ رقمی جدید را وارد کنید:</b>\n"
        "مثال: <code>6037991812345678</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_card_number, F.text)
async def admin_card_edit_number_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    clean_num = (
        persian_to_english_digits(message.text.strip())
        .replace(" ", "")
        .replace("-", "")
    )
    if len(clean_num) != 16 or not clean_num.isdigit():
        await message.answer("❌ لطفاً یک شماره کارت ۱۶ رقمی معتبر وارد کنید.")
        return

    from db.models import set_card_config

    await set_card_config(card_number=clean_num)
    await state.clear()

    panel_text, keyboard = await _build_pricing_panel()
    await message.answer(
        f"✅ شماره کارت جدید (<code>{clean_num}</code>) با موفقیت ذخیره شد.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_card_edit_holder")
async def admin_card_edit_holder_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    await state.set_state(AdminControlStates.waiting_card_holder)
    await callback.message.edit_text(
        "👤 <b>نام و نام‌خانوادگی صاحب کارت را وارد کنید:</b>\n"
        "مثال: <code>رضا محمدی</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_card_holder, F.text)
async def admin_card_edit_holder_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    holder_name = message.text.strip()
    from db.models import set_card_config

    await set_card_config(card_holder=holder_name)
    await state.clear()

    panel_text, keyboard = await _build_pricing_panel()
    await message.answer(
        f"✅ نام صاحب کارت (<b>{holder_name}</b>) با موفقیت ذخیره شد.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
