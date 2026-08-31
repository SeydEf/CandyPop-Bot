from __future__ import annotations

import logging

from aiogram import Bot, F, Router, types
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
from utils.helpers import safe_edit_text

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
    waiting_test_reset_user = State()

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

    waiting_alert_gb = State()
    waiting_alert_days = State()
    waiting_alert_delete_days = State()
    waiting_alert_interval = State()
    waiting_ip_checker_interval = State()
    waiting_add_admin_id = State()
    waiting_start_msg_content = State()
    waiting_start_msg_button_title = State()
    waiting_start_msg_button_url = State()
    waiting_pricing_content = State()
    waiting_pricing_caption = State()
    waiting_channel_lock_id = State()
    waiting_channel_lock_link = State()


def _is_owner(event: types.CallbackQuery | types.Message) -> bool:
    from db.models import is_owner

    return event.from_user is not None and is_owner(event.from_user.id)


async def _is_admin(event: types.CallbackQuery | types.Message) -> bool:
    from db.models import is_admin

    return event.from_user is not None and await is_admin(event.from_user.id)


async def _require_owner(callback: types.CallbackQuery) -> bool:
    if not _is_owner(callback):
        await callback.answer(
            "⛔️ این بخش اختصاصی مدیریت ارشد (مالک ربات) می‌باشد.", show_alert=True
        )
        return False
    return True


async def _require_permission(
    event: types.CallbackQuery | types.Message, perm_key: str
) -> bool:
    from db.models import PERMISSION_TITLES, has_admin_permission

    if event.from_user is None:
        return False

    tg_id = event.from_user.id
    permitted = await has_admin_permission(tg_id, perm_key)
    if not permitted:
        title = PERMISSION_TITLES.get(perm_key, perm_key)
        msg = f"⛔️ شما دسترسی به بخش «{title}» را ندارید."
        if isinstance(event, types.CallbackQuery):
            await event.answer(msg, show_alert=True)
        else:
            await event.answer(msg)
        return False
    return True


