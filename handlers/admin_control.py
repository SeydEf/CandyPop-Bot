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


def _is_admin(event: types.CallbackQuery | types.Message) -> bool:
    return event.from_user is not None and event.from_user.id == ADMIN_CHAT_ID


async def _build_pricing_panel() -> tuple[str, InlineKeyboardMarkup]:
    config = await get_pricing_config()
    test_config = await get_test_sub_config()

    base_rate = config["base_gb_rate"]
    user_surcharge = config["user_surcharge"]
    dur_surcharges: dict[int, int] = config["duration_surcharges"]
    volume_tiers: list[tuple[int, int]] = config["volume_tiers"]
    fallback_rate: int = config["fallback_gb_rate"]

    test_gb = test_config["gb"]
    test_dur = test_config["duration_days"]
    test_cool = test_config["cooldown_days"]

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

    text = (
        f"⚙️ <b>پنل مدیریت و تنظیمات ربات</b>\n\n"
        f"💵 <b>نرخ پایه هر گیگ:</b> {format_price(base_rate)}\n"
        f"👤 <b>هزینه هر کاربر اضافه:</b> +{format_price(user_surcharge)}\n\n"
        f"⏱ <b>حق‌الزحمه مدت زمان:</b>\n{dur_text}\n"
        f"📊 <b>پله‌های تخفیف حجم:</b>\n{tiers_text}\n"
        f"🎁 <b>اشتراک تست رایگان:</b>\n{test_text}"
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
    await callback.message.edit_text(
        f"✅ امکان دریافت اشتراک تست برای <b>{to_persian_digits(count)} کاربر</b> با موفقیت بازنشانی شد.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()