async def _build_pricing_panel() -> tuple[str, InlineKeyboardMarkup]:
    config = await get_pricing_config()
    test_config = await get_test_sub_config()
    from db.models import (
        get_alert_config,
        get_card_config,
        get_channel_lock_config,
        get_pricing_display_config,
        get_referral_config,
        get_shop_status,
        get_start_first_use_config,
        get_start_message_config,
    )

    ref_config = await get_referral_config()
    card_config = await get_card_config()
    alert_config = await get_alert_config()
    shop_status = await get_shop_status()
    start_msg_config = await get_start_message_config()
    pricing_disp_config = await get_pricing_display_config()
    channel_lock_config = await get_channel_lock_config()
    channel_lock_status_str = (
        "🟢 فعال" if channel_lock_config["enabled"] else "🔴 غیرفعال"
    )
    start_first_use_enabled = await get_start_first_use_config()
    start_first_use_str = (
        "🟢 از اولین اتصال" if start_first_use_enabled else "🔴 از زمان خرید"
    )

    pur_status_str = (
        "🟢 باز (فعال)" if shop_status["purchases_enabled"] else "🔴 بسته (غیرفعال)"
    )
    ren_status_str = (
        "🟢 باز (فعال)" if shop_status["renewals_enabled"] else "🔴 بسته (غیرفعال)"
    )

    card_num = card_config["card_number"] or "تنظیم نشده"
    card_own = card_config["card_holder"] or "تنظیم نشده"

    min_gb_alert = float(alert_config["min_gb"])
    min_days_alert = int(alert_config["min_days"])
    auto_delete_days = int(alert_config["auto_delete_days"])
    interval_minutes = int(alert_config.get("interval_minutes", 30))
    low_gb_enabled = bool(alert_config.get("low_gb_enabled", True))
    expiring_days_enabled = bool(alert_config.get("expiring_days_enabled", True))

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

    del_days_str = (
        f"{to_persian_digits(auto_delete_days)} روز پس از انقضا"
        if auto_delete_days > 0
        else "🔴 غیرفعال"
    )

    low_gb_status = "🟢 فعال" if low_gb_enabled else "🔴 غیرفعال"
    exp_days_status = "🟢 فعال" if expiring_days_enabled else "🔴 غیرفعال"

    alert_text = (
        f"  • فاصله زمان بررسی (پایش): هر {to_persian_digits(interval_minutes)} دقیقه\n"
        f"  • هشدار پله‌ای حجم (۱ گیگی): {low_gb_status} (کمتر از {format_size_gb(min_gb_alert)})\n"
        f"  • یادآور روزانه زمان: {exp_days_status} (کمتر از {to_persian_digits(min_days_alert)} روز)\n"
        f"  • حذف منقضی‌شده‌ها: {del_days_str}\n"
    )

    start_msg_status = "🟢 فعال" if start_msg_config["enabled"] else "🔴 غیرفعال"
    pricing_disp_status = "🟢 فعال" if pricing_disp_config["enabled"] else "🔴 غیرفعال"

    text = (
        f"⚙️ <b>پنل مدیریت و تنظیمات ربات</b>\n\n"
        f"🛒 <b>فروش جدید:</b> {pur_status_str}\n"
        f"🔄 <b>تمدید اشتراک:</b> {ren_status_str}\n\n"
        f"💵 <b>نرخ پایه هر گیگ:</b> {format_price(base_rate)}\n"
        f"👤 <b>هزینه هر کاربر اضافه:</b> +{format_price(user_surcharge)}\n\n"
        f"💳 <b>کارت جهت واریز:</b> <code>{card_num}</code> ({card_own})\n\n"
        f"🔔 <b>حدآستانه هشدارهای اتمام سرویس:</b>\n{alert_text}\n"
        f"⏱ <b>حق‌الزحمه مدت زمان:</b>\n{dur_text}\n"
        f"📊 <b>پله‌های تخفیف حجم:</b>\n{tiers_text}\n"
        f"🎁 <b>اشتراک تست رایگان:</b>\n{test_text}\n"
        f"👥 <b>سیستم زیرمجموعه‌گیری:</b>\n{ref_text}\n"
        f"📩 <b>پیام پس از استارت:</b> {start_msg_status}\n"
        f"📢 <b>عضویت اجباری کانال:</b> {channel_lock_status_str}\n"
        f"💰 <b>بخش تعرفه‌ها:</b> {pricing_disp_status}\n"
        f"⏳ <b>شروع اعتبار کانفیگ:</b> {start_first_use_str}"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 آمار و گزارشات جامع ربات",
                    callback_data="admin_stats_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🛒/🔄 وضعیت فروش و تمدید اشتراک",
                    callback_data="admin_shop_status_menu",
                ),
            ],
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
                    text="🖼️ تنظیم متن و تصویر تعرفه‌ها",
                    callback_data="admin_pricing_disp_menu",
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
                    text="🎁 هدیه همگانی (حجم و زمان به همه کاربران)",
                    callback_data="admin_bulk_gift_menu",
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
                    text="🔔 تنظیمات هشدارهای اتمام حجم و زمان",
                    callback_data="admin_alert_menu",
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
                    text="👥 مدیریت مدیران ربات (ادمین‌ها) 👑",
                    callback_data="admin_manage_admins_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📢 ارسال پیام همگانی (اطلاعیه)",
                    callback_data="admin_broadcast_start",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📩 تنظیم پیام پس از استارت",
                    callback_data="admin_start_msg_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📢 تنظیمات عضویت اجباری کانال",
                    callback_data="admin_channel_lock_menu",
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
                    text="👥 لیست و مدیریت تمام کاربران",
                    callback_data="admin_users_list_0",
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
                    text="👤 بازنشانی تست یک کاربر مشخص",
                    callback_data="admin_test_reset_user",
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
    if not await _is_admin(message):
        return
    await state.clear()
    text, keyboard = await _build_pricing_panel()
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "admin_price_main")
async def admin_pricing_main(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
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
    if not await _require_permission(callback, "pricing"):
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
    if not await _require_permission(message, "pricing"):
        return
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
    if not await _require_permission(callback, "pricing"):
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
    if not await _require_permission(message, "pricing"):
        return
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
    if not await _require_permission(callback, "pricing"):
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
    if not await _require_permission(callback, "pricing"):
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
    if not await _require_permission(callback, "pricing"):
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
    if not await _require_permission(callback, "pricing"):
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
    if not await _require_permission(callback, "reset_configs"):
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
    if not await _require_permission(callback, "test_sub"):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _require_permission(callback, "reset_configs"):
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


@router.callback_query(F.data == "admin_test_reset_user")
async def admin_test_reset_user_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "reset_configs"):
        return
    await state.set_state(AdminControlStates.waiting_test_reset_user)
    await safe_edit_text(
        callback.message,
        "👤 <b>لطفاً شناسه عددی تلگرام (Telegram ID) یا نام کاربری کاربر را جهت بازنشانی اشتراک تست وارد کنید:</b>\n\n"
        "مثال: <code>123456789</code> یا <code>@username</code>\n\n"
        "<i>برای انصراف /cancel را بزنید.</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_test_reset_user, F.text)
async def admin_test_reset_user_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    query = message.text.strip()
    from db.models import reset_user_test_sub, search_users

    users = await search_users(query)
    if not users:
        await message.answer(
            f"❌ هیچ کاربری با شناسه یا نام کاربری «<code>{query}</code>» در دیتابیس ربات یافت نشد.\n"
            "لطفاً دوباره تلاش کنید یا /cancel را ارسال فرمایید.",
            parse_mode="HTML",
        )
        return

    target_user = users[0]
    tg_id = target_user["tg_id"]

    success = await reset_user_test_sub(tg_id)
    await state.clear()

    panel_text, keyboard = await _build_pricing_panel()
    if success:
        await message.answer(
            f"✅ <b>امکان دریافت اشتراک تست برای کاربر (<code>{tg_id}</code>) با موفقیت بازنشانی شد.</b>\n\n{panel_text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"⚠️ عملیات انجام شد اما تغییر در وضعیت کاربر <code>{tg_id}</code> اعمال نگردید.\n\n{panel_text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )


@router.callback_query(F.data == "admin_discounts_menu")
async def admin_discounts_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "discounts"):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _require_permission(callback, "referral"):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _require_permission(callback, "inbounds"):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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
    if not await _require_permission(callback, "card_config"):
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
    if not await _is_admin(callback):
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
    if not await _is_admin(callback):
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


@router.callback_query(F.data == "admin_alert_menu")
async def admin_alert_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _require_permission(callback, "alerts"):
        return
    await state.clear()

    from db.models import get_alert_config, get_ip_checker_config

    alert_config = await get_alert_config()
    ip_config = await get_ip_checker_config()
    scheduler_enabled = bool(alert_config.get("enabled", True))
    min_gb = float(alert_config["min_gb"])
    min_days = int(alert_config["min_days"])
    auto_delete_days = int(alert_config["auto_delete_days"])
    interval_minutes = int(alert_config.get("interval_minutes", 30))
    ip_interval_minutes = int(ip_config.get("interval_minutes", 5))
    low_gb_enabled = bool(alert_config.get("low_gb_enabled", True))
    expiring_days_enabled = bool(alert_config.get("expiring_days_enabled", True))
    expired_notice_enabled = bool(alert_config.get("expired_notice_enabled", True))
    auto_delete_enabled = bool(alert_config.get("auto_delete_enabled", True))
    ip_checker_enabled = bool(ip_config.get("enabled", True))

    del_str = (
        f"<b>{to_persian_digits(auto_delete_days)} روز پس از انقضا</b>"
        if auto_delete_days > 0 and auto_delete_enabled
        else "<b>🔴 غیرفعال (بدون حذف)</b>"
    )

    scheduler_btn_text = (
        "🟢 زمان‌بند هشدارهای خودکار (فعال)"
        if scheduler_enabled
        else "🔴 زمان‌بند هشدارهای خودکار (غیرفعال)"
    )
    low_gb_btn_text = (
        "🟢 هشدار پله‌ای حجم (فعال)"
        if low_gb_enabled
        else "🔴 هشدار پله‌ای حجم (غیرفعال)"
    )
    exp_days_btn_text = (
        "🟢 یادآور روزانه زمان (فعال)"
        if expiring_days_enabled
        else "🔴 یادآور روزانه زمان (غیرفعال)"
    )
    exp_notice_btn_text = (
        "🟢 ارسال اخطار انقضا (فعال)"
        if expired_notice_enabled
        else "🔴 ارسال اخطار انقضا (غیرفعال)"
    )
    auto_del_btn_text = (
        "🟢 حذف خودکار منقضی‌ها (فعال)"
        if auto_delete_enabled
        else "🔴 حذف خودکار منقضی‌ها (غیرفعال)"
    )
    ip_btn_text = (
        "🟢 پایش سقف IP کاربر (فعال)"
        if ip_checker_enabled
        else "🔴 پایش سقف IP کاربر (غیرفعال)"
    )

    text = (
        f"🔔 <b>تنظیمات حدآستانه هشدارهای هوشمند و پاکسازی اشتراک‌ها</b>\n\n"
        f"ربات به صورت خودکار کاربران را پیش از اتمام سرویس یا در صورت تخلف IP آگاه می‌سازد.\n\n"
        f"🔘 <b>وضعیت کلی زمان‌بند هشدارها:</b> {scheduler_btn_text}\n"
        f"⏳ <b>فاصله زمان بررسی پایش هشدارهای عمومی:</b> هر <b>{to_persian_digits(interval_minutes)} دقیقه</b>\n"
        f"🛡 <b>فاصله زمان پایش سقف IP:</b> هر <b>{to_persian_digits(ip_interval_minutes)} دقیقه</b>\n"
        f"🛡 <b>وضعیت پایش سقف IP (تخلفات):</b> {ip_btn_text}\n"
        f"📊 <b>هشدار پله‌ای حجم (هر ۱ گیگ):</b> {low_gb_btn_text}\n"
        f"⏱ <b>حدآستانه هشدار ترافیک:</b> کمتر از <b>{format_size_gb(min_gb)}</b>\n"
        f"📅 <b>یادآور روزانه اتمام زمان:</b> {exp_days_btn_text}\n"
        f"⏱ <b>حدآستانه هشدار انقضا:</b> کمتر از <b>{to_persian_digits(min_days)} روز</b>\n"
        f"⛔️ <b>ارسال اخطار انقضای سرویس:</b> {exp_notice_btn_text}\n"
        f"🗑 <b>حذف خودکار سرویس‌های منقضی:</b> {auto_del_btn_text}\n"
        f"🗑 <b>مهلت حذف اشتراک‌های منقضی‌شده:</b> {del_str}\n\n"
        f"لطفاً یکی از گزینه‌های زیر را جهت تغییر انتخاب کنید:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=scheduler_btn_text,
                    callback_data="admin_alert_toggle_scheduler",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=ip_btn_text, callback_data="admin_alert_toggle_ip_checker"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=low_gb_btn_text, callback_data="admin_alert_toggle_low_gb"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=exp_days_btn_text,
                    callback_data="admin_alert_toggle_expiring_days",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=exp_notice_btn_text,
                    callback_data="admin_alert_toggle_expired_notice",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=auto_del_btn_text,
                    callback_data="admin_alert_toggle_auto_delete",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏳ تغییر فاصله عمومی بررسی (دقیقه)",
                    callback_data="admin_alert_edit_interval",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏳ تغییر فاصله زمان پایش IP (دقیقه)",
                    callback_data="admin_ip_edit_interval",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 تغییر حدآستانه حجم (گیگ)",
                    callback_data="admin_alert_edit_gb",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏱ تغییر حدآستانه زمان (روز)",
                    callback_data="admin_alert_edit_days",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 تغییر مهلت حذف منقضی‌شده‌ها (روز)",
                    callback_data="admin_alert_edit_delete_days",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
                )
            ],
        ]
    )

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_alert_edit_gb")
async def admin_alert_edit_gb_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    await state.set_state(AdminControlStates.waiting_alert_gb)
    await callback.message.edit_text(
        "📊 <b>حدآستانه جدید هشدار ترافیک (به گیگابایت) را وارد کنید:</b>\n"
        "مثال: <code>2.0</code> یا <code>1.5</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_alert_gb, F.text)
async def admin_alert_edit_gb_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        gb_val = float(persian_to_english_digits(message.text.strip()))
        if gb_val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد معتبر به گیگابایت وارد کنید.")
        return

    from db.models import set_alert_config

    await set_alert_config(min_gb=gb_val)
    await state.clear()

    panel_text, keyboard = await _build_pricing_panel()
    await message.answer(
        f"✅ حدآستانه جدید ترافیک (<b>{format_size_gb(gb_val)}</b>) با موفقیت ذخیره شد.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_alert_edit_days")
async def admin_alert_edit_days_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    await state.set_state(AdminControlStates.waiting_alert_days)
    await callback.message.edit_text(
        "⏱ <b>حدآستانه جدید هشدار انقضا (به روز) را وارد کنید:</b>\n"
        "مثال: <code>3</code> یا <code>5</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_alert_days, F.text)
async def admin_alert_edit_days_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        days_val = int(persian_to_english_digits(message.text.strip()))
        if days_val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر به روز وارد کنید.")
        return

    from db.models import set_alert_config

    await set_alert_config(min_days=days_val)
    await state.clear()

    panel_text, keyboard = await _build_pricing_panel()
    await message.answer(
        f"✅ حدآستانه جدید انقضا (<b>{to_persian_digits(days_val)} روز</b>) با موفقیت ذخیره شد.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_alert_edit_delete_days")
async def admin_alert_edit_delete_days_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    await state.set_state(AdminControlStates.waiting_alert_delete_days)
    await callback.message.edit_text(
        "🗑 <b>مهلت حذف خودکار اشتراک‌های منقضی‌شده (به روز پس از انقضا) را وارد کنید:</b>\n"
        "مثال: <code>3</code> (حذف پس از ۳ روز انقضا)\n"
        "<i>برای غیرفعال‌سازی حذف خودکار عدد <code>0</code> را ارسال کنید.</i>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_alert_delete_days, F.text)
async def admin_alert_edit_delete_days_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        del_val = int(persian_to_english_digits(message.text.strip()))
        if del_val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید.")
        return

    from db.models import set_alert_config

    await set_alert_config(auto_delete_days=del_val)
    await state.clear()

    panel_text, keyboard = await _build_pricing_panel()
    res_str = (
        f"<b>{to_persian_digits(del_val)} روز پس از انقضا</b>"
        if del_val > 0
        else "<b>غیرفعال</b>"
    )
    await message.answer(
        f"✅ مهلت جدید حذف اشتراک‌های منقضی‌شده (<b>{res_str}</b>) با موفقیت ذخیره شد.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_alert_edit_interval")
async def admin_alert_edit_interval_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    await state.set_state(AdminControlStates.waiting_alert_interval)
    await callback.message.edit_text(
        "⏳ <b>فاصله زمان جدید بررسی (پایش) سرویس‌ها (به دقیقه) را وارد کنید:</b>\n"
        "مثال: <code>15</code> یا <code>30</code> یا <code>60</code>\n"
        "<i>(حداقل ۱ دقیقه)</i>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_alert_interval, F.text)
async def admin_alert_edit_interval_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        interval_val = int(persian_to_english_digits(message.text.strip()))
        if interval_val < 1:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر (به دقیقه) وارد کنید.")
        return

    from db.models import set_alert_config

    await set_alert_config(interval_minutes=interval_val)
    await state.clear()

    panel_text, keyboard = await _build_pricing_panel()
    await message.answer(
        f"✅ فاصله زمان جدید بررسی (<b>هر {to_persian_digits(interval_val)} دقیقه</b>) با موفقیت ذخیره شد.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_alert_toggle_scheduler")
async def admin_alert_toggle_scheduler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    from db.models import get_alert_config, set_alert_config

    cfg = await get_alert_config()
    curr = bool(cfg.get("enabled", True))
    await set_alert_config(enabled=not curr)
    await admin_alert_menu(callback, state)


@router.callback_query(F.data == "admin_alert_toggle_low_gb")
async def admin_alert_toggle_low_gb(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    from db.models import get_alert_config, set_alert_config

    cfg = await get_alert_config()
    curr = bool(cfg.get("low_gb_enabled", True))
    await set_alert_config(low_gb_enabled=not curr)
    await admin_alert_menu(callback, state)


@router.callback_query(F.data == "admin_alert_toggle_expiring_days")
async def admin_alert_toggle_expiring_days(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    from db.models import get_alert_config, set_alert_config

    cfg = await get_alert_config()
    curr = bool(cfg.get("expiring_days_enabled", True))
    await set_alert_config(expiring_days_enabled=not curr)
    await admin_alert_menu(callback, state)


@router.callback_query(F.data == "admin_alert_toggle_expired_notice")
async def admin_alert_toggle_expired_notice(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    from db.models import get_alert_config, set_alert_config

    cfg = await get_alert_config()
    curr = bool(cfg.get("expired_notice_enabled", True))
    await set_alert_config(expired_notice_enabled=not curr)
    await admin_alert_menu(callback, state)


@router.callback_query(F.data == "admin_alert_toggle_auto_delete")
async def admin_alert_toggle_auto_delete(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    from db.models import get_alert_config, set_alert_config

    cfg = await get_alert_config()
    curr = bool(cfg.get("auto_delete_enabled", True))
    await set_alert_config(auto_delete_enabled=not curr)
    await admin_alert_menu(callback, state)


@router.callback_query(F.data == "admin_shop_status_menu")
async def admin_shop_status_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "shop_status"):
        return
    await state.clear()

    from db.models import get_shop_status, get_start_first_use_config
    from services.test_sub_config import get_test_sub_config

    status = await get_shop_status()
    test_config = await get_test_sub_config()
    start_first_use = await get_start_first_use_config()

    pur_enabled = status["purchases_enabled"]
    ren_enabled = status["renewals_enabled"]
    test_enabled = test_config.get("enabled", True)

    pur_text = "🟢 باز (فعال)" if pur_enabled else "🔴 بسته (موقتاً غیرفعال)"
    ren_text = "🟢 باز (فعال)" if ren_enabled else "🔴 بسته (موقتاً غیرفعال)"
    test_text = "🟢 باز (فعال)" if test_enabled else "🔴 بسته (موقتاً غیرفعال)"
    start_first_use_text = (
        "🟢 فعال (شروع از اولین اتصال)"
        if start_first_use
        else "🔴 غیرفعال (محاسبه از زمان ساخت)"
    )

    text = (
        f"🛒/🔄 <b>تنظیمات وضعیت فروش، تمدید و شروع اعتبار</b>\n\n"
        f"از این بخش می‌توانید امکان خرید اشتراک جدید، تمدید، دریافت اشتراک تست رایگان و نحوه محاسبه زمان اعتبار کانفیگ‌ها را مدیریت کنید.\n\n"
        f"🛒 <b>وضعیت فروش اشتراک جدید:</b> <b>{pur_text}</b>\n"
        f"🔄 <b>وضعیت تمدید اشتراک‌ها:</b> <b>{ren_text}</b>\n"
        f"🎁 <b>وضعیت دریافت اشتراک تست:</b> <b>{test_text}</b>\n"
        f"⏳ <b>شروع اعتبار از اولین اتصال:</b> <b>{start_first_use_text}</b>\n\n"
        f"جهت تغییر وضعیت هر بخش، روی دکمه مربوطه کلیک کنید:"
    )

    pur_btn = (
        "🟢 فروش جدید: باز (کلیک جهت بستن)"
        if pur_enabled
        else "🔴 فروش جدید: بسته (کلیک جهت بازکردن)"
    )
    ren_btn = (
        "🟢 تمدید اشتراک: باز (کلیک جهت بستن)"
        if ren_enabled
        else "🔴 تمدید اشتراک: بسته (کلیک جهت بازکردن)"
    )
    test_btn = (
        "🟢 اشتراک تست: باز (کلیک جهت بستن)"
        if test_enabled
        else "🔴 اشتراک تست: بسته (کلیک جهت بازکردن)"
    )
    start_first_use_btn = (
        "🟢 شروع از اتصال: فعال (کلیک جهت غیرفعال‌سازی)"
        if start_first_use
        else "🔴 شروع از اتصال: غیرفعال (کلیک جهت فعال‌سازی)"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=pur_btn, callback_data="admin_toggle_purchases"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=ren_btn, callback_data="admin_toggle_renewals"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=test_btn, callback_data="admin_toggle_test_sub"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=start_first_use_btn,
                    callback_data="admin_toggle_start_first_use",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
                )
            ],
        ]
    )

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_toggle_start_first_use")
async def admin_toggle_start_first_use(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    from db.models import (
        get_start_first_use_config,
        set_start_first_use_config,
    )

    curr = await get_start_first_use_config()
    new_val = not curr
    await set_start_first_use_config(new_val)
    status_str = "فعال شد 🟢" if new_val else "غیرفعال شد 🔴"
    await callback.answer(f"شروع اعتبار از اولین اتصال {status_str}")
    await admin_shop_status_menu(callback, state)


@router.callback_query(F.data == "admin_toggle_purchases")
async def admin_toggle_purchases(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    from db.models import get_shop_status, set_shop_status

    status = await get_shop_status()
    await set_shop_status(purchases_enabled=not status["purchases_enabled"])
    await admin_shop_status_menu(callback, state)


@router.callback_query(F.data == "admin_toggle_renewals")
async def admin_toggle_renewals(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    from db.models import get_shop_status, set_shop_status

    status = await get_shop_status()
    await set_shop_status(renewals_enabled=not status["renewals_enabled"])
    await admin_shop_status_menu(callback, state)


@router.callback_query(F.data == "admin_toggle_test_sub")
async def admin_toggle_test_sub(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    from services.test_sub_config import get_test_sub_config, update_test_sub_config

    test_config = await get_test_sub_config()
    current_enabled = test_config.get("enabled", True)
    await update_test_sub_config(enabled=not current_enabled)
    await admin_shop_status_menu(callback, state)


@router.callback_query(F.data == "admin_alert_toggle_ip_checker")
async def admin_alert_toggle_ip_checker(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    from db.models import get_ip_checker_config, set_ip_checker_config

    cfg = await get_ip_checker_config()
    curr = bool(cfg.get("enabled", True))
    await set_ip_checker_config(enabled=not curr)
    await admin_alert_menu(callback, state)


@router.callback_query(F.data.startswith("admin_ip_reactivate_"))
async def admin_ip_reactivate_start(callback: types.CallbackQuery) -> None:
    if not await _is_admin(callback):
        return
    email = callback.data[len("admin_ip_reactivate_") :]

    text = (
        f"⚠️ <b>تأییدیه رفع مسدودی سرویس</b>\n\n"
        f"آیا از رفع مسدودی و فعال‌سازی مجدد سرویس <code>{email}</code> مطمئن هستید؟"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟢 بله، فعال شود",
                    callback_data=f"admin_ip_confirm_reactivate_{email}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data=f"admin_ip_cancel_{email}"
                )
            ],
        ]
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_ip_confirm_reactivate_"))
async def admin_ip_confirm_reactivate(callback: types.CallbackQuery, bot: Bot) -> None:
    if not await _is_admin(callback):
        return
    email = callback.data[len("admin_ip_confirm_reactivate_") :]

    from db.models import reset_ip_violations, resolve_client_tg_id
    from services import xui_api

    try:
        client_full = await xui_api.get_client(email)
        if client_full:
            update_data = dict(client_full)
            update_data["enable"] = True
            await xui_api.update_client(email, update_data)

        await reset_ip_violations(email)

        tg_id = 0
        if client_full:
            tg_id = await resolve_client_tg_id(client_full)

        if tg_id > 0:
            try:
                user_msg = (
                    f"✅ <b>اشتراک شما مجدداً فعال گردید</b>\n\n"
                    f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n\n"
                    f"اشتراک شما توسط مدیریت از حالت مسدودی خارج و فعال گردید. لطفاً سقف استفاده همزمان دستگاه‌ها را رعایت فرمایید."
                )
                await bot.send_message(chat_id=tg_id, text=user_msg, parse_mode="HTML")
            except Exception:
                pass

        await callback.message.edit_text(
            f"✅ سرویس <code>{email}</code> با موفقیت توسط مدیریت رفع مسدودی و فعال شد.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Failed to reactivate client %s", email)
        await callback.answer(f"❌ بروز خطا: {e}", show_alert=True)

    await callback.answer()


@router.callback_query(F.data.startswith("admin_ip_delete_"))
async def admin_ip_delete_start(callback: types.CallbackQuery) -> None:
    if not await _is_admin(callback):
        return
    email = callback.data[len("admin_ip_delete_") :]

    text = (
        f"🚨 <b>تأییدیه حذف کامل سرویس مسدودشده</b>\n\n"
        f"آیا از حذف کامل سرویس <code>{email}</code> از سرور مطمئن هستید؟ این عمل غیرقابل بازگشت است."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔴 بله، حذف شود",
                    callback_data=f"admin_ip_confirm_delete_{email}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data=f"admin_ip_cancel_{email}"
                )
            ],
        ]
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_ip_confirm_delete_"))
async def admin_ip_confirm_delete(callback: types.CallbackQuery, bot: Bot) -> None:
    if not await _is_admin(callback):
        return
    email = callback.data[len("admin_ip_confirm_delete_") :]

    from db.models import reset_ip_violations, resolve_client_tg_id
    from services import xui_api

    try:
        client_full = await xui_api.get_client(email)
        tg_id = 0
        if client_full:
            tg_id = await resolve_client_tg_id(client_full)

        await xui_api.delete_client(email)
        await reset_ip_violations(email)

        if tg_id > 0:
            try:
                user_msg = (
                    f"🗑 <b>اطلاعیه حذف سرویس مسدودشده</b>\n\n"
                    f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n\n"
                    f"اشتراک فوق به دلیل عدم رعایت قوانین استفاده، توسط مدیریت از سرور حذف گردید."
                )
                await bot.send_message(chat_id=tg_id, text=user_msg, parse_mode="HTML")
            except Exception:
                pass

        await callback.message.edit_text(
            f"🗑 سرویس <code>{email}</code> با موفقیت از سرور حذف گردید.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Failed to delete client %s", email)
        await callback.answer(f"❌ بروز خطا: {e}", show_alert=True)

    await callback.answer()


@router.callback_query(F.data.startswith("admin_ip_cancel_"))
async def admin_ip_cancel(callback: types.CallbackQuery) -> None:
    if not await _is_admin(callback):
        return
    await callback.message.edit_text("❌ عملیات تعیین تکلیف سرویس لغو شد.")
    await callback.answer()


@router.callback_query(F.data == "admin_ip_edit_interval")
async def admin_ip_edit_interval_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    await state.set_state(AdminControlStates.waiting_ip_checker_interval)
    await callback.message.edit_text(
        "⏳ <b>فاصله زمان جدید پایش سقف IP (به دقیقه) را وارد کنید:</b>\n"
        "مثال: <code>3</code> یا <code>5</code> یا <code>10</code>\n"
        "<i>(حداقل ۱ دقیقه)</i>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_ip_checker_interval, F.text)
async def admin_ip_edit_interval_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        from utils.formatting import persian_to_english_digits

        interval_val = int(persian_to_english_digits(message.text.strip()))
        if interval_val < 1:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر (به دقیقه) وارد کنید.")
        return

    from db.models import set_ip_checker_config

    await set_ip_checker_config(interval_minutes=interval_val)
    await state.clear()

    panel_text, keyboard = await _build_pricing_panel()
    await message.answer(
        f"✅ فاصله زمان جدید پایش سقف IP (<b>هر {to_persian_digits(interval_val)} دقیقه</b>) با موفقیت ذخیره شد.\n\n{panel_text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_bulk_gift_menu")
async def admin_bulk_gift_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "bulk_gift"):
        return
    await state.clear()

    from keyboards.inline_kb import bulk_gift_menu_keyboard

    text = (
        "🎁 <b>پنل هدیه همگانی (افزایش حجم و زمان همه کاربران)</b>\n\n"
        "از این بخش می‌توانید به صورت دسته‌جمعی به تمامی اشتراک‌های فعال در سرور، حجم رایگان یا روزهای اضافی هدیه دهید.\n\n"
        "گزینه مورد نظر جهت تنظیم مقدار هدیه را انتخاب کنید:"
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=bulk_gift_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_bulk_gift_gb_start")
@router.callback_query(F.data.startswith("admin_bulk_gift_gb_step_"))
async def admin_bulk_gift_gb_stepper(callback: types.CallbackQuery) -> None:
    if not await _is_admin(callback):
        return

    if callback.data.startswith("admin_bulk_gift_gb_step_"):
        gb = int(callback.data.split("_")[-1])
    else:
        gb = 1

    from keyboards.inline_kb import bulk_gift_gb_stepper_keyboard

    text = (
        "📊 <b>تنظیم مقدار هدیه حجم همگانی</b>\n\n"
        "میزان حجم هدیه مورد نظر جهت افزودن به کلیه اشتراک‌ها را تعیین کنید:\n\n"
        "💡 <i>با کلیک روی دکمه‌های ➕ و ➖ مقدار هدیه را تنظیم کرده و سپس دکمه اعمال را بزنید.</i>"
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=bulk_gift_gb_stepper_keyboard(gb),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.edit_reply_markup(
            reply_markup=bulk_gift_gb_stepper_keyboard(gb)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_bulk_gift_gb_confirm_"))
async def admin_bulk_gift_gb_confirm(callback: types.CallbackQuery, bot: Bot) -> None:
    if not await _is_admin(callback):
        return
    gb = int(callback.data.split("_")[-1])

    await callback.message.edit_text(
        f"⏳ <b>در حال اعمال هدیه +{format_size_gb(gb)} به تمامی اشتراک‌ها... لطفاً شکیبا باشید.</b>",
        parse_mode="HTML",
    )

    from services.xui_api import bulk_grant_volume

    success_cnt, fail_cnt = await bulk_grant_volume(gb, bot)

    panel_text, keyboard = await _build_pricing_panel()
    res_text = (
        f"✅ <b>عملیات اهدای حجم همگانی با موفقیت انجام شد.</b>\n\n"
        f"📊 <b>مقدار هدیه:</b> +{format_size_gb(gb)}\n"
        f"🟢 <b>تعداد موفق:</b> {to_persian_digits(success_cnt)} اشتراک\n"
        f"🔴 <b>تعداد ناموفق:</b> {to_persian_digits(fail_cnt)} اشتراک\n\n"
        f"{panel_text}"
    )
    await callback.message.edit_text(res_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_bulk_gift_days_start")
@router.callback_query(F.data.startswith("admin_bulk_gift_days_step_"))
async def admin_bulk_gift_days_stepper(callback: types.CallbackQuery) -> None:
    if not _is_admin(callback):
        return

    if callback.data.startswith("admin_bulk_gift_days_step_"):
        days = int(callback.data.split("_")[-1])
    else:
        days = 1

    from keyboards.inline_kb import bulk_gift_days_stepper_keyboard

    text = (
        "⏱ <b>تنظیم مقدار هدیه تمدید زمان همگانی</b>\n\n"
        "تعداد روزهای اضافه مورد نظر جهت تمدید اعتبار کلیه اشتراک‌ها را تعیین کنید:\n\n"
        "💡 <i>با کلیک روی دکمه‌های ➕ و ➖ تعداد روزها را تنظیم کرده و سپس دکمه اعمال را بزنید.</i>"
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=bulk_gift_days_stepper_keyboard(days),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.edit_reply_markup(
            reply_markup=bulk_gift_days_stepper_keyboard(days)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_bulk_gift_days_confirm_"))
async def admin_bulk_gift_days_confirm(callback: types.CallbackQuery, bot: Bot) -> None:
    if not await _is_admin(callback):
        return
    days = int(callback.data.split("_")[-1])

    await callback.message.edit_text(
        f"⏳ <b>در حال تمدید هدیه +{to_persian_digits(days)} روز به تمامی اشتراک‌ها... لطفاً شکیبا باشید.</b>",
        parse_mode="HTML",
    )

    from services.xui_api import bulk_grant_duration

    success_cnt, fail_cnt = await bulk_grant_duration(days, bot)

    panel_text, keyboard = await _build_pricing_panel()
    res_text = (
        f"✅ <b>عملیات تمدید زمان همگانی با موفقیت انجام شد.</b>\n\n"
        f"⏱ <b>مقدار تمدید:</b> +{to_persian_digits(days)} روز\n"
        f"🟢 <b>تعداد موفق:</b> {to_persian_digits(success_cnt)} اشتراک\n"
        f"🔴 <b>تعداد ناموفق:</b> {to_persian_digits(fail_cnt)} اشتراک\n\n"
        f"{panel_text}"
    )
    await callback.message.edit_text(res_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_manage_admins_menu")
async def admin_manage_admins_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_owner(callback):
        return
    await state.clear()

    from db.models import get_all_admins
    from keyboards.inline_kb import admin_manage_admins_keyboard

    admins = await get_all_admins()
    admin_list_str = ""
    if admins:
        for idx, adm in enumerate(admins, start=1):
            username_str = (
                f"(@{adm['username']})" if adm.get("username") else "(بدون آیدی)"
            )
            admin_list_str += f"  {to_persian_digits(idx)}. 🆔 <code>{adm['tg_id']}</code> {username_str}\n"
    else:
        admin_list_str = "  <i>هیچ ادمین جانبی ثبتی وجود ندارد.</i>\n"

    text = (
        f"👥 <b>پنل مدیریت ادمین‌های ربات</b>\n\n"
        f"👑 <b>مالک ارشد ربات:</b> <code>{ADMIN_CHAT_ID}</code>\n\n"
        f"📋 <b>لیست ادمین‌های فعلی:</b>\n{admin_list_str}\n"
        f"💡 <i>ادمین‌ها امکان مدیریت اشتراک‌ها، پاسخ به سفارشات، ارسال پیام همگانی و اعمال هدیه را دارند.</i>"
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=admin_manage_admins_keyboard(admins),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_add_admin_start")
async def admin_add_admin_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_owner(callback):
        return

    await state.set_state(AdminControlStates.waiting_add_admin_id)
    await callback.message.edit_text(
        "➕ <b>افزودن ادمین جدید:</b>\n\n"
        "لطفاً شناسه عددی تلگرام (Telegram User ID) ادمین جدید را ارسال کنید.\n"
        "یا یک پیام از کاربر مورد نظر را به این گفتگو **فوروارد (Forward)** کنید.\n\n"
        "🔸 مثال: <code>123456789</code>\n\n"
        "<i>برای انصراف /cancel را بفرستید.</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_add_admin_id)
async def admin_add_admin_save(message: types.Message, state: FSMContext) -> None:
    if not _is_owner(message):
        return

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات افزودن ادمین لغو شد.")
        return

    new_tg_id = 0
    username = ""

    if message.forward_from:
        new_tg_id = message.forward_from.id
        username = message.forward_from.username or ""
    elif message.text:
        try:
            clean_id = (
                persian_to_english_digits(message.text.strip())
                .replace(" ", "")
                .lstrip("@")
            )
            new_tg_id = int(clean_id)
        except ValueError:
            await message.answer(
                "⚠️ لطفاً یک شناسه عددی معتبر تلگرام یا پیام فورواردی ارسال کنید."
            )
            return

    if new_tg_id <= 0:
        await message.answer("⚠️ شناسه تلگرام وارد شده معتبر نیست.")
        return

    from db.models import add_admin

    success = await add_admin(
        tg_id=new_tg_id, username=username, added_by=message.from_user.id
    )
    await state.clear()

    if success:
        panel_text, keyboard = await _build_pricing_panel()
        await message.answer(
            f"✅ کاربر با شناسه <code>{new_tg_id}</code> با موفقیت به عنوان ادمین ربات ثبت شد.\n\n{panel_text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await message.answer("❌ خطا در ثبت ادمین جدید (ممکن است مالک اصلی باشد).")


@router.callback_query(F.data == "admin_remove_admin_menu")
async def admin_remove_admin_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_owner(callback):
        return
    await state.clear()

    from db.models import get_all_admins
    from keyboards.inline_kb import admin_remove_admins_keyboard

    admins = await get_all_admins()
    if not admins:
        await callback.answer("⚠️ هیچ ادمین جانبی برای عزل وجود ندارد.", show_alert=True)
        return

    text = "🗑 <b>انتخاب ادمین جهت عزل و سلب دسترسی:</b>\n\nبرای عزل ادمین، روی دکمه مربوطه کلیک کنید:"
    await callback.message.edit_text(
        text,
        reply_markup=admin_remove_admins_keyboard(admins),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_remove_admin_confirm_"))
async def admin_remove_admin_confirm(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_owner(callback):
        return

    target_id = int(callback.data.split("_")[-1])

    from db.models import remove_admin

    await remove_admin(target_id)
    await callback.answer("✅ دسترسی ادمین با موفقیت سلب گردید.", show_alert=True)

    await admin_manage_admins_menu(callback, state)


@router.callback_query(F.data.startswith("admin_perm_panel_"))
async def admin_perm_panel(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _require_owner(callback):
        return
    await state.clear()

    target_id = int(callback.data.split("_")[-1])

    from db.models import get_admin_permissions
    from keyboards.inline_kb import admin_permissions_keyboard

    perms = await get_admin_permissions(target_id)

    text = (
        f"⚙️ <b>مدیریت دسترسی‌های ادمین جانبی</b>\n\n"
        f"👤 <b>شناسه ادمین:</b> <code>{target_id}</code>\n\n"
        f"💡 <i>با کلیک روی هر گزینه، دسترسی مربوطه را فعال (🟢) یا غیرفعال (🔴) کنید:</i>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=admin_permissions_keyboard(target_id, perms),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_toggle_perm_"))
async def admin_toggle_perm(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _require_owner(callback):
        return

    parts = callback.data.split("_")
    target_id = int(parts[3])
    perm_key = "_".join(parts[4:])

    from db.models import PERMISSION_TITLES, toggle_admin_permission
    from keyboards.inline_kb import admin_permissions_keyboard

    updated_perms = await toggle_admin_permission(target_id, perm_key)
    new_status = updated_perms.get(perm_key, False)
    status_str = "🟢 فعال" if new_status else "🔴 غیرفعال"
    perm_title = PERMISSION_TITLES.get(perm_key, perm_key)

    await callback.answer(f"دسترسی «{perm_title}» {status_str} شد.")

    try:
        await callback.message.edit_reply_markup(
            reply_markup=admin_permissions_keyboard(target_id, updated_perms)
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_stats_menu")
async def admin_stats_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _require_permission(callback, "stats"):
        return
    await state.clear()

    from db.models import get_bot_statistics
    from keyboards.inline_kb import admin_stats_keyboard

    stats = await get_bot_statistics()

    text = (
        f"📊 <b>آمار و گزارشات جامع ربات</b>\n\n"
        f"👥 <b>آمار کاربران</b>\n"
        f"  • کل کاربران: <b>{to_persian_digits(stats['total_users'])}</b> نفر\n"
        f"  • امروز: <b>{to_persian_digits(stats['users_today'])}</b> نفر\n"
        f"  • ۷ روز اخیر: <b>{to_persian_digits(stats['users_week'])}</b> نفر\n"
        f"  • ۳۰ روز اخیر: <b>{to_persian_digits(stats['users_month'])}</b> نفر\n\n"
        f"💰 <b>تراکنش‌ها و آمار مالی</b>\n"
        f"  • 📋 کل فاکتورها: <b>{to_persian_digits(stats['total_invoices_count'])}</b> فقره ({format_price(stats['total_invoices_amount'])})\n"
        f"  • 🟢 پرداخت‌های موفق: <b>{to_persian_digits(stats['paid_invoices_count'])}</b> فقره ({format_price(stats['paid_revenue'])})\n"
        f"       •• 🛒 فروش/تمدید اشتراک: <b>{to_persian_digits(stats['subs_sales_count'])}</b> فقره ({format_price(stats['subs_sales_revenue'])})\n"
        f"       •• 💳 شارژ کیف پول: <b>{to_persian_digits(stats['topups_count'])}</b> فقره ({format_price(stats['topups_revenue'])})\n"
        f"       •• 💳 کارت به کارت: <b>{to_persian_digits(stats.get('card_count', 0))}</b> فقره ({format_price(stats.get('card_revenue', 0))})\n"
        f"       •• 👛 پرداخت از کیف پول: <b>{to_persian_digits(stats.get('wallet_count', 0))}</b> فقره ({format_price(stats.get('wallet_revenue', 0))})\n"
        f"  • 🟡 پندینگ: <b>{to_persian_digits(stats['pending_invoices_count'])}</b> فقره ({format_price(stats['pending_amount'])})\n"
        f"  • 🔴 ردشده: <b>{to_persian_digits(stats['rejected_invoices_count'])}</b> فقره ({format_price(stats['rejected_amount'])})\n"
        f"  • 💼 موجودی کیف پول کاربران: <b>{format_price(stats['total_wallets_balance'])}</b>\n\n"
        f"⚙️ <b>زیرساخت و سرور</b>\n"
        f"  • اینباندهای فعال اختصاصی: <b>{to_persian_digits(stats['active_inbounds_count'])}</b> عدد\n"
        f"  • اشتراک‌های مسدود (تخطی IP): <b>{to_persian_digits(stats['suspended_count'])}</b> عدد\n"
        f"  • تعداد ادمین‌ها: <b>{to_persian_digits(stats['admins_count'])}</b> نفر\n\n"
        f"💡 <i>اطلاعات فوق به صورت زنده از دیتابیس ربات محاسبه شده‌اند.</i>"
    )

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=admin_stats_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_users_list_noop")
async def admin_users_list_noop(callback: types.CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("admin_users_list_"))
async def admin_users_list(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _require_permission(callback, "users_list"):
        return
    page_str = callback.data[len("admin_users_list_") :]
    try:
        page = int(page_str)
    except ValueError:
        page = 0

    await state.update_data(current_list_page=page, last_search_query=None)

    from db.models import get_users_paginated
    from keyboards.inline_kb import admin_users_list_keyboard

    page_size = 5
    users, total_users, total_pages = await get_users_paginated(
        page, page_size=page_size
    )

    if not users:
        await safe_edit_text(
            callback.message,
            "📭 <b>هیچ کاربری در ربات یافت نشد.</b>",
            reply_markup=admin_users_list_keyboard([], 0, 1),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    lines = [
        f"👥 <b>لیست و مدیریت کاربران ربات</b> (کل: <b>{to_persian_digits(total_users)}</b> کاربر)\n"
    ]

    start_idx = page * page_size + 1
    for idx, u in enumerate(users, start=start_idx):
        tg_id = u["tg_id"]
        username = u.get("username")
        full_name = u.get("full_name") or "بدون نام"
        username_str = f"@{username}" if username else "بدون نام‌کاربری"
        bal_str = format_price(u.get("balance", 0))
        total_paid_str = format_price(u.get("total_paid", 0))
        paid_count = u.get("paid_count", 0)
        joined_at = u.get("joined_at") or "نامشخص"

        lines.append(
            f"{to_persian_digits(idx)}️⃣ <b>{full_name}</b> ({username_str})\n"
            f"   🆔 <code>{tg_id}</code> | 💰 کیف پول: {bal_str}\n"
            f"   💳 کل پرداختی‌ها: <b>{total_paid_str}</b> ({to_persian_digits(paid_count)} موفق)\n"
            f"   📅 ثبت‌نام: <code>{joined_at}</code>\n"
        )

    text = "\n".join(lines)
    keyboard = admin_users_list_keyboard(users, page, total_pages)

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


async def _build_start_msg_panel() -> tuple[str, InlineKeyboardMarkup]:
    from db.models import get_start_message_config
    from services.start_message import MESSAGE_TYPE_TITLES

    config = await get_start_message_config()
    is_enabled = config["enabled"]
    target = config.get("target", "all")
    msg_data = config.get("message_data")

    status_str = "🟢 فعال" if is_enabled else "🔴 غیرفعال"
    target_str = "👥 همه استارت‌ها" if target == "all" else "👤 فقط کاربران جدید"

    if msg_data:
        msg_type = msg_data.get("type", "text")
        type_title = MESSAGE_TYPE_TITLES.get(msg_type, msg_type)
        btns_count = len(msg_data.get("buttons") or [])
        btns_info = (
            f" (دارای {to_persian_digits(btns_count)} دکمه شیشه‌ای)"
            if btns_count
            else ""
        )
        msg_info = f"✅ تنظیم‌شده ({type_title}){btns_info}"
    else:
        msg_info = "❌ تنظیم‌نشده (هیچ پیامی ثبت نشده است)"

    text = (
        "📩 <b>مدیریت پیام پس از دستور /start</b>\n\n"
        "این پیام بلافاصله پس از پیام خوش‌آمدگویی و منوی اصلی ربات برای کاربر ارسال می‌شود.\n\n"
        f"⚙️ <b>وضعیت ارسال:</b> {status_str}\n"
        f"🎯 <b>جامعه هدف:</b> {target_str}\n"
        f"📦 <b>محتوای پیام:</b> {msg_info}\n\n"
        "از دکمه‌های زیر جهت تغییر تنظیمات استفاده کنید:"
    )

    toggle_btn_text = "🔴 غیرفعال کردن" if is_enabled else "🟢 فعال کردن"
    toggle_target_text = (
        "🎯 ارسال به: فقط کاربران جدید"
        if target == "all"
        else "🎯 ارسال به: همه استارت‌ها"
    )

    keyboard_rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=toggle_btn_text, callback_data="admin_start_msg_toggle"
            ),
            InlineKeyboardButton(
                text=toggle_target_text,
                callback_data="admin_start_msg_toggle_target",
            ),
        ],
        [
            InlineKeyboardButton(
                text="✏️ تنظیم پیام جدید", callback_data="admin_start_msg_set"
            ),
        ],
    ]

    if msg_data:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text="👁 پیش‌نمایش پیام فعلی",
                    callback_data="admin_start_msg_preview",
                ),
                InlineKeyboardButton(
                    text="🗑 حذف پیام", callback_data="admin_start_msg_delete"
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

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


@router.callback_query(F.data == "admin_start_msg_menu")
async def admin_start_msg_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "start_message"):
        return
    await state.clear()
    text, keyboard = await _build_start_msg_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_start_msg_toggle")
async def admin_start_msg_toggle(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "start_message"):
        return
    from db.models import get_start_message_config, set_start_message_config

    config = await get_start_message_config()
    new_status = not config["enabled"]
    if new_status and not config.get("message_data"):
        await callback.answer(
            "⚠️ ابتدا باید یک پیام تنظیم کنید، سپس می‌توانید آن را فعال نمایید.",
            show_alert=True,
        )
        return

    await set_start_message_config(enabled=new_status)
    text, keyboard = await _build_start_msg_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    status_toast = "فعال شد 🟢" if new_status else "غیرفعال شد 🔴"
    await callback.answer(f"پیام پس از استارت {status_toast}")


@router.callback_query(F.data == "admin_start_msg_toggle_target")
async def admin_start_msg_toggle_target(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "start_message"):
        return
    from db.models import get_start_message_config, set_start_message_config

    config = await get_start_message_config()
    current_target = config.get("target", "all")
    new_target = "new_only" if current_target == "all" else "all"
    await set_start_message_config(target=new_target)

    text, keyboard = await _build_start_msg_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    target_toast = "فقط کاربران جدید" if new_target == "new_only" else "همه استارت‌ها"
    await callback.answer(f"جامعه هدف به «{target_toast}» تغییر یافت.")


@router.callback_query(F.data == "admin_start_msg_preview")
async def admin_start_msg_preview(callback: types.CallbackQuery, bot: Bot) -> None:
    if not await _require_permission(callback, "start_message"):
        return
    from db.models import get_start_message_config
    from services.start_message import send_start_payload

    config = await get_start_message_config()
    msg_data = config.get("message_data")
    if not msg_data:
        await callback.answer("پیامی تنظیم نشده است.", show_alert=True)
        return

    await callback.answer("در حال ارسال پیش‌نمایش...")
    if callback.from_user:
        await send_start_payload(bot, callback.from_user.id, msg_data)


@router.callback_query(F.data == "admin_start_msg_delete")
async def admin_start_msg_delete(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "start_message"):
        return
    from db.models import delete_start_message

    await delete_start_message()
    await state.clear()
    text, keyboard = await _build_start_msg_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer("🗑 پیام پس از استارت حذف گردید.", show_alert=True)


@router.callback_query(F.data == "admin_start_msg_set")
async def admin_start_msg_set(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _require_permission(callback, "start_message"):
        return
    await state.clear()
    await state.set_state(AdminControlStates.waiting_start_msg_content)

    text = (
        "📩 <b>تنظیم پیام جدید پس از استارت</b>\n\n"
        "لطفاً پیام مدنظر خود را ارسال یا فوروارد کنید.\n\n"
        "• پشتیبانی از: <b>متن ساده/فرمت‌دار، عکس با کپشن، ویدیو، انیمیشن (گیف)، وویس، فایل صوتی، داکیومنت و استیکر</b>.\n"
        "• پس از ارسال، پیش‌نمایش به شما نشان داده می‌شود و می‌توانید دکمه‌های شیشه‌ای دلخواه نیز اضافه کنید.\n\n"
        "<i>برای انصراف /cancel را ارسال کنید.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data="admin_start_msg_menu"
                )
            ]
        ]
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(
    AdminControlStates.waiting_start_msg_content,
    F.content_type.in_(
        {
            types.ContentType.TEXT,
            types.ContentType.PHOTO,
            types.ContentType.VIDEO,
            types.ContentType.ANIMATION,
            types.ContentType.DOCUMENT,
            types.ContentType.AUDIO,
            types.ContentType.VOICE,
            types.ContentType.STICKER,
        }
    ),
)
async def admin_start_msg_content_received(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not await _is_admin(message):
        return
    if not await _require_permission(message, "start_message"):
        return

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        text, keyboard = await _build_start_msg_panel()
        await message.answer(
            f"❌ تنظیم پیام لغو شد.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    from services.start_message import extract_message_data, send_start_payload

    msg_data = extract_message_data(message)
    await state.update_data(draft_start_msg=msg_data)

    await message.answer("👇 <b>پیش‌نمایش پیام ارسالی شما:</b>", parse_mode="HTML")
    await send_start_payload(bot, message.chat.id, msg_data)

    btns = msg_data.get("buttons") or []
    btns_count = len(btns)

    confirm_text = (
        "👆 <b>پیش‌نمایش پیام در بالا ارسال شد.</b>\n\n"
        f"🔘 <b>تعداد ردیف‌های دکمه شیشه‌ای:</b> {to_persian_digits(btns_count)}\n\n"
        "آیا این پیام را برای ذخیره نهایی تأیید می‌کنید یا می‌خواهید دکمه شیشه‌ای اضافه کنید؟"
    )

    confirm_rows = [
        [
            InlineKeyboardButton(
                text="🚀 تایید و ذخیره نهایی",
                callback_data="admin_start_msg_save",
            )
        ],
        [
            InlineKeyboardButton(
                text="➕ افزودن دکمه شیشه‌ای (لینک)",
                callback_data="admin_start_msg_add_btn",
            )
        ],
    ]
    if btns_count > 0:
        confirm_rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 پاک کردن تمام دکمه‌ها",
                    callback_data="admin_start_msg_clear_btns",
                )
            ]
        )
    confirm_rows.append(
        [InlineKeyboardButton(text="❌ انصراف", callback_data="admin_start_msg_menu")]
    )

    await message.answer(
        confirm_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=confirm_rows),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_start_msg_save")
async def admin_start_msg_save(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "start_message"):
        return

    data = await state.get_data()
    draft = data.get("draft_start_msg")
    if not draft:
        await callback.answer("❌ اطلاعات پیام یافت نشد.", show_alert=True)
        await state.clear()
        return

    from db.models import set_start_message_config

    await set_start_message_config(enabled=True, message_data=draft)
    await state.clear()

    text, keyboard = await _build_start_msg_panel()
    if callback.message:
        await callback.message.answer(
            f"✅ <b>پیام پس از استارت با موفقیت ذخیره و فعال شد!</b>\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "admin_start_msg_add_btn")
async def admin_start_msg_add_btn(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "start_message"):
        return
    data = await state.get_data()
    if not data.get("draft_start_msg"):
        await callback.answer("پیام معتبری در حافظه نیست.", show_alert=True)
        return

    await state.set_state(AdminControlStates.waiting_start_msg_button_title)
    if callback.message:
        await callback.message.answer(
            "🔘 <b>افزودن دکمه شیشه‌ای جدید</b>\n\n"
            "لطفاً <b>عنوان (متن روی دکمه)</b> را ارسال کنید:\n"
            "مثال: <code>کانال اطلاع‌رسانی</code>\n\n"
            "<i>برای انصراف /cancel را ارسال کنید.</i>",
            parse_mode="HTML",
        )
    await callback.answer()


@router.message(AdminControlStates.waiting_start_msg_button_title, F.text)
async def admin_start_msg_btn_title_received(
    message: types.Message, state: FSMContext
) -> None:
    if not await _require_permission(message, "start_message"):
        return
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        text, keyboard = await _build_start_msg_panel()
        await message.answer(
            f"❌ عملیات لغو شد.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    title = message.text.strip()
    await state.update_data(btn_title=title)
    await state.set_state(AdminControlStates.waiting_start_msg_button_url)

    await message.answer(
        f"🔗 عنوان دکمه: <b>{title}</b>\n\n"
        "حالا لطفاً <b>لینک دکمه (URL)</b> را ارسال کنید:\n"
        "مثال: <code>https://t.me/your_channel</code>\n\n"
        "<i>برای انصراف /cancel را ارسال کنید.</i>",
        parse_mode="HTML",
    )


@router.message(AdminControlStates.waiting_start_msg_button_url, F.text)
async def admin_start_msg_btn_url_received(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not await _require_permission(message, "start_message"):
        return
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        text, keyboard = await _build_start_msg_panel()
        await message.answer(
            f"❌ عملیات لغو شد.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    url = message.text.strip()
    if not (
        url.startswith("http://")
        or url.startswith("https://")
        or url.startswith("t.me/")
        or url.startswith("tg://")
    ):
        await message.answer(
            "❌ لینک نامعتبر است. لینک باید با <code>https://</code> یا <code>t.me/</code> شروع شود.\n"
            "لطفاً مجدداً لینک صحیح را بفرستید:",
            parse_mode="HTML",
        )
        return

    if url.startswith("t.me/"):
        url = f"https://{url}"

    data = await state.get_data()
    title = data.get("btn_title", "دکمه")
    draft = data.get("draft_start_msg", {})

    buttons = draft.get("buttons") or []
    buttons.append([{"text": title, "url": url}])
    draft["buttons"] = buttons

    await state.update_data(draft_start_msg=draft)
    await state.set_state(AdminControlStates.waiting_start_msg_content)

    from services.start_message import send_start_payload

    await message.answer(
        "✅ <b>دکمه شیشه‌ای اضافه شد! پیش‌نمایش جدید:</b>", parse_mode="HTML"
    )
    await send_start_payload(bot, message.chat.id, draft)

    btns_count = len(buttons)
    confirm_text = (
        "👆 <b>پیش‌نمایش بروزرسانی‌شده در بالا ارسال شد.</b>\n\n"
        f"🔘 <b>تعداد ردیف‌های دکمه شیشه‌ای:</b> {to_persian_digits(btns_count)}\n\n"
        "آیا تغییرات را تایید و ذخیره می‌کنید؟"
    )
    confirm_rows = [
        [
            InlineKeyboardButton(
                text="🚀 تایید و ذخیره نهایی",
                callback_data="admin_start_msg_save",
            )
        ],
        [
            InlineKeyboardButton(
                text="➕ افزودن دکمه شیشه‌ای دیگر",
                callback_data="admin_start_msg_add_btn",
            )
        ],
        [
            InlineKeyboardButton(
                text="🗑 پاک کردن تمام دکمه‌ها",
                callback_data="admin_start_msg_clear_btns",
            )
        ],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="admin_start_msg_menu")],
    ]
    await message.answer(
        confirm_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=confirm_rows),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_start_msg_clear_btns")
async def admin_start_msg_clear_btns(
    callback: types.CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not await _require_permission(callback, "start_message"):
        return
    data = await state.get_data()
    draft = data.get("draft_start_msg")
    if not draft:
        await callback.answer("پیام نامعتبر است.", show_alert=True)
        return

    draft["buttons"] = []
    await state.update_data(draft_start_msg=draft)

    from services.start_message import send_start_payload

    if callback.message and callback.from_user:
        await callback.message.answer(
            "🗑 <b>تمام دکمه‌ها پاک شدند. پیش‌نمایش جدید:</b>",
            parse_mode="HTML",
        )
        await send_start_payload(bot, callback.from_user.id, draft)

        confirm_text = (
            "👆 <b>پیش‌نمایش بدون دکمه در بالا ارسال شد.</b>\n\n"
            "آیا این پیام را ذخیره می‌کنید؟"
        )
        confirm_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚀 تایید و ذخیره نهایی",
                        callback_data="admin_start_msg_save",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="➕ افزودن دکمه شیشه‌ای",
                        callback_data="admin_start_msg_add_btn",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ انصراف", callback_data="admin_start_msg_menu"
                    )
                ],
            ]
        )
        await callback.message.answer(
            confirm_text, reply_markup=confirm_keyboard, parse_mode="HTML"
        )
    await callback.answer()


async def _build_pricing_display_panel() -> tuple[str, InlineKeyboardMarkup]:
    from db.models import get_pricing_display_config

    config = await get_pricing_display_config()
    is_enabled = config["enabled"]
    hide_button = config.get("hide_button", False)
    mode = config.get("mode", "default")

    status_str = "🟢 فعال" if is_enabled else "🔴 غیرفعال"
    btn_status_str = "🙈 پنهان (مخفی)" if hide_button else "👁 نمایان (در منوی اصلی)"

    mode_titles = {
        "default": "⚙️ متن خودکار سیستم (بدون تصویر)",
        "photo_with_default_text": "🖼️ تصویر + متن خودکار سیستم",
        "photo_with_custom_text": "🖼️ تصویر + متن سفارشی ادمین",
        "custom_text_only": "📝 فقط متن سفارشی ادمین (بدون تصویر)",
    }
    mode_str = mode_titles.get(mode, "⚙️ متن خودکار سیستم")

    text = (
        "🖼️ <b>تنظیمات نمایش بخش تعرفه‌ها</b>\n\n"
        "در این بخش می‌توانید فعال/غیرفعال بودن، پنهان یا نمایان بودن دکمه در منوی اصلی و تصویر یا متن دلخواه تعرفه را تنظیم کنید.\n\n"
        f"⚙️ <b>وضعیت بخش تعرفه‌ها:</b> {status_str}\n"
        f"🔘 <b>دکمه در منوی اصلی:</b> {btn_status_str}\n"
        f"📋 <b>حالت فعلی محتوا:</b> {mode_str}\n\n"
        "جهت تغییر، گزینه‌ی مورد نظر را انتخاب نمایید:"
    )

    toggle_btn_text = (
        "🔴 غیرفعال کردن بخش تعرفه" if is_enabled else "🟢 فعال کردن بخش تعرفه"
    )
    toggle_hide_btn_text = (
        "👁 نمایش دکمه در منو" if hide_button else "🙈 پنهان کردن دکمه در منو"
    )

    keyboard_rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=toggle_btn_text, callback_data="admin_pricing_disp_toggle"
            ),
            InlineKeyboardButton(
                text=toggle_hide_btn_text,
                callback_data="admin_pricing_disp_toggle_btn",
            ),
        ],
        [
            InlineKeyboardButton(
                text="✏️ تنظیم متن یا تصویر جدید",
                callback_data="admin_pricing_disp_set",
            ),
        ],
        [
            InlineKeyboardButton(
                text="👁 پیش‌نمایش نحوه نمایش به کاربر",
                callback_data="admin_pricing_disp_preview",
            ),
        ],
    ]

    if mode != "default":
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 بازنشانی به متن خودکار پیش‌فرض",
                    callback_data="admin_pricing_disp_reset",
                ),
            ]
        )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
            ),
        ]
    )

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


@router.callback_query(F.data == "admin_pricing_disp_menu")
async def admin_pricing_disp_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "pricing"):
        return
    await state.clear()
    text, keyboard = await _build_pricing_display_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_pricing_disp_toggle")
async def admin_pricing_disp_toggle(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "pricing"):
        return
    from db.models import (
        get_pricing_display_config,
        set_pricing_display_config,
    )

    config = await get_pricing_display_config()
    new_status = not config["enabled"]
    await set_pricing_display_config(enabled=new_status)

    text, keyboard = await _build_pricing_display_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    status_toast = "فعال شد 🟢" if new_status else "غیرفعال شد 🔴"
    await callback.answer(f"بخش تعرفه‌ها {status_toast}")


@router.callback_query(F.data == "admin_pricing_disp_toggle_btn")
async def admin_pricing_disp_toggle_btn(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "pricing"):
        return
    from db.models import (
        get_pricing_display_config,
        set_pricing_display_config,
    )

    config = await get_pricing_display_config()
    new_hide = not config.get("hide_button", False)
    await set_pricing_display_config(hide_button=new_hide)

    text, keyboard = await _build_pricing_display_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    toast = (
        "دکمه تعرفه از منوی اصلی پنهان شد 🙈"
        if new_hide
        else "دکمه تعرفه در منوی اصلی نمایش داده می‌شود 👁"
    )
    await callback.answer(toast)


@router.callback_query(F.data == "admin_pricing_disp_reset")
async def admin_pricing_disp_reset(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "pricing"):
        return
    from db.models import reset_pricing_display_config

    await reset_pricing_display_config()
    await state.clear()
    text, keyboard = await _build_pricing_display_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer(
        "✅ بخش تعرفه به حالت متن خودکار سیستم بازنشانی شد.", show_alert=True
    )


@router.callback_query(F.data == "admin_pricing_disp_preview")
async def admin_pricing_disp_preview(callback: types.CallbackQuery, bot: Bot) -> None:
    if not await _require_permission(callback, "pricing"):
        return
    from db.models import get_pricing_display_config
    from handlers.pricing import build_pricing_text

    config = await get_pricing_display_config()
    mode = config.get("mode", "default")
    photo_file_id = config.get("photo_file_id")
    custom_text = config.get("custom_text")

    await callback.answer("در حال ارسال پیش‌نمایش...")
    if not callback.from_user:
        return
    chat_id = callback.from_user.id

    if mode == "photo_with_default_text" and photo_file_id:
        text = await build_pricing_text()
        await bot.send_photo(
            chat_id=chat_id,
            photo=photo_file_id,
            caption=text,
            parse_mode="HTML",
        )
    elif mode == "photo_with_custom_text" and photo_file_id:
        caption = custom_text or await build_pricing_text()
        await bot.send_photo(
            chat_id=chat_id,
            photo=photo_file_id,
            caption=caption,
            parse_mode="HTML",
        )
    elif mode == "custom_text_only" and custom_text:
        await bot.send_message(chat_id=chat_id, text=custom_text, parse_mode="HTML")
    else:
        text = await build_pricing_text()
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


@router.callback_query(F.data == "admin_pricing_disp_set")
async def admin_pricing_disp_set(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "pricing"):
        return
    await state.clear()
    await state.set_state(AdminControlStates.waiting_pricing_content)

    text = (
        "🖼️ <b>تنظیم متن یا تصویر بخش تعرفه‌ها</b>\n\n"
        "لطفاً یکی از موارد زیر را ارسال کنید:\n\n"
        "1️⃣ <b>ارسال تصویر:</b>\n"
        "• اگر تصویر را <u>بدون متن (کپشن)</u> بفرستید، می‌توانید انتخاب کنید که متن خودکار سیستم به عنوان کپشن زیر آن قرار گیرد یا کپشن دلخواه بنویسید.\n"
        "• اگر تصویر را <u>همراه با کپشن</u> بفرستید، کپشن شما به عنوان توضیحات تعرفه قرار خواهد گرفت.\n\n"
        "2️⃣ <b>ارسال متن دلخواه:</b>\n"
        "• می‌توانید متن دلخواه خود را (با فرمت‌های HTML) بفرستید تا تعرفه به صورت متنی نمایش داده شود.\n\n"
        "<i>جهت انصراف /cancel را ارسال کنید.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data="admin_pricing_disp_menu"
                )
            ]
        ]
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(
    AdminControlStates.waiting_pricing_content,
    F.content_type.in_({types.ContentType.TEXT, types.ContentType.PHOTO}),
)
async def admin_pricing_content_received(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not await _is_admin(message):
        return
    if not await _require_permission(message, "pricing"):
        return

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        text, keyboard = await _build_pricing_display_panel()
        await message.answer(
            f"❌ عملیات لغو شد.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    from handlers.pricing import build_pricing_text

    if message.photo:
        photo_id = message.photo[-1].file_id
        if message.caption:
            custom_caption = message.html_text
            await state.update_data(
                pricing_photo_id=photo_id,
                pricing_custom_caption=custom_caption,
            )

            await message.answer(
                "👇 <b>پیش‌نمایش تصویر و کپشن ارسالی شما:</b>",
                parse_mode="HTML",
            )
            await bot.send_photo(
                chat_id=message.chat.id,
                photo=photo_id,
                caption=custom_caption,
                parse_mode="HTML",
            )

            confirm_text = (
                "👆 <b>پیش‌نمایش تصویر به همراه کپشن سفارشی شما در بالا ارسال شد.</b>\n\n"
                "آیا این پیام را به عنوان تعرفه ذخیره می‌کنید، یا مایلید متن خودکار سیستم به عنوان کپشن آن قرار گیرد؟"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🚀 تایید (تصویر + همین کپشن سفارشی)",
                            callback_data="admin_pricing_disp_save_custom",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔄 تغییر به: تصویر + متن خودکار سیستم",
                            callback_data="admin_pricing_disp_save_auto",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ انصراف",
                            callback_data="admin_pricing_disp_menu",
                        )
                    ],
                ]
            )
            await message.answer(confirm_text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await state.update_data(pricing_photo_id=photo_id)
            auto_text = await build_pricing_text()

            await message.answer(
                "👇 <b>پیش‌نمایش تصویر به همراه متن خودکار سیستم:</b>",
                parse_mode="HTML",
            )
            await bot.send_photo(
                chat_id=message.chat.id,
                photo=photo_id,
                caption=auto_text,
                parse_mode="HTML",
            )

            confirm_text = (
                "👆 <b>پیش‌نمایش تصویر به همراه متن خودکار سیستم در بالا ارسال شد.</b>\n\n"
                "تصویر شما بدون کپشن ارسال شد. مایلید همین تصویر به همراه متن خودکار سیستم ذخیره شود، یا می‌خواهید یک متن سفارشی برای آن بنویسید؟"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🚀 تایید (تصویر + متن خودکار سیستم)",
                            callback_data="admin_pricing_disp_save_auto",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="✍️ نوشتن متن/کپشن سفارشی برای عکس",
                            callback_data="admin_pricing_disp_write_caption",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ انصراف",
                            callback_data="admin_pricing_disp_menu",
                        )
                    ],
                ]
            )
            await message.answer(confirm_text, reply_markup=keyboard, parse_mode="HTML")
    elif message.text:
        custom_text = message.html_text
        await state.update_data(pricing_custom_text=custom_text)

        await message.answer(
            "👇 <b>پیش‌نمایش متن سفارشی شما:</b>",
            parse_mode="HTML",
        )
        await message.answer(custom_text, parse_mode="HTML")

        confirm_text = (
            "👆 <b>پیش‌نمایش متن در بالا ارسال شد.</b>\n\n"
            "آیا این متن را به عنوان تعرفه ذخیره می‌کنید؟"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚀 تایید و ذخیره متن سفارشی",
                        callback_data="admin_pricing_disp_save_text",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ انصراف",
                        callback_data="admin_pricing_disp_menu",
                    )
                ],
            ]
        )
        await message.answer(confirm_text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "admin_pricing_disp_write_caption")
async def admin_pricing_disp_write_caption(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "pricing"):
        return
    data = await state.get_data()
    if not data.get("pricing_photo_id"):
        await callback.answer("عکسی در حافظه نیست.", show_alert=True)
        return

    await state.set_state(AdminControlStates.waiting_pricing_caption)
    if callback.message:
        await callback.message.answer(
            "✍️ لطفاً <b>متن (کپشن) دلخواه</b> برای تصویر را ارسال کنید:\n\n"
            "<i>جهت انصراف /cancel را ارسال کنید.</i>",
            parse_mode="HTML",
        )
    await callback.answer()


@router.message(AdminControlStates.waiting_pricing_caption, F.text)
async def admin_pricing_caption_received(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not await _require_permission(message, "pricing"):
        return
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        text, keyboard = await _build_pricing_display_panel()
        await message.answer(
            f"❌ عملیات لغو شد.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    photo_id = data.get("pricing_photo_id")
    if not photo_id:
        await state.clear()
        await message.answer("❌ تصویر یافت نشد.")
        return

    caption = message.html_text
    await state.update_data(pricing_custom_caption=caption)

    await message.answer(
        "👇 <b>پیش‌نمایش تصویر به همراه کپشن جدید:</b>",
        parse_mode="HTML",
    )
    await bot.send_photo(
        chat_id=message.chat.id,
        photo=photo_id,
        caption=caption,
        parse_mode="HTML",
    )

    confirm_text = (
        "👆 <b>پیش‌نمایش جدید در بالا ارسال شد.</b>\n\n"
        "آیا این تنظیمات را تایید و ذخیره می‌کنید؟"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 تایید و ذخیره نهایی",
                    callback_data="admin_pricing_disp_save_custom",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="admin_pricing_disp_menu",
                )
            ],
        ]
    )
    await message.answer(confirm_text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "admin_pricing_disp_save_auto")
async def admin_pricing_disp_save_auto(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "pricing"):
        return
    data = await state.get_data()
    photo_id = data.get("pricing_photo_id")
    if not photo_id:
        await callback.answer("تصویری یافت نشد.", show_alert=True)
        return

    from db.models import set_pricing_display_config

    await set_pricing_display_config(
        enabled=True,
        mode="photo_with_default_text",
        photo_file_id=photo_id,
        custom_text="",
    )
    await state.clear()

    text, keyboard = await _build_pricing_display_panel()
    if callback.message:
        await callback.message.answer(
            f"✅ <b>بخش تعرفه‌ها با موفقیت تنظیم شد (تصویر + متن خودکار سیستم)!</b>\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "admin_pricing_disp_save_custom")
async def admin_pricing_disp_save_custom(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "pricing"):
        return
    data = await state.get_data()
    photo_id = data.get("pricing_photo_id")
    caption = data.get("pricing_custom_caption")
    if not photo_id:
        await callback.answer("تصویری یافت نشد.", show_alert=True)
        return

    from db.models import set_pricing_display_config

    await set_pricing_display_config(
        enabled=True,
        mode="photo_with_custom_text",
        photo_file_id=photo_id,
        custom_text=caption or "",
    )
    await state.clear()

    text, keyboard = await _build_pricing_display_panel()
    if callback.message:
        await callback.message.answer(
            f"✅ <b>بخش تعرفه‌ها با موفقیت تنظیم شد (تصویر + متن سفارشی)!</b>\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "admin_pricing_disp_save_text")
async def admin_pricing_disp_save_text(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "pricing"):
        return
    data = await state.get_data()
    custom_text = data.get("pricing_custom_text")
    if not custom_text:
        await callback.answer("متنی یافت نشد.", show_alert=True)
        return

    from db.models import set_pricing_display_config

    await set_pricing_display_config(
        enabled=True,
        mode="custom_text_only",
        custom_text=custom_text,
        photo_file_id="",
    )
    await state.clear()

    text, keyboard = await _build_pricing_display_panel()
    if callback.message:
        await callback.message.answer(
            f"✅ <b>بخش تعرفه‌ها با موفقیت تنظیم شد (فقط متن سفارشی)!</b>\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await callback.answer()


async def _build_channel_lock_panel() -> tuple[str, InlineKeyboardMarkup]:
    from db.models import get_channel_lock_config

    config = await get_channel_lock_config()
    is_raw_enabled = config["raw_enabled"]
    is_effective_enabled = config["enabled"]
    mode = config["mode"]
    channel_id = config["channel_id"] or "تنظیم نشده ⚠️"
    channel_link = config["channel_link"] or "تنظیم نشده ⚠️"

    status_str = "🟢 فعال" if is_effective_enabled else "🔴 غیرفعال"
    if is_raw_enabled and not config["channel_id"]:
        status_str = "⚠️ فعال ولی بدون آیدی کانال (غیرفعال)"

    mode_str = (
        "🛑 در لحظه استارت (قبل از ورود)"
        if mode == "on_start"
        else "⚡ پس از استارت (هنگام استفاده از امکانات)"
    )

    text = (
        "📢 <b>تنظیمات عضویت اجباری در کانال (Channel Lock)</b>\n\n"
        "در این بخش می‌توانید عضویت اجباری در کانال تلگرام را فعال/غیرفعال کرده، زمان بررسی عضویت را مشخص کنید و آیدی یا لینک کانال را تغییر دهید.\n\n"
        f"⚙️ <b>وضعیت عضویت اجباری:</b> {status_str}\n"
        f"🎯 <b>نحوه و زمان بررسی:</b> {mode_str}\n"
        f"📢 <b>آیدی/یوزرنیم کانال:</b> <code>{channel_id}</code>\n"
        f"🔗 <b>لینک عضویت کانال:</b> <code>{channel_link}</code>\n\n"
        "جهت تغییر، از دکمه‌های زیر استفاده نمایید:"
    )

    toggle_btn_text = (
        "🔴 غیرفعال کردن عضویت اجباری"
        if is_raw_enabled
        else "🟢 فعال کردن عضویت اجباری"
    )
    mode_btn_text = (
        "🔄 تغییر حالت به: پس از استارت ⚡"
        if mode == "on_start"
        else "🔄 تغییر حالت به: در لحظه استارت 🛑"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_btn_text,
                    callback_data="admin_channel_lock_toggle",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=mode_btn_text,
                    callback_data="admin_channel_lock_toggle_mode",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ تغییر آیدی کانال",
                    callback_data="admin_channel_lock_set_id",
                ),
                InlineKeyboardButton(
                    text="✏️ تغییر لینک کانال",
                    callback_data="admin_channel_lock_set_link",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👁 پیش‌نمایش پیام عضویت اجباری",
                    callback_data="admin_channel_lock_preview",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 بازنشانی به پیش‌فرض .env",
                    callback_data="admin_channel_lock_reset",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل اصلی",
                    callback_data="admin_price_main",
                ),
            ],
        ]
    )
    return text, keyboard


@router.callback_query(F.data == "admin_channel_lock_menu")
async def admin_channel_lock_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_lock"):
        return
    await state.clear()
    text, keyboard = await _build_channel_lock_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_channel_lock_toggle")
async def admin_channel_lock_toggle(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_lock"):
        return
    from db.models import get_channel_lock_config, set_channel_lock_config

    config = await get_channel_lock_config()
    new_status = not config["raw_enabled"]
    await set_channel_lock_config(enabled=new_status)

    text, keyboard = await _build_channel_lock_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    status_toast = "فعال شد 🟢" if new_status else "غیرفعال شد 🔴"
    await callback.answer(f"عضویت اجباری {status_toast}")


@router.callback_query(F.data == "admin_channel_lock_toggle_mode")
async def admin_channel_lock_toggle_mode(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_lock"):
        return
    from db.models import get_channel_lock_config, set_channel_lock_config

    config = await get_channel_lock_config()
    curr_mode = config["mode"]
    new_mode = "on_action" if curr_mode == "on_start" else "on_start"
    await set_channel_lock_config(mode=new_mode)

    text, keyboard = await _build_channel_lock_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    mode_toast = (
        "حالت: پس از استارت ⚡"
        if new_mode == "on_action"
        else "حالت: در لحظه استارت 🛑"
    )
    await callback.answer(mode_toast)


@router.callback_query(F.data == "admin_channel_lock_set_id")
async def admin_channel_lock_set_id(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_lock"):
        return
    await state.set_state(AdminControlStates.waiting_channel_lock_id)
    text = (
        "📢 <b>تنظیم آیدی/یوزرنیم کانال</b>\n\n"
        "لطفاً آیدی عددی کانال (مثال: <code>-1001234567890</code>) یا یوزرنیم کانال (مثال: <code>@MyChannel</code>) را ارسال کنید:\n\n"
        "<i>جهت انصراف /cancel را ارسال نمایید.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data="admin_channel_lock_menu"
                )
            ]
        ]
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_channel_lock_id, F.text)
async def admin_channel_lock_id_received(
    message: types.Message, state: FSMContext
) -> None:
    if not await _is_admin(message):
        return
    if not await _require_permission(message, "channel_lock"):
        return
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        text, keyboard = await _build_channel_lock_panel()
        await message.answer(
            f"❌ عملیات لغو شد.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    new_id = message.text.strip()
    from db.models import set_channel_lock_config

    await set_channel_lock_config(channel_id=new_id)
    await state.clear()

    text, keyboard = await _build_channel_lock_panel()
    await message.answer(
        f"✅ <b>آیدی کانال با موفقیت تنظیم شد:</b> <code>{new_id}</code>\n\n{text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_channel_lock_set_link")
async def admin_channel_lock_set_link(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_lock"):
        return
    await state.set_state(AdminControlStates.waiting_channel_lock_link)
    text = (
        "🔗 <b>تنظیم لینک عضویت در کانال</b>\n\n"
        "لطفاً لینک عضویت عمومی یا لینک خصوصی (Invite Link) کانال را ارسال کنید (مثال: <code>https://t.me/MyChannel</code> یا <code>https://t.me/+AbCdEf...</code>):\n\n"
        "<i>جهت انصراف /cancel را ارسال نمایید.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data="admin_channel_lock_menu"
                )
            ]
        ]
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_channel_lock_link, F.text)
async def admin_channel_lock_link_received(
    message: types.Message, state: FSMContext
) -> None:
    if not await _is_admin(message):
        return
    if not await _require_permission(message, "channel_lock"):
        return
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        text, keyboard = await _build_channel_lock_panel()
        await message.answer(
            f"❌ عملیات لغو شد.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    new_link = message.text.strip()
    from db.models import set_channel_lock_config

    await set_channel_lock_config(channel_link=new_link)
    await state.clear()

    text, keyboard = await _build_channel_lock_panel()
    await message.answer(
        f"✅ <b>لینک کانال با موفقیت تنظیم شد:</b> <code>{new_link}</code>\n\n{text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_channel_lock_preview")
async def admin_channel_lock_preview(callback: types.CallbackQuery, bot: Bot) -> None:
    if not await _require_permission(callback, "channel_lock"):
        return
    from db.models import get_channel_lock_config
    from middlewares.channel_check import _JOIN_TEXT, _join_keyboard

    config = await get_channel_lock_config()
    link = config["channel_link"] or "https://t.me"

    await callback.answer("در حال ارسال پیش‌نمایش...")
    if callback.from_user:
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f"👇 <b>پیش‌نمایش پیام عضویت اجباری برای کاربران:</b>\n\n{_JOIN_TEXT}",
            reply_markup=_join_keyboard(link),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "admin_channel_lock_reset")
async def admin_channel_lock_reset(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_lock"):
        return
    from db.models import reset_channel_lock_config

    await reset_channel_lock_config()
    await state.clear()
    text, keyboard = await _build_channel_lock_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer(
        "✅ تنظیمات عضویت اجباری به مقادیر پیش‌فرض .env بازنشانی شد.",
        show_alert=True,
    )
