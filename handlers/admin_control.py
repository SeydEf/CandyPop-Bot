from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

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
    format_datetime,
    format_price,
    format_size,
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
    waiting_invoice_search = State()
    waiting_receipt_disabled_text = State()
    waiting_disc_fixed_amount = State()
    waiting_disc_min_gb = State()
    waiting_disc_max_gb = State()
    waiting_disc_min_amount = State()
    waiting_disc_max_cap = State()
    waiting_disc_target_users = State()
    waiting_disc_expiry_custom = State()
    waiting_disc_user_limit = State()

    waiting_inbound_monitor_interval = State()
    waiting_inbound_monitor_timeout = State()
    waiting_inbound_monitor_target_host = State()


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
        get_ip_checker_config,
        get_pricing_display_config,
        get_referral_config,
        get_schedulers_last_run,
        get_shop_status,
        get_start_first_use_config,
        get_start_message_config,
    )

    ref_config = await get_referral_config()
    card_config = await get_card_config()
    alert_config = await get_alert_config()
    ip_config = await get_ip_checker_config()
    schedulers_last_run = await get_schedulers_last_run()
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

    alert_sch_enabled = bool(alert_config.get("enabled", True))
    ip_checker_enabled = bool(ip_config.get("enabled", True))

    alert_sch_status = "🟢 فعال" if alert_sch_enabled else "🔴 غیرفعال"
    ip_sch_status = "🟢 فعال" if ip_checker_enabled else "🔴 غیرفعال"

    last_alert_raw = schedulers_last_run.get("alert_scheduler")
    last_ip_raw = schedulers_last_run.get("ip_checker")

    last_alert_time = (
        format_datetime(last_alert_raw) if last_alert_raw else "هنوز اجرا نشده"
    )
    last_ip_time = format_datetime(last_ip_raw) if last_ip_raw else "هنوز اجرا نشده"

    schedulers_text = (
        f"  • 🔔 هشدارهای حجم و انقضا: {alert_sch_status} | آخرین پایش: <code>{last_alert_time}</code>\n"
        f"  • 🛡 پایش سقف IP: {ip_sch_status} | آخرین پایش: <code>{last_ip_time}</code>\n"
    )

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

    volume_tiers_enabled = config.get("volume_tiers_enabled", True)
    if volume_tiers_enabled:
        tiers_summary = f"📊 <b>پله‌های تخفیف حجم:</b> 🟢 فعال\n{tiers_text}"
    else:
        tiers_summary = f"📊 <b>پله‌های تخفیف حجم:</b> 🔴 غیرفعال (محاسبه بر اساس نرخ پایه: {format_price(base_rate)} / GB)\n"

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
        f"🤖 <b>وضعیت و آخرین اجرای زمان‌بندها:</b>\n{schedulers_text}\n"
        f"🔔 <b>حدآستانه هشدارهای اتمام سرویس:</b>\n{alert_text}\n"
        f"⏱ <b>حق‌الزحمه مدت زمان:</b>\n{dur_text}\n"
        f"{tiers_summary}\n"
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
                    text="📊 آمار و گزارشات جامع",
                    callback_data="admin_stats_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 مدیریت کاربران",
                    callback_data="admin_sub_users_menu",
                ),
                InlineKeyboardButton(
                    text="💰 قیمت و مالی",
                    callback_data="admin_pricing_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔔 هشدارها و زمان‌بندها",
                    callback_data="admin_alerts_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ تنظیمات",
                    callback_data="admin_settings_menu",
                ),
                InlineKeyboardButton(
                    text="❌ بستن پنل",
                    callback_data="admin_price_close",
                ),
            ],
        ]
    )
    return text, keyboard


def _build_users_submenu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
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
                    text="🧾 مدیریت و لیست فاکتورها",
                    callback_data="admin_invoices_all_0",
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
                    text="👑 مدیریت مدیران ربات (ادمین‌ها)",
                    callback_data="admin_manage_admins_menu",
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


async def _build_pricing_submenu() -> InlineKeyboardMarkup:
    from db.models import get_card_config
    from services.pricing import get_pricing_config

    card_config = await get_card_config()
    card_status = "🟢" if card_config.get("enabled", True) else "🔴"

    pricing_config = await get_pricing_config()
    tiers_status = "🟢" if pricing_config.get("volume_tiers_enabled", True) else "🔴"

    return InlineKeyboardMarkup(
        inline_keyboard=[
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
                    text=f"📊 پله‌های تخفیف حجم ({tiers_status})",
                    callback_data="admin_price_tiers_menu",
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
                    text="🏷️ مدیریت کدهای تخفیف",
                    callback_data="admin_discounts_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"💳 تنظیمات شماره کارت ({card_status})",
                    callback_data="admin_card_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🧾 تنظیمات دریافت رسید واریز (عکس/متن)",
                    callback_data="admin_receipt_config_menu",
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
                    text="🔙 بازگشت به پنل اصلی",
                    callback_data="admin_price_main",
                ),
            ],
        ]
    )


def _build_alerts_submenu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔔 تنظیمات هشدارهای اتمام حجم و زمان",
                    callback_data="admin_alert_menu",
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
                    text="🔙 بازگشت به پنل اصلی",
                    callback_data="admin_price_main",
                ),
            ],
        ]
    )


def _build_settings_submenu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎁 تنظیمات اشتراک تست",
                    callback_data="admin_test_menu",
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
                    text="🩺 پایش سلامت اینباندها (Health Check)",
                    callback_data="admin_inbound_monitor_menu:settings",
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
                    text="🔙 بازگشت به پنل اصلی",
                    callback_data="admin_price_main",
                ),
            ],
        ]
    )


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


@router.callback_query(F.data == "admin_sub_users_menu")
async def admin_sub_users_menu_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await state.clear()
    await safe_edit_text(
        callback.message,
        "👥 <b>مدیریت کاربران</b>\n\nیک بخش را انتخاب کنید:",
        reply_markup=_build_users_submenu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_pricing_menu")
async def admin_pricing_menu_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await state.clear()
    await safe_edit_text(
        callback.message,
        "💰 <b>قیمت و مالی</b>\n\nیک بخش را انتخاب کنید:",
        reply_markup=await _build_pricing_submenu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_alerts_menu")
async def admin_alerts_menu_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await state.clear()
    await safe_edit_text(
        callback.message,
        "🔔 <b>هشدارها و زمان‌بندها</b>\n\nیک بخش را انتخاب کنید:",
        reply_markup=_build_alerts_submenu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_settings_menu")
async def admin_settings_menu_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await state.clear()
    await safe_edit_text(
        callback.message,
        "⚙️ <b>تنظیمات</b>\n\nیک بخش را انتخاب کنید:",
        reply_markup=_build_settings_submenu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_cancel_to_pricing")
async def admin_cancel_to_pricing_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.clear()
    await safe_edit_text(
        callback.message,
        "💰 <b>قیمت و مالی</b>\n\nیک بخش را انتخاب کنید:",
        reply_markup=await _build_pricing_submenu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_cancel_to_users")
async def admin_cancel_to_users_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.clear()
    await safe_edit_text(
        callback.message,
        "👥 <b>مدیریت کاربران</b>\n\nیک بخش را انتخاب کنید:",
        reply_markup=_build_users_submenu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_cancel_to_alerts")
async def admin_cancel_to_alerts_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.clear()
    await safe_edit_text(
        callback.message,
        "🔔 <b>هشدارها و زمان‌بندها</b>\n\nیک بخش را انتخاب کنید:",
        reply_markup=_build_alerts_submenu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_cancel_to_settings")
async def admin_cancel_to_settings_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.clear()
    await safe_edit_text(
        callback.message,
        "⚙️ <b>تنظیمات</b>\n\nیک بخش را انتخاب کنید:",
        reply_markup=_build_settings_submenu(),
        parse_mode="HTML",
    )
    await callback.answer()


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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_pricing",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_pricing",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر به تومان وارد کنید. مثال: <code>5000</code>\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_pricing",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_pricing",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید. مثال: <code>50000</code>\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
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
                    text="🔙 بازگشت به قیمت و مالی",
                    callback_data="admin_pricing_menu",
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_pricing",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_pricing",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )


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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_pricing",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_pricing",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )


async def _build_price_tiers_panel() -> tuple[str, InlineKeyboardMarkup]:
    from services.pricing import get_pricing_config

    config = await get_pricing_config()
    is_enabled = config.get("volume_tiers_enabled", True)
    base_rate = config["base_gb_rate"]
    volume_tiers = config["volume_tiers"]
    fallback_rate = config["fallback_gb_rate"]

    status_str = (
        "🟢 فعال (محاسبه پلکانی)"
        if is_enabled
        else "🔴 غیرفعال (قیمت ثابت بر اساس نرخ پایه)"
    )
    toggle_btn_text = (
        "🔴 غیرفعال‌سازی تخفیف پلکانی" if is_enabled else "🟢 فعال‌سازی تخفیف پلکانی"
    )

    tiers_desc = ""
    for max_gb, rate in sorted(volume_tiers, key=lambda x: x[0]):
        tiers_desc += (
            f"  • تا {to_persian_digits(max_gb)} گیگ: {format_price(rate)} / GB\n"
        )
    last_max = volume_tiers[-1][0] if volume_tiers else 100
    tiers_desc += f"  • بالای {to_persian_digits(last_max)} گیگ: {format_price(fallback_rate)} / GB\n"

    text = (
        f"📊 <b>مدیریت تخفیف پلکانی حجم</b>\n\n"
        f"🔘 <b>وضعیت سیستم:</b> <b>{status_str}</b>\n"
        f"💵 <b>نرخ پایه هر گیگ (قیمت ثابت):</b> <b>{format_price(base_rate)}</b>\n\n"
        f"📋 <b>پله‌های تعریف‌شده فعلی:</b>\n{tiers_desc}\n"
        f"💡 <i>در صورت غیرفعال بودن تخفیف پلکانی، هزینه هر گیگابایت برای تمامی حجم‌ها بر اساس نرخ پایه ({format_price(base_rate)}) محاسبه خواهد شد.</i>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_btn_text,
                    callback_data="admin_price_tiers_toggle",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ ویرایش پله‌های تخفیف",
                    callback_data="admin_price_tiers_edit",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به قیمت و مالی",
                    callback_data="admin_pricing_menu",
                )
            ],
        ]
    )
    return text, keyboard


@router.callback_query(F.data == "admin_price_tiers_menu")
async def admin_price_tiers_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "pricing"):
        return
    await state.clear()

    text, keyboard = await _build_price_tiers_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data == "admin_price_tiers_toggle")
async def admin_price_tiers_toggle(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "pricing"):
        return

    from services.pricing import get_pricing_config, set_volume_tiers_enabled

    config = await get_pricing_config()
    current_status = config.get("volume_tiers_enabled", True)
    new_status = not current_status
    await set_volume_tiers_enabled(new_status)

    msg = "فعال" if new_status else "غیرفعال"
    await callback.answer(f"✅ تخفیف پلکانی {msg} شد.")
    text, keyboard = await _build_price_tiers_panel()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_price_tiers_edit")
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_price_tiers_menu",
                    )
                ]
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_tiers_text, F.text)
async def admin_price_tiers_save(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        text, keyboard = await _build_price_tiers_panel()
        await message.answer(
            "❌ عملیات لغو شد.\n\n" + text, reply_markup=keyboard, parse_mode="HTML"
        )
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
        text, keyboard = await _build_price_tiers_panel()
        await message.answer(
            f"✅ پله‌های تخفیف حجم با موفقیت به روزرسانی شدند!\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    except Exception:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_price_tiers_menu",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ فرمت وارد شده نامعتبر است.\n"
            "لطفاً مطابق مثال ارسال کنید:\n"
            "<code>20:5000\n50:4500\n100:4000\ndefault:3500</code>\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
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
                    text="📊 آمار و آخرین دریافت‌کنندگان تست",
                    callback_data="admin_test_stats_report",
                ),
            ],
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
                    text="🔙 بازگشت به تنظیمات", callback_data="admin_settings_menu"
                ),
            ],
        ]
    )

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_test_stats_report")
async def admin_test_stats_report(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "test_sub"):
        return
    await state.clear()

    from db.models import get_recent_test_sub_users, get_test_sub_statistics
    from utils.formatting import format_datetime

    stats = await get_test_sub_statistics()
    recent_users = await get_recent_test_sub_users(10)

    stats_lines = [
        "📊 <b>آمار و گزارش اشتراک‌های تست رایگان</b>\n",
        "📈 <b>تعداد تست‌های دریافت شده در بازه‌های زمانی:</b>",
        f"• ☀️ <b>امروز:</b> {to_persian_digits(stats['today'])} مورد",
        f"• 🌙 <b>دیروز:</b> {to_persian_digits(stats['yesterday'])} مورد",
        f"• 🗓 <b>۷ روز گذشته:</b> {to_persian_digits(stats['last_7_days'])} مورد",
        f"• 📅 <b>۳۰ روز گذشته:</b> {to_persian_digits(stats['last_30_days'])} مورد",
        f"• 🏆 <b>مجموع کل تست‌ها:</b> {to_persian_digits(stats['total_tests'])} بار ({to_persian_digits(stats['total_users'])} کاربر)",
        "\n━━━━━━━━━━━━━━━━━━━━\n",
        "👥 <b>۱۰ کاربر اخیر دریافت‌کننده اشتراک تست:</b>\n",
    ]

    if not recent_users:
        stats_lines.append("<i>هنوز هیچ اشتراک تستی ثبت نشده است.</i>")
    else:
        for idx, u in enumerate(recent_users, 1):
            tg_id = u["tg_id"]
            username = u.get("username")
            username_str = f"@{username}" if username else "نامشخص"
            full_name = u.get("full_name") or "کاربر"
            test_count = u.get("test_used", 1)
            last_dt = format_datetime(u.get("last_test_at"))

            stats_lines.append(
                f"{to_persian_digits(idx)}️⃣ <b>{full_name}</b> ({username_str})\n"
                f"   🆔 <code>{tg_id}</code> | 🔢 دفعات تست: {to_persian_digits(test_count)} بار\n"
                f"   📅 آخرین دریافت: <code>{last_dt}</code>\n"
            )

    text = "\n".join(stats_lines)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 به‌روزرسانی گزارش",
                    callback_data="admin_test_stats_report",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به تنظیمات تست",
                    callback_data="admin_test_menu",
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


@router.callback_query(F.data == "admin_test_gb")
async def admin_test_gb_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_test_gb)
    await callback.message.edit_text(
        "📊 <b>حجم اشتراک تست را به گیگابایت (اعشاری یا صحیح) وارد کنید:</b>\n"
        "مثال: <code>0.5</code> یا <code>1</code> یا <code>2</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_settings",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_settings",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد معتبر وارد کنید. مثال: <code>0.5</code>\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_settings",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_settings",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )


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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_settings",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_settings",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )


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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        )
        await message.answer(
            f"❌ هیچ کاربری با شناسه یا نام کاربری «<code>{query}</code>» در دیتابیس ربات یافت نشد.\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
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
            rules = dc.get("rules") or {}
            if rules.get("discount_type") == "fixed":
                val_str = f"{format_price(rules.get('amount', 0))} (نقدی)"
            else:
                val_str = f"{to_persian_digits(dc['discount_percent'])}٪ تخفیف"

            text += (
                f"🔹 <b>{dc['code']}</b> — {val_str} | "
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
        rules = dc.get("rules") or {}
        if rules.get("discount_type") == "fixed":
            val_display = format_price(rules.get("amount", 0))
        else:
            val_display = f"{to_persian_digits(dc['discount_percent'])}%"
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status_symbol} {dc['code']} ({val_display})",
                    callback_data=f"admin_disc_view_{dc['code']}",
                )
            ]
        )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به قیمت و مالی", callback_data="admin_pricing_menu"
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ طول کد تخفیف باید بین ۲ تا ۳۰ کاراکتر باشد.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    from db.discounts import get_discount_code

    existing = await get_discount_code(code)
    if existing:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ این کد تخفیف قبلاً ثبت شده است. لطفاً کد دیگری وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    await state.update_data(new_code=code)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 تخفیف درصدی (%)",
                    callback_data="admin_disc_type_percent",
                ),
                InlineKeyboardButton(
                    text="💵 تخفیف مبلغ ثابت (تومان)",
                    callback_data="admin_disc_type_fixed",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                )
            ],
        ]
    )
    await message.answer(
        f"✅ کد <code>{code}</code> انتخاب شد.\n\nنوع تخفیف را مشخص فرمایید:",
        reply_markup=keyboard,
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد بین ۴ تا ۱۶ وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    from db.discounts import generate_random_code, get_discount_code

    code = generate_random_code(length)
    while await get_discount_code(code):
        code = generate_random_code(length)

    await state.update_data(new_code=code)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 تخفیف درصدی (%)",
                    callback_data="admin_disc_type_percent",
                ),
                InlineKeyboardButton(
                    text="💵 تخفیف مبلغ ثابت (تومان)",
                    callback_data="admin_disc_type_fixed",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                )
            ],
        ]
    )
    await message.answer(
        f"🎲 کد تصادفی <code>{code}</code> تولید گردید.\n\nنوع تخفیف را مشخص فرمایید:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_disc_type_percent")
async def admin_disc_type_percent(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_disc_percent)
    await state.update_data(discount_type="percent")
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                )
            ]
        ]
    )
    await callback.message.edit_text(
        "📊 <b>درصد تخفیف را بین ۱ تا ۱۰۰ وارد کنید:</b>\n"
        "مثال: <code>20</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_disc_type_fixed")
async def admin_disc_type_fixed(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await state.set_state(AdminControlStates.waiting_disc_fixed_amount)
    await state.update_data(discount_type="fixed")
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                )
            ]
        ]
    )
    await callback.message.edit_text(
        "💵 <b>مبلغ ثابت تخفیف به تومان را وارد کنید:</b>\n"
        "مثال: <code>50000</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_fixed_amount, F.text)
async def admin_disc_create_fixed_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        amount = int(clean)
        if amount <= 0:
            raise ValueError
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک مبلغ معتبر به تومان (بزرگتر از صفر) وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    await state.update_data(
        discount_type="fixed", new_fixed_amount=amount, new_percent=0
    )
    await state.set_state(AdminControlStates.waiting_disc_max_uses)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                )
            ]
        ]
    )
    await message.answer(
        f"✅ مبلغ تخفیف: <b>{format_price(amount)}</b>\n\n"
        "🔢 <b>حداکثر تعداد استفاده از این کد را وارد کنید:</b>\n"
        "(برای <b>استفاده بی‌نهایت</b> عدد <code>0</code> را ارسال کنید)\n"
        "مثال: <code>50</code> یا <code>0</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح بین ۱ تا ۱۰۰ وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    await state.update_data(new_percent=percent)
    await state.set_state(AdminControlStates.waiting_disc_max_uses)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                )
            ]
        ]
    )
    await message.answer(
        f"✅ میزان تخفیف: <b>{to_persian_digits(percent)}٪</b>\n\n"
        "🔢 <b>حداکثر تعداد استفاده از این کد را وارد کنید:</b>\n"
        "(برای <b>استفاده بی‌نهایت</b> عدد <code>0</code> را ارسال کنید)\n"
        "مثال: <code>50</code> یا <code>0</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_discounts_menu"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید (0 برای بی‌نهایت).\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    code = data["new_code"]
    disc_type = data.get("discount_type", "percent")
    percent = data.get("new_percent", 0)
    fixed_amt = data.get("new_fixed_amount", 0)
    uses_value = -1 if max_uses == 0 else max_uses

    rules = {
        "discount_type": disc_type,
        "amount": fixed_amt if disc_type == "fixed" else percent,
    }

    from db.discounts import create_discount_code

    success = await create_discount_code(
        code=code,
        discount_percent=percent if disc_type == "percent" else 0,
        max_uses=uses_value,
        rules=rules,
    )
    await state.clear()

    if success:
        val_display = (
            f"<b>{format_price(fixed_amt)}</b> (مبلغ ثابت)"
            if disc_type == "fixed"
            else f"<b>{to_persian_digits(percent)}٪</b>"
        )
        max_str = "بی‌نهایت" if uses_value == -1 else f"{to_persian_digits(uses_value)}"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ تنظیم شروط کد تخفیف",
                        callback_data=f"admin_disc_rules_{code}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏷️ مشاهده مشخصات کد",
                        callback_data=f"admin_disc_view_{code}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 لیست کدهای تخفیف",
                        callback_data="admin_discounts_menu",
                    )
                ],
            ]
        )
        await message.answer(
            f"✅ کد تخفیف <code>{code}</code> با مقدار {val_display} و ظرفیت <b>{max_str}</b> ساخته شد!\n\n"
            "اکنون می‌توانید با دکمه زیر شروط مورد نظرتان (حجم، مدت، نوع سفارش، خریدار بار اول، انقضا و...) را به آن اضافه کنید:",
            reply_markup=kb,
            parse_mode="HTML",
        )
    else:
        await message.answer("❌ خطا در ثبت کد تخفیف. ممکن است کد تکراری باشد.")


def _format_discount_rules_summary(rules: dict) -> str:
    lines = []

    order_type = rules.get("order_type", "all")
    if order_type == "new":
        lines.append("▫️ نوع سفارش: <b>فقط خریدهای جدید</b>")
    elif order_type == "renew":
        lines.append("▫️ نوع سفارش: <b>فقط تمدید اشتراک</b>")
    else:
        lines.append("▫️ نوع سفارش: <b>همه سفارش‌ها (جدید و تمدید)</b>")

    if rules.get("first_time_only"):
        lines.append("▫️ مخاطب: <b>فقط خریداران بار اول (بدون فاکتور قبلی)</b>")

    min_gb = rules.get("min_gb")
    max_gb = rules.get("max_gb")
    if min_gb and max_gb:
        lines.append(
            f"▫️ محدوده حجم: <b>از {to_persian_digits(min_gb)} تا {to_persian_digits(max_gb)} گیگابایت</b>"
        )
    elif min_gb:
        lines.append(f"▫️ حداقل حجم: <b>{to_persian_digits(min_gb)} گیگابایت</b>")
    elif max_gb:
        lines.append(f"▫️ حداکثر حجم: <b>{to_persian_digits(max_gb)} گیگابایت</b>")

    durs = rules.get("allowed_durations")
    if durs:
        durs_str = "، ".join(f"{to_persian_digits(d)} روزه" for d in sorted(durs))
        lines.append(f"▫️ دوره‌های مجاز: <b>{durs_str}</b>")

    min_amt = rules.get("min_amount")
    if min_amt:
        lines.append(f"▫️ حداقل مبلغ سبد خرید: <b>{format_price(min_amt)}</b>")

    cap = rules.get("max_discount_amount")
    if cap:
        lines.append(f"▫️ سقف تخفیف درصدی: <b>{format_price(cap)}</b>")

    users = rules.get("allowed_users")
    if users:
        u_display = ", ".join(
            str(u) if str(u).startswith("@") or str(u).isdigit() else f"@{u}"
            for u in users[:3]
        )
        if len(users) > 3:
            u_display += f" و {to_persian_digits(len(users) - 3)} کاربر دیگر"
        lines.append(f"▫️ کاربران مجاز: <b>{u_display}</b>")

    groups = rules.get("allowed_groups")
    if groups:
        lines.append(f"▫️ گروه‌های کلاینت مجاز: <b>{'، '.join(groups)}</b>")

    user_lim = rules.get("max_uses_per_user", 1)
    if user_lim in (-1, 0):
        lines.append("▫️ دفعات مجاز هر کاربر: <b>نامحدود</b>")
    else:
        lines.append(f"▫️ دفعات مجاز هر کاربر: <b>{to_persian_digits(user_lim)} بار</b>")

    exp_str = rules.get("expires_at")
    if exp_str:
        lines.append(f"▫️ تاریخ انقضا: <b>{format_datetime(exp_str)}</b>")
    else:
        lines.append("▫️ مهلت زمانی: <b>همیشگی (بدون انقضا)</b>")

    if not lines:
        return "▫️ <i>بدون شرط خاص (آزاد برای همه سفارش‌ها و کاربران)</i>"
    return "\n".join(lines)


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

    rules = dc.get("rules") or {}
    disc_type = rules.get("discount_type", "percent")
    if disc_type == "fixed":
        type_str = f"💵 مبلغ ثابت: <b>{format_price(rules.get('amount', 0))}</b>"
        edit_val_btn = "💵 تغییر مبلغ تخفیف"
    else:
        max_cap = rules.get("max_discount_amount")
        cap_str = f" (سقف: {format_price(max_cap)})" if max_cap else ""
        type_str = (
            f"📊 درصدی: <b>{to_persian_digits(dc['discount_percent'])}٪</b>{cap_str}"
        )
        edit_val_btn = "📊 تغییر درصد تخفیف"

    rules_summary = _format_discount_rules_summary(rules)

    text = (
        f"🏷️ <b>جزئیات کد تخفیف:</b> <code>{dc['code']}</code>\n\n"
        f"🔹 <b>نوع و ارزش:</b> {type_str}\n"
        f"🔢 <b>میزان استفاده کل:</b> {to_persian_digits(dc['used_count'])} از {max_uses_str}\n"
        f"🔘 <b>وضعیت:</b> {status_str}\n\n"
        f"📋 <b>شروط و محدودیت‌های فعال:</b>\n"
        f"{rules_summary}\n\n"
        "جهت تغییر ویژگی‌ها، شروط یا حذف، گزینه مورد نظر را انتخاب کنید:"
    )

    toggle_btn_text = "🔴 غیرفعال‌سازی کد" if dc["is_active"] else "🟢 فعال‌سازی کد"

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
                    text="⚙️ تنظیم و مدیریت شروط کد تخفیف",
                    callback_data=f"admin_disc_rules_{dc['code']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=edit_val_btn,
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
    from db.discounts import get_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await callback.answer("❌ کد تخفیف یافت نشد.", show_alert=True)
        return

    rules = dc.get("rules") or {}
    disc_type = rules.get("discount_type", "percent")

    await state.set_state(AdminControlStates.waiting_disc_edit_percent)
    await state.update_data(edit_code=code, disc_type=disc_type)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_disc_view_{code}",
                )
            ]
        ]
    )

    if disc_type == "fixed":
        prompt = (
            f"💵 <b>مبلغ جدید تخفیف را برای کد <code>{code}</code> به تومان وارد کنید:</b>\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>"
        )
    else:
        prompt = (
            f"📊 <b>درصد جدید تخفیف را برای کد <code>{code}</code> (۱ تا ۱۰۰) وارد کنید:</b>\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>"
        )

    await callback.message.edit_text(prompt, reply_markup=cancel_kb, parse_mode="HTML")
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_edit_percent, F.text)
async def admin_disc_edit_percent_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    data = await state.get_data()
    code = data.get("edit_code")
    disc_type = data.get("disc_type", "percent")
    if not code:
        await state.clear()
        return

    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await state.clear()
        await message.answer("❌ کد تخفیف یافت نشد.")
        return

    rules = dc.get("rules") or {}

    try:
        clean = persian_to_english_digits(message.text.strip())
        val = int(clean)
        if disc_type == "fixed":
            if val < 1000:
                err_kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="❌ انصراف و بازگشت",
                                callback_data=f"admin_disc_view_{code}",
                            )
                        ]
                    ]
                )
                await message.answer(
                    "❌ حداقل مبلغ تخفیف ۱,۰۰۰ تومان است.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                    reply_markup=err_kb,
                    parse_mode="HTML",
                )
                return
            rules["amount"] = val
            await update_discount_code(code, rules=rules)
            msg_val = format_price(val)
        else:
            if val < 1 or val > 100:
                err_kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="❌ انصراف و بازگشت",
                                callback_data=f"admin_disc_view_{code}",
                            )
                        ]
                    ]
                )
                await message.answer(
                    "❌ لطفاً یک عدد صحیح بین ۱ تا ۱۰۰ وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                    reply_markup=err_kb,
                    parse_mode="HTML",
                )
                return
            rules["amount"] = val
            await update_discount_code(code, discount_percent=val, rules=rules)
            msg_val = f"{to_persian_digits(val)}٪"
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_disc_view_{code}",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد معتبر وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏷 مشاهده کد تخفیف",
                    callback_data=f"admin_disc_view_{code}",
                )
            ]
        ]
    )
    await message.answer(
        f"✅ میزان تخفیف کد <code>{code}</code> به <b>{msg_val}</b> تغییر یافت.",
        reply_markup=kb,
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
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_disc_view_{code}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"⏱ <b>حداکثر سقف استفاده جدید برای کد <code>{code}</code> را وارد کنید:</b>\n"
        "(جهت استفاده <b>بی‌نهایت</b> عدد <code>0</code> را وارد کنید)\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_edit_max_uses, F.text)
async def admin_disc_edit_max_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = data.get("edit_code")
    if not code:
        await state.clear()
        return

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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_disc_view_{code}",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید (0 برای بی‌نهایت).\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    from db.discounts import update_discount_code

    uses_value = -1 if val == 0 else val
    await update_discount_code(code, max_uses=uses_value)
    await state.clear()
    max_str = "بی‌نهایت" if uses_value == -1 else f"{to_persian_digits(uses_value)}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏷 مشاهده کد تخفیف",
                    callback_data=f"admin_disc_view_{code}",
                )
            ]
        ]
    )
    await message.answer(
        f"✅ سقف استفاده از کد <code>{code}</code> به <b>{max_str}</b> تغییر یافت.",
        reply_markup=kb,
        parse_mode="HTML",
    )


async def _build_disc_rules_menu_content(code: str) -> tuple[str, InlineKeyboardMarkup]:
    from db.discounts import get_discount_code

    dc = await get_discount_code(code)
    if not dc:
        return "❌ کد تخفیف یافت نشد.", InlineKeyboardMarkup(inline_keyboard=[])

    rules = dc.get("rules") or {}
    dtype = rules.get("discount_type", "percent")

    summary = _format_discount_rules_summary(rules)
    text = (
        f"⚙️ <b>مدیریت و تنظیم شروط کد تخفیف:</b> <code>{code}</code>\n\n"
        f"📋 <b>وضعیت شروط فعلی:</b>\n"
        f"{summary}\n\n"
        "جهت تغییر هر یک از شروط، دکمه مورد نظر را لمس کنید:"
    )

    otype = rules.get("order_type", "all")
    otype_map = {"all": "🌐 همه", "new": "✨ فقط جدید", "renew": "🔄 فقط تمدید"}
    btn_otype = InlineKeyboardButton(
        text=f"نوع سفارش: {otype_map.get(otype, 'همه')}",
        callback_data=f"admin_disc_r_ordertype_{code}",
    )

    first_time = rules.get("first_time_only", False)
    btn_first_time = InlineKeyboardButton(
        text=f"فقط خرید اول: {'🟢 بله' if first_time else '⚪️ خیر'}",
        callback_data=f"admin_disc_r_firsttime_{code}",
    )

    min_gb = rules.get("min_gb")
    max_gb = rules.get("max_gb")
    btn_min_gb = InlineKeyboardButton(
        text=f"📉 حداقل: {to_persian_digits(min_gb)}GB" if min_gb else "📉 حداقل: آزاد",
        callback_data=f"admin_disc_r_mingb_{code}",
    )
    btn_max_gb = InlineKeyboardButton(
        text=f"📈 حداکثر: {to_persian_digits(max_gb)}GB"
        if max_gb
        else "📈 حداکثر: آزاد",
        callback_data=f"admin_disc_r_maxgb_{code}",
    )

    durs = rules.get("allowed_durations")
    dur_label = f"{to_persian_digits(len(durs))} دوره" if durs else "همه دوره‌ها"
    btn_durs = InlineKeyboardButton(
        text=f"⏱ دوره‌ها: {dur_label}",
        callback_data=f"admin_disc_r_durs_menu_{code}",
    )

    min_amt = rules.get("min_amount")
    btn_min_amt = InlineKeyboardButton(
        text=f"💳 حداقل مبلغ: {format_price(min_amt)}"
        if min_amt
        else "💳 حداقل مبلغ: آزاد",
        callback_data=f"admin_disc_r_minamt_{code}",
    )

    extra_rows = []
    if dtype == "percent":
        cap = rules.get("max_discount_amount")
        btn_cap = InlineKeyboardButton(
            text=f"🛑 سقف تخفیف: {format_price(cap)}"
            if cap
            else "🛑 سقف تخفیف: بدون سقف",
            callback_data=f"admin_disc_r_maxcap_{code}",
        )
        extra_rows.append([btn_cap])

    users = rules.get("allowed_users")
    btn_users = InlineKeyboardButton(
        text=f"👥 کاربران: {to_persian_digits(len(users))} کاربر"
        if users
        else "👥 کاربران: همه",
        callback_data=f"admin_disc_r_users_{code}",
    )

    groups = rules.get("allowed_groups")
    btn_groups = InlineKeyboardButton(
        text=f"🏷 گروه‌ها: {to_persian_digits(len(groups))} گروه"
        if groups
        else "🏷 گروه‌ها: همه",
        callback_data=f"admin_disc_r_groups_menu_{code}",
    )

    user_lim = rules.get("max_uses_per_user", 1)
    user_lim_str = (
        "نامحدود" if user_lim in (-1, 0) else f"{to_persian_digits(user_lim)} بار"
    )
    btn_user_lim = InlineKeyboardButton(
        text=f"🔁 هر کاربر: {user_lim_str}",
        callback_data=f"admin_disc_r_userlim_{code}",
    )

    exp_str = rules.get("expires_at")
    exp_btn_label = "دارد ⏳" if exp_str else "بدون انقضا"
    btn_exp = InlineKeyboardButton(
        text=f"⏳ انقضا: {exp_btn_label}",
        callback_data=f"admin_disc_r_exp_menu_{code}",
    )

    btn_reset = InlineKeyboardButton(
        text="🧹 حذف تمامی شروط (ریست)",
        callback_data=f"admin_disc_r_reset_{code}",
    )
    btn_back = InlineKeyboardButton(
        text="🔙 بازگشت به جزئیات کد تخفیف",
        callback_data=f"admin_disc_view_{code}",
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [btn_otype, btn_first_time],
            [btn_min_gb, btn_max_gb],
            [btn_durs, btn_min_amt],
            *extra_rows,
            [btn_users, btn_groups],
            [btn_user_lim, btn_exp],
            [btn_reset],
            [btn_back],
        ]
    )
    return text, keyboard


async def _build_disc_durs_menu_content(code: str) -> tuple[str, InlineKeyboardMarkup]:
    from db.discounts import get_discount_code

    dc = await get_discount_code(code)
    if not dc:
        return "❌ کد تخفیف یافت نشد.", InlineKeyboardMarkup(inline_keyboard=[])

    rules = dc.get("rules") or {}
    allowed = set(rules.get("allowed_durations") or [])

    preset_durs = [30, 60, 90, 180, 365]
    buttons = []
    row = []
    for d in preset_durs:
        label = (
            f"✅ {to_persian_digits(d)} روزه"
            if d in allowed
            else f"{to_persian_digits(d)} روزه"
        )
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"admin_disc_r_td_{code}_{d}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append(
        [
            InlineKeyboardButton(
                text="❌ پاکسازی (مجاز بودن همه دوره‌ها)",
                callback_data=f"admin_disc_r_durs_clear_{code}",
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به شروط",
                callback_data=f"admin_disc_rules_{code}",
            )
        ]
    )

    text = (
        f"⏱ <b>تنظیم دوره‌های مجاز برای کد:</b> <code>{code}</code>\n\n"
        "با لمس هر دوره، وضعیت انتخاب آن جابجا می‌شود.\n"
        "در صورتی که هیچ دوره‌ای انتخاب نشود، کد روی <b>تمامی دوره‌ها</b> معتبر خواهد بود."
    )
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_disc_groups_menu_content(
    code: str,
) -> tuple[str, InlineKeyboardMarkup]:
    from db.discounts import get_discount_code
    from services import xui_api

    dc = await get_discount_code(code)
    if not dc:
        return "❌ کد تخفیف یافت نشد.", InlineKeyboardMarkup(inline_keyboard=[])

    rules = dc.get("rules") or {}
    allowed_groups = set(rules.get("allowed_groups") or [])

    groups = await xui_api.list_client_groups()
    buttons = []

    if not groups:
        text = (
            f"🏷 <b>تنظیم گروه‌های کلاینت مجاز برای کد:</b> <code>{code}</code>\n\n"
            "⚠️ هیچ گروه کلاینتی در پنل 3x-ui تعریف نشده است."
        )
    else:
        text = (
            f"🏷 <b>تنظیم گروه‌های کلاینت مجاز برای کد:</b> <code>{code}</code>\n\n"
            "گروه‌هایی که این کد تخفیف روی آن‌ها معتبر است را انتخاب کنید:\n"
            "(اگر هیچ گروهی انتخاب نشود، برای تمام گروه‌ها معتبر خواهد بود)"
        )
        row = []
        for idx, g in enumerate(groups):
            g_name = g.get("name", "")
            if not g_name:
                continue
            is_checked = g_name in allowed_groups
            label = f"✅ {g_name}" if is_checked else g_name
            row.append(
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"admin_disc_r_tg_{code}_{idx}",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    buttons.append(
        [
            InlineKeyboardButton(
                text="❌ پاکسازی (مجاز بودن همه گروه‌ها)",
                callback_data=f"admin_disc_r_groups_clear_{code}",
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به شروط",
                callback_data=f"admin_disc_rules_{code}",
            )
        ]
    )
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_disc_exp_menu_content(code: str) -> tuple[str, InlineKeyboardMarkup]:
    from db.discounts import get_discount_code

    dc = await get_discount_code(code)
    if not dc:
        return "❌ کد تخفیف یافت نشد.", InlineKeyboardMarkup(inline_keyboard=[])

    rules = dc.get("rules") or {}
    exp_str = rules.get("expires_at")
    current_exp = format_datetime(exp_str) if exp_str else "بدون تاریخ انقضا (همیشگی)"

    text = (
        f"⏳ <b>تنظیم تاریخ انقضای کد تخفیف:</b> <code>{code}</code>\n\n"
        f"▫️ مهلت فعلی: <b>{current_exp}</b>\n\n"
        "یکی از بازه‌های سریع زیر را انتخاب کنید یا تاریخ دلخواه خود را وارد نمایید:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡️ ۲۴ ساعت آینده",
                    callback_data=f"admin_disc_r_setexp_{code}_24h",
                ),
                InlineKeyboardButton(
                    text="⚡️ ۳ روز آینده",
                    callback_data=f"admin_disc_r_setexp_{code}_3d",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚡️ ۷ روز آینده",
                    callback_data=f"admin_disc_r_setexp_{code}_7d",
                ),
                InlineKeyboardButton(
                    text="⚡️ ۳۰ روز آینده",
                    callback_data=f"admin_disc_r_setexp_{code}_30d",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📅 ورود تاریخ یا روز دلخواه",
                    callback_data=f"admin_disc_r_exp_custom_{code}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ حذف تاریخ انقضا (نامحدود)",
                    callback_data=f"admin_disc_r_setexp_{code}_none",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به شروط",
                    callback_data=f"admin_disc_rules_{code}",
                ),
            ],
        ]
    )
    return text, keyboard


@router.callback_query(F.data.startswith("admin_disc_rules_"))
async def admin_disc_rules_view(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await state.clear()
    code = callback.data[len("admin_disc_rules_") :]
    text, keyboard = await _build_disc_rules_menu_content(code)
    await safe_edit_text(
        callback.message, text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_disc_r_ordertype_"))
async def admin_disc_r_ordertype(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_ordertype_") :]
    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await callback.answer("❌ کد یافت نشد.", show_alert=True)
        return

    rules = dc.get("rules") or {}
    cycle = {"all": "new", "new": "renew", "renew": "all"}
    rules["order_type"] = cycle.get(rules.get("order_type", "all"), "all")
    await update_discount_code(code, rules=rules)
    text, keyboard = await _build_disc_rules_menu_content(code)
    await safe_edit_text(
        callback.message, text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_disc_r_firsttime_"))
async def admin_disc_r_firsttime(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_firsttime_") :]
    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await callback.answer("❌ کد یافت نشد.", show_alert=True)
        return

    rules = dc.get("rules") or {}
    rules["first_time_only"] = not bool(rules.get("first_time_only", False))
    await update_discount_code(code, rules=rules)
    text, keyboard = await _build_disc_rules_menu_content(code)
    await safe_edit_text(
        callback.message, text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_disc_r_mingb_"))
async def admin_disc_r_mingb_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_mingb_") :]
    await state.set_state(AdminControlStates.waiting_disc_min_gb)
    await state.update_data(edit_code=code)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_disc_rules_{code}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"📉 <b>حداقل حجم مجاز (گیگابایت) را برای کد <code>{code}</code> وارد کنید:</b>\n"
        "(جهت حذف محدودیت عدد <code>0</code> را وارد کنید)\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_min_gb, F.text)
async def admin_disc_r_mingb_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = data.get("edit_code")
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if code:
            text, kb = await _build_disc_rules_menu_content(code)
            await message.answer(
                f"❌ عملیات لغو شد.\n\n{text}", reply_markup=kb, parse_mode="HTML"
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        val = int(clean)
        if val < 0:
            raise ValueError
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_disc_rules_{code}",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر (0 یا بیشتر) وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    if not code:
        await state.clear()
        return

    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    rules = dc.get("rules") or {} if dc else {}
    rules["min_gb"] = val if val > 0 else None
    await update_discount_code(code, rules=rules)
    await state.clear()

    text, kb = await _build_disc_rules_menu_content(code)
    res_str = (
        f"<b>{to_persian_digits(val)} گیگابایت</b>"
        if val > 0
        else "<b>حذف شد (بدون محدودیت)</b>"
    )
    await message.answer(
        f"✅ حداقل حجم مجاز برای کد <code>{code}</code> {res_str}.\n\n{text}",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_disc_r_maxgb_"))
async def admin_disc_r_maxgb_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_maxgb_") :]
    await state.set_state(AdminControlStates.waiting_disc_max_gb)
    await state.update_data(edit_code=code)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_disc_rules_{code}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"📈 <b>حداکثر حجم مجاز (گیگابایت) را برای کد <code>{code}</code> وارد کنید:</b>\n"
        "(جهت حذف محدودیت عدد <code>0</code> را وارد کنید)\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_max_gb, F.text)
async def admin_disc_r_maxgb_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = data.get("edit_code")
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if code:
            text, kb = await _build_disc_rules_menu_content(code)
            await message.answer(
                f"❌ عملیات لغو شد.\n\n{text}", reply_markup=kb, parse_mode="HTML"
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        val = int(clean)
        if val < 0:
            raise ValueError
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_disc_rules_{code}",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر (0 یا بیشتر) وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    if not code:
        await state.clear()
        return

    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    rules = dc.get("rules") or {} if dc else {}
    rules["max_gb"] = val if val > 0 else None
    await update_discount_code(code, rules=rules)
    await state.clear()

    text, kb = await _build_disc_rules_menu_content(code)
    res_str = (
        f"<b>{to_persian_digits(val)} گیگابایت</b>"
        if val > 0
        else "<b>حذف شد (بدون محدودیت)</b>"
    )
    await message.answer(
        f"✅ حداکثر حجم مجاز برای کد <code>{code}</code> {res_str}.\n\n{text}",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_disc_r_minamt_"))
async def admin_disc_r_minamt_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_minamt_") :]
    await state.set_state(AdminControlStates.waiting_disc_min_amount)
    await state.update_data(edit_code=code)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_disc_rules_{code}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"💳 <b>حداقل مبلغ سفارش (تومان) را برای کد <code>{code}</code> وارد کنید:</b>\n"
        "(جهت حذف محدودیت عدد <code>0</code> را وارد کنید)\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_min_amount, F.text)
async def admin_disc_r_minamt_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = data.get("edit_code")
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if code:
            text, kb = await _build_disc_rules_menu_content(code)
            await message.answer(
                f"❌ عملیات لغو شد.\n\n{text}", reply_markup=kb, parse_mode="HTML"
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        val = int(clean)
        if val < 0:
            raise ValueError
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_disc_rules_{code}",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک مبلغ معتبر به تومان وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    if not code:
        await state.clear()
        return

    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    rules = dc.get("rules") or {} if dc else {}
    rules["min_amount"] = val if val > 0 else None
    await update_discount_code(code, rules=rules)
    await state.clear()

    text, kb = await _build_disc_rules_menu_content(code)
    res_str = f"<b>{format_price(val)}</b>" if val > 0 else "<b>حذف شد (بدون سقف)</b>"
    await message.answer(
        f"✅ حداقل مبلغ فاکتور برای کد <code>{code}</code> {res_str}.\n\n{text}",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_disc_r_maxcap_"))
async def admin_disc_r_maxcap_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_maxcap_") :]
    await state.set_state(AdminControlStates.waiting_disc_max_cap)
    await state.update_data(edit_code=code)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_disc_rules_{code}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"🛑 <b>حداکثر سقف مبلغ تخفیف (تومان) را برای کد <code>{code}</code> وارد کنید:</b>\n"
        "(جهت بدون سقف بودن عدد <code>0</code> را وارد کنید)\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_max_cap, F.text)
async def admin_disc_r_maxcap_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = data.get("edit_code")
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if code:
            text, kb = await _build_disc_rules_menu_content(code)
            await message.answer(
                f"❌ عملیات لغو شد.\n\n{text}", reply_markup=kb, parse_mode="HTML"
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        val = int(clean)
        if val < 0:
            raise ValueError
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_disc_rules_{code}",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک مبلغ معتبر به تومان وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    if not code:
        await state.clear()
        return

    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    rules = dc.get("rules") or {} if dc else {}
    rules["max_discount_amount"] = val if val > 0 else None
    await update_discount_code(code, rules=rules)
    await state.clear()

    text, kb = await _build_disc_rules_menu_content(code)
    res_str = f"<b>{format_price(val)}</b>" if val > 0 else "<b>حذف شد (بدون سقف)</b>"
    await message.answer(
        f"✅ سقف تخفیف برای کد <code>{code}</code> {res_str}.\n\n{text}",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_disc_r_userlim_"))
async def admin_disc_r_userlim_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_userlim_") :]
    await state.set_state(AdminControlStates.waiting_disc_user_limit)
    await state.update_data(edit_code=code)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_disc_rules_{code}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"🔁 <b>حداکثر دفعات مجاز استفاده برای هر کاربر از کد <code>{code}</code> را وارد کنید:</b>\n"
        "(مثلاً <code>1</code> یا <code>2</code> — برای بی‌نهایت عدد <code>0</code> را وارد کنید)\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_user_limit, F.text)
async def admin_disc_r_userlim_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = data.get("edit_code")
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if code:
            text, kb = await _build_disc_rules_menu_content(code)
            await message.answer(
                f"❌ عملیات لغو شد.\n\n{text}", reply_markup=kb, parse_mode="HTML"
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    try:
        clean = persian_to_english_digits(message.text.strip())
        val = int(clean)
        if val < 0:
            raise ValueError
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_disc_rules_{code}",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید (0 برای بی‌نهایت).\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    if not code:
        await state.clear()
        return

    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    rules = dc.get("rules") or {} if dc else {}
    rules["max_uses_per_user"] = -1 if val == 0 else val
    await update_discount_code(code, rules=rules)
    await state.clear()

    text, kb = await _build_disc_rules_menu_content(code)
    res_str = (
        f"<b>{to_persian_digits(val)} بار به ازای هر کاربر</b>"
        if val > 0
        else "<b>نامحدود</b>"
    )
    await message.answer(
        f"✅ سقف استفاده هر کاربر از کد <code>{code}</code> به {res_str} تغییر یافت.\n\n{text}",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_disc_r_users_"))
async def admin_disc_r_users_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_users_") :]
    await state.set_state(AdminControlStates.waiting_disc_target_users)
    await state.update_data(edit_code=code)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_disc_rules_{code}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"👥 <b>شناسه عددی یا آیدی تلگرام کاربران مجاز را برای کد <code>{code}</code> وارد کنید:</b>\n\n"
        "می‌توانید چند شناسه یا یوزرنیم را با <b>فاصله، کاما یا خط جدید</b> از یکدیگر جدا کنید.\n"
        "مثال:\n"
        "<code>123456789 @username 987654321</code>\n\n"
        "برای حذف محدودیت و مجاز بودن کد برای تمامی کاربران، عدد <code>0</code> را ارسال کنید.\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_target_users, F.text)
async def admin_disc_r_users_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = data.get("edit_code")
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if code:
            text, kb = await _build_disc_rules_menu_content(code)
            await message.answer(
                f"❌ عملیات لغو شد.\n\n{text}", reply_markup=kb, parse_mode="HTML"
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not code:
        await state.clear()
        return

    raw_text = message.text.strip()
    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    rules = dc.get("rules") or {} if dc else {}

    if raw_text in ("0", "پاک", "حذف", "همه"):
        rules["allowed_users"] = []
        desc = "حذف شد (مجاز برای همه کاربران)"
    else:
        tokens = re.split(r"[\s,]+", raw_text)
        users_list: list[str | int] = []
        for t in tokens:
            t = t.strip()
            if not t:
                continue
            if t.startswith("@"):
                t = t[1:]
            clean_t = persian_to_english_digits(t)
            if clean_t.isdigit():
                users_list.append(int(clean_t))
            else:
                users_list.append(t)
        rules["allowed_users"] = users_list
        desc = f"محدود به {to_persian_digits(len(users_list))} کاربر"

    await update_discount_code(code, rules=rules)
    await state.clear()

    text, kb = await _build_disc_rules_menu_content(code)
    await message.answer(
        f"✅ کاربران مجاز برای کد <code>{code}</code> تنظیم شدند ({desc}).\n\n{text}",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_disc_r_reset_"))
async def admin_disc_r_reset(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_reset_") :]
    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await callback.answer("❌ کد یافت نشد.", show_alert=True)
        return

    old_rules = dc.get("rules") or {}
    clean_rules = {
        "discount_type": old_rules.get("discount_type", "percent"),
        "amount": old_rules.get("amount", dc.get("discount_percent", 0)),
    }
    await update_discount_code(code, rules=clean_rules)
    text, keyboard = await _build_disc_rules_menu_content(code)
    await safe_edit_text(
        callback.message, text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer("✅ تمامی شروط پاکسازی شدند.", show_alert=True)


@router.callback_query(F.data.startswith("admin_disc_r_durs_menu_"))
async def admin_disc_r_durs_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_durs_menu_") :]
    text, kb = await _build_disc_durs_menu_content(code)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_disc_r_td_"))
async def admin_disc_r_toggle_dur(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    payload = callback.data[len("admin_disc_r_td_") :]
    code, dur_str = payload.rsplit("_", 1)
    dur = int(dur_str)

    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await callback.answer("❌ کد یافت نشد.", show_alert=True)
        return

    rules = dc.get("rules") or {}
    durs = set(rules.get("allowed_durations") or [])
    if dur in durs:
        durs.remove(dur)
    else:
        durs.add(dur)
    rules["allowed_durations"] = sorted(list(durs))
    await update_discount_code(code, rules=rules)
    text, kb = await _build_disc_durs_menu_content(code)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_disc_r_durs_clear_"))
async def admin_disc_r_durs_clear(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_durs_clear_") :]
    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await callback.answer("❌ کد یافت نشد.", show_alert=True)
        return

    rules = dc.get("rules") or {}
    rules["allowed_durations"] = []
    await update_discount_code(code, rules=rules)
    text, kb = await _build_disc_durs_menu_content(code)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("✅ محدودیت دوره‌ها پاکسازی شد.")


@router.callback_query(F.data.startswith("admin_disc_r_groups_menu_"))
async def admin_disc_r_groups_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_groups_menu_") :]
    text, kb = await _build_disc_groups_menu_content(code)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_disc_r_tg_"))
async def admin_disc_r_toggle_group(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    payload = callback.data[len("admin_disc_r_tg_") :]
    code, idx_str = payload.rsplit("_", 1)
    idx = int(idx_str)

    from db.discounts import get_discount_code, update_discount_code
    from services import xui_api

    dc = await get_discount_code(code)
    if not dc:
        await callback.answer("❌ کد یافت نشد.", show_alert=True)
        return

    groups = await xui_api.list_client_groups()
    if idx < 0 or idx >= len(groups):
        await callback.answer("❌ گروه یافت نشد.", show_alert=True)
        return

    g_name = groups[idx].get("name", "")
    rules = dc.get("rules") or {}
    allowed = set(rules.get("allowed_groups") or [])
    if g_name in allowed:
        allowed.remove(g_name)
    else:
        allowed.add(g_name)
    rules["allowed_groups"] = list(allowed)
    await update_discount_code(code, rules=rules)
    text, kb = await _build_disc_groups_menu_content(code)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_disc_r_groups_clear_"))
async def admin_disc_r_groups_clear(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_groups_clear_") :]
    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await callback.answer("❌ کد یافت نشد.", show_alert=True)
        return

    rules = dc.get("rules") or {}
    rules["allowed_groups"] = []
    await update_discount_code(code, rules=rules)
    text, kb = await _build_disc_groups_menu_content(code)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("✅ محدودیت گروه‌ها پاکسازی شد.")


@router.callback_query(F.data.startswith("admin_disc_r_exp_menu_"))
async def admin_disc_r_exp_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_exp_menu_") :]
    text, kb = await _build_disc_exp_menu_content(code)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_disc_r_setexp_"))
async def admin_disc_r_setexp(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return
    payload = callback.data[len("admin_disc_r_setexp_") :]
    code, val = payload.rsplit("_", 1)

    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await callback.answer("❌ کد یافت نشد.", show_alert=True)
        return

    rules = dc.get("rules") or {}
    if val == "none":
        rules["expires_at"] = None
    else:
        hours_map = {"24h": 24, "3d": 72, "7d": 168, "30d": 720}
        h = hours_map.get(val, 24)
        exp_dt = datetime.now(timezone.utc) + timedelta(hours=h)
        rules["expires_at"] = exp_dt.isoformat()

    await update_discount_code(code, rules=rules)
    text, kb = await _build_disc_exp_menu_content(code)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("✅ تاریخ انقضا به‌روزرسانی شد.")


@router.callback_query(F.data.startswith("admin_disc_r_exp_custom_"))
async def admin_disc_r_exp_custom_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    code = callback.data[len("admin_disc_r_exp_custom_") :]
    await state.set_state(AdminControlStates.waiting_disc_expiry_custom)
    await state.update_data(edit_code=code)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_disc_exp_{code}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"📅 <b>ورود تاریخ یا مهلت انقضا برای کد <code>{code}</code>:</b>\n\n"
        "می‌توانید به دو روش وارد کنید:\n"
        "۱. <b>تعداد روز:</b> مثلاً <code>10</code> (۱۰ روز از اکنون)\n"
        "۲. <b>تاریخ شمسی:</b> مثلاً <code>1404/06/31</code> یا <code>1404/06/31 23:59</code>\n\n"
        "برای حذف انقضا عدد <code>0</code> را ارسال کنید.\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_disc_expiry_custom, F.text)
async def admin_disc_r_exp_custom_save(
    message: types.Message, state: FSMContext
) -> None:
    data = await state.get_data()
    code = data.get("edit_code")
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if code:
            text, kb = await _build_disc_exp_menu_content(code)
            await message.answer(
                f"❌ عملیات لغو شد.\n\n{text}", reply_markup=kb, parse_mode="HTML"
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not code:
        await state.clear()
        return

    from db.discounts import get_discount_code, update_discount_code

    dc = await get_discount_code(code)
    if not dc:
        await state.clear()
        await message.answer("❌ کد تخفیف یافت نشد.")
        return

    rules = dc.get("rules") or {}
    clean = persian_to_english_digits(message.text.strip())

    if clean == "0":
        rules["expires_at"] = None
        exp_desc = "حذف شد (بدون تاریخ انقضا)"
    elif clean.isdigit():
        days = int(clean)
        if days <= 0:
            rules["expires_at"] = None
            exp_desc = "حذف شد (بدون تاریخ انقضا)"
        else:
            exp_dt = datetime.now(timezone.utc) + timedelta(days=days)
            rules["expires_at"] = exp_dt.isoformat()
            exp_desc = f"{to_persian_digits(days)} روز دیگر ({format_datetime(rules['expires_at'])})"
    else:
        clean_date = clean.replace("-", "/").strip()
        import jdatetime

        exp_iso = None
        for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
            try:
                jdt = jdatetime.datetime.strptime(clean_date, fmt)
                if fmt == "%Y/%m/%d":
                    jdt = jdt.replace(hour=23, minute=59, second=59)
                greg = jdt.togregorian()
                tz_iran = timezone(timedelta(hours=3, minutes=30))
                greg = greg.replace(tzinfo=tz_iran)
                exp_iso = greg.astimezone(timezone.utc).isoformat()
                break
            except Exception:
                continue

        if not exp_iso:
            err_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ انصراف و بازگشت",
                            callback_data=f"admin_disc_exp_{code}",
                        )
                    ]
                ]
            )
            await message.answer(
                "❌ فرمت تاریخ نامعتبر است.\n"
                "لطفاً تعداد روز (مثلاً <code>7</code>) یا تاریخ شمسی (مانند <code>1404/06/31</code>) وارد کنید:\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=err_kb,
                parse_mode="HTML",
            )
            return
        rules["expires_at"] = exp_iso
        exp_desc = format_datetime(exp_iso)

    await update_discount_code(code, rules=rules)
    await state.clear()
    text, kb = await _build_disc_rules_menu_content(code)
    await message.answer(
        f"✅ تاریخ انقضای کد <code>{code}</code> تنظیم شد: <b>{exp_desc}</b>\n\n{text}",
        reply_markup=kb,
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
                    text="🔙 بازگشت به تنظیمات", callback_data="admin_settings_menu"
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_settings",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_settings",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح بین ۱ تا ۱۰۰ وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
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
                        text="🔙 بازگشت به تنظیمات", callback_data="admin_settings_menu"
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
                text="🩺 پایش سلامت اینباندها (Health Check)",
                callback_data="admin_inbound_monitor_menu:inbounds",
            )
        ]
    )
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به تنظیمات", callback_data="admin_settings_menu"
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


@router.callback_query(F.data.startswith("admin_inbound_monitor_menu"))
async def admin_inbound_monitor_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return

    source = "settings"
    parts = callback.data.split(":")
    if len(parts) > 1 and parts[1] in ("settings", "inbounds"):
        source = parts[1]
        await state.update_data(monitor_source=source)
    else:
        st_data = await state.get_data()
        source = st_data.get("monitor_source", "settings")

    await state.set_state(None)

    from db.models import get_inbound_monitor_config
    from services.inbound_monitor import get_default_target_host

    cfg = await get_inbound_monitor_config()
    enabled = cfg.get("enabled", False)
    interval = cfg.get("interval_seconds", 60)
    timeout = cfg.get("timeout_seconds", 5.0)
    target_host = cfg.get("target_host") or ""
    monitored_ids = cfg.get("monitored_inbound_ids", [])
    default_host = get_default_target_host()

    status_icon = "🟢 فعال" if enabled else "🔴 غیرفعال"
    toggle_text = "🔴 غیرفعال‌سازی پایش" if enabled else "🟢 فعال‌سازی پایش"

    active_host_display = target_host if target_host else f"پیش‌فرض ({default_host})"
    if monitored_ids:
        inbounds_display = (
            f"<b>{to_persian_digits(len(monitored_ids))}</b> اینباند اختصاصی"
        )
    else:
        inbounds_display = "<b>تمامی اینباندهای فعال سرور</b>"

    if interval >= 60 and interval % 60 == 0:
        interval_display = f"{to_persian_digits(interval // 60)} دقیقه ({to_persian_digits(interval)} ثانیه)"
    else:
        interval_display = f"{to_persian_digits(interval)} ثانیه"

    text = (
        "🩺 <b>پایش سلامت و تست خودکار اینباندها (Health Check)</b>\n\n"
        "این سیستم به صورت خودکار و در بازه‌های زمانی مشخص، پورت و پاسخگویی تک‌تک اینباندها را از طریق اتصال سوکت TCP پایش می‌کند.\n"
        "در صورت قطعی یا عدم پاسخگویی پورت، بلافاصله اخطار به مدیران ارسال شده و پس از رفع مشکل و اتصال مجدد نیز اعلان وصل شدن ارسال می‌گردد.\n\n"
        f"📊 <b>وضعیت سیستم:</b> {status_icon}\n"
        f"⏱ <b>بازه بررسی:</b> <b>{interval_display}</b>\n"
        f"⏳ <b>مهلت تایم‌اوت:</b> <b>{to_persian_digits(timeout)} ثانیه</b>\n"
        f"🖥 <b>آدرس سرور پایش:</b> <code>{active_host_display}</code>\n"
        f"🎯 <b>اینباندهای تحت نظر:</b> {inbounds_display}\n\n"
        "جهت تغییر تنظیمات یا اجرای تست زنده، یکی از گزینه‌های زیر را انتخاب نمایید:"
    )

    if source == "inbounds":
        back_btn_row = [
            InlineKeyboardButton(
                text="🔙 بازگشت به مدیریت اینباندها",
                callback_data="admin_inbounds_menu",
            )
        ]
    else:
        back_btn_row = [
            InlineKeyboardButton(
                text="🔙 بازگشت به تنظیمات",
                callback_data="admin_settings_menu",
            )
        ]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data="admin_inbound_monitor_toggle",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚡️ تست فوری سلامت اینباندها",
                    callback_data="admin_inbound_monitor_run_check",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎯 انتخاب اینباندها جهت پایش",
                    callback_data="admin_inbound_monitor_select_inbounds",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"⏱ بازه بررسی ({to_persian_digits(interval)}s)",
                    callback_data="admin_inbound_monitor_interval_menu",
                ),
                InlineKeyboardButton(
                    text=f"⏳ تایم‌اوت ({to_persian_digits(timeout)}s)",
                    callback_data="admin_inbound_monitor_timeout_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🖥 تنظیم آدرس / IP سرور",
                    callback_data="admin_inbound_monitor_host_prompt",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 بازنشانی تنظیمات پایش به پیش‌فرض",
                    callback_data="admin_inbound_monitor_reset",
                ),
            ],
            back_btn_row,
        ]
    )

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_inbound_monitor_toggle")
async def admin_inbound_monitor_toggle(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    from db.models import get_inbound_monitor_config, set_inbound_monitor_config

    cfg = await get_inbound_monitor_config()
    new_state = not cfg.get("enabled", False)
    await set_inbound_monitor_config(enabled=new_state)

    msg = "فعال" if new_state else "غیرفعال"
    await callback.answer(
        f"✅ سیستم پایش سلامت اینباندها {msg} گردید.", show_alert=True
    )
    await admin_inbound_monitor_menu(callback, state)


@router.callback_query(F.data == "admin_inbound_monitor_run_check")
async def admin_inbound_monitor_run_check(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return

    await callback.answer("⏳ در حال بررسی و تست پورت‌های اینباندها...")

    from services.inbound_monitor import check_inbounds_health

    results = await check_inbounds_health()
    if not results:
        await safe_edit_text(
            callback.message,
            "⚠️ <b>هیچ اینباندی جهت بررسی یافت نشد یا ارتباط با پنل برقرار نیست.</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔙 بازگشت به تنظیمات پایش",
                            callback_data="admin_inbound_monitor_menu",
                        )
                    ]
                ]
            ),
            parse_mode="HTML",
        )
        return

    now_str = format_datetime(datetime.now(timezone.utc), with_seconds=True)
    lines = []
    ok_count = 0
    fail_count = 0

    for item in results:
        ib_id = item["id"]
        remark = item["remark"]
        protocol = item["protocol"]
        port = item["port"]
        is_ok = item["is_ok"]
        is_enabled = item["enable"]
        latency = item["latency_ms"]
        err = item["error"]

        if not is_enabled:
            status_desc = "⚪️ <b>غیرفعال در پنل</b>"
        elif is_ok:
            ok_count += 1
            status_desc = f"🟢 <b>آنلاین و متصل</b> (پینگ: <b>{to_persian_digits(round(latency))} ms</b>)"
        else:
            fail_count += 1
            status_desc = f"🔴 <b>قطعی / خطا:</b> <code>{err}</code>"

        lines.append(
            f"• <b>#{to_persian_digits(ib_id)} | {remark}</b> ({protocol}:{to_persian_digits(port)})\n"
            f"  🌐 <b>آدرس پایش:</b> <code>{item['host']}</code>\n"
            f"  وضعیت: {status_desc}"
        )

    summary_status = (
        "🟢 وضعیت کلی: پایدار و عادی"
        if fail_count == 0
        else f"🚨 وضعیت کلی: دارای {to_persian_digits(fail_count)} قطعی!"
    )

    text = (
        "⚡️ <b>گزارش لحظه‌ای تست سلامت اینباندها (Live Test)</b>\n\n"
        "🖥 <b>آدرس‌های تست‌شده:</b> بر اساس <code>shareAddr</code> اختصاصی هر اینباند\n"
        f"📅 <b>زمان تست:</b> {now_str}\n"
        f"📊 <b>خلاصه وضعیت:</b> {summary_status}\n"
        f"✅ <b>سالم:</b> {to_persian_digits(ok_count)} | ❌ <b>قطعی:</b> {to_persian_digits(fail_count)}\n\n"
        + "\n\n".join(lines)
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 تست مجدد لحظه‌ای",
                    callback_data="admin_inbound_monitor_run_check",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به تنظیمات پایش",
                    callback_data="admin_inbound_monitor_menu",
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


@router.callback_query(F.data == "admin_inbound_monitor_select_inbounds")
async def admin_inbound_monitor_select_inbounds(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    await state.clear()

    from db.models import get_inbound_monitor_config
    from services import xui_api

    cfg = await get_inbound_monitor_config()
    all_inbounds = await xui_api.list_inbound_options()

    if not all_inbounds:
        await callback.answer("اینباندی در پنل یافت نشد.", show_alert=True)
        return

    all_ids = [int(ib["id"]) for ib in all_inbounds if "id" in ib]
    saved_ids = set(cfg.get("monitored_inbound_ids", []))
    is_all = len(saved_ids) == 0 or saved_ids == set(all_ids)

    buttons = []
    for ib in all_inbounds:
        ib_id = int(ib.get("id", 0))
        remark = ib.get("remark") or ib.get("tag") or f"Inbound #{ib_id}"
        port = ib.get("port", 0)
        share_addr = (ib.get("shareAddr") or "").strip()
        addr_suffix = f" [{share_addr}]" if share_addr else ""
        is_checked = is_all or (ib_id in saved_ids)
        icon = "✅" if is_checked else "⬜️"
        btn_text = f"{icon} #{to_persian_digits(ib_id)} {remark} ({to_persian_digits(port)}){addr_suffix}"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"admin_inbound_monitor_toggle_ib_{ib_id}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="✅ انتخاب همه",
                callback_data="admin_inbound_monitor_select_all",
            ),
            InlineKeyboardButton(
                text="⬜️ لغو انتخاب همه",
                callback_data="admin_inbound_monitor_unselect_all",
            ),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به تنظیمات پایش",
                callback_data="admin_inbound_monitor_menu",
            )
        ]
    )

    text = (
        "🎯 <b>انتخاب اینباندهای مورد نظر جهت پایش سلامت:</b>\n\n"
        "روی هر اینباند کلیک کنید تا وضعیت پایش آن (فعال ✅ / غیرفعال ⬜️) تغییر کند.\n"
        "آدرس تست هر اینباند در پرانتز کروشه‌ای [shareAddr] مشخص شده است.\n"
        "در صورتی که همه انتخاب شوند، تمامی اینباندهای فعلی و جدید پایش خواهند شد."
    )

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_inbound_monitor_toggle_ib_"))
async def admin_inbound_monitor_toggle_ib(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return

    ib_id = int(callback.data.split("_")[-1])
    from db.models import get_inbound_monitor_config, set_inbound_monitor_config
    from services import xui_api

    cfg = await get_inbound_monitor_config()
    all_inbounds = await xui_api.list_inbound_options()
    all_ids = [int(ib["id"]) for ib in all_inbounds if "id" in ib]

    saved_ids = set(cfg.get("monitored_inbound_ids", []))
    if len(saved_ids) == 0:
        current_set = set(all_ids)
    else:
        current_set = set(saved_ids)

    if ib_id in current_set:
        current_set.remove(ib_id)
    else:
        current_set.add(ib_id)

    if current_set == set(all_ids):
        new_list: list[int] = []
    else:
        new_list = sorted(list(current_set))

    await set_inbound_monitor_config(monitored_inbound_ids=new_list)
    await admin_inbound_monitor_select_inbounds(callback, state)


@router.callback_query(F.data == "admin_inbound_monitor_select_all")
async def admin_inbound_monitor_select_all(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    from db.models import set_inbound_monitor_config

    await set_inbound_monitor_config(monitored_inbound_ids=[])
    await callback.answer("✅ تمام اینباندها جهت پایش انتخاب شدند.")
    await admin_inbound_monitor_select_inbounds(callback, state)


@router.callback_query(F.data == "admin_inbound_monitor_unselect_all")
async def admin_inbound_monitor_unselect_all(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    from db.models import set_inbound_monitor_config

    await set_inbound_monitor_config(monitored_inbound_ids=[-1])
    await callback.answer("⬜️ انتخاب تمامی اینباندها لغو شد.")
    await admin_inbound_monitor_select_inbounds(callback, state)


@router.callback_query(F.data == "admin_inbound_monitor_interval_menu")
async def admin_inbound_monitor_interval_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    await state.clear()

    from db.models import get_inbound_monitor_config

    cfg = await get_inbound_monitor_config()
    current_interval = cfg.get("interval_seconds", 60)

    text = (
        "⏱ <b>تنظیم بازه زمانی پایش سلامت اینباندها:</b>\n\n"
        f"بازه فعلی: <b>{to_persian_digits(current_interval)} ثانیه</b>\n\n"
        "یکی از بازه‌های آماده زیر را انتخاب کنید یا عدد دلخواه خود (به ثانیه) را وارد نمایید:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="۳۰ ثانیه" + (" 🔘" if current_interval == 30 else ""),
                    callback_data="admin_inbound_monitor_set_interval_30",
                ),
                InlineKeyboardButton(
                    text="۱ دقیقه" + (" 🔘" if current_interval == 60 else ""),
                    callback_data="admin_inbound_monitor_set_interval_60",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="۲ دقیقه" + (" 🔘" if current_interval == 120 else ""),
                    callback_data="admin_inbound_monitor_set_interval_120",
                ),
                InlineKeyboardButton(
                    text="۵ دقیقه" + (" 🔘" if current_interval == 300 else ""),
                    callback_data="admin_inbound_monitor_set_interval_300",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="۱۰ دقیقه" + (" 🔘" if current_interval == 600 else ""),
                    callback_data="admin_inbound_monitor_set_interval_600",
                ),
                InlineKeyboardButton(
                    text="✏️ ورود دستی بازه (ثانیه)",
                    callback_data="admin_inbound_monitor_interval_prompt",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به تنظیمات پایش",
                    callback_data="admin_inbound_monitor_menu",
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


@router.callback_query(F.data.startswith("admin_inbound_monitor_set_interval_"))
async def admin_inbound_monitor_set_interval(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    sec = int(callback.data.split("_")[-1])
    from db.models import set_inbound_monitor_config

    await set_inbound_monitor_config(interval_seconds=sec)
    await callback.answer(f"✅ بازه پایش به {sec} ثانیه تنظیم شد.", show_alert=True)
    await admin_inbound_monitor_menu(callback, state)


@router.callback_query(F.data == "admin_inbound_monitor_interval_prompt")
async def admin_inbound_monitor_interval_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    await state.set_state(AdminControlStates.waiting_inbound_monitor_interval)
    text = (
        "✏️ <b>لطفاً بازه زمانی بررسی را به ثانیه وارد نمایید:</b>\n\n"
        "حداقل مقدار مجاز: ۵ ثانیه (پیشنهادی: ۳۰ الی ۳۰۰ ثانیه)\n\n"
        "💡 <i>جهت انصراف از دکمه زیر یا دستور /cancel استفاده کنید.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data="admin_inbound_monitor_menu",
                )
            ]
        ]
    )
    await safe_edit_text(
        callback.message, text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_inbound_monitor_interval, F.text)
async def admin_inbound_monitor_interval_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() in ("/cancel", "انصراف"):
        await state.clear()
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 بازگشت به تنظیمات پایش",
                        callback_data="admin_inbound_monitor_menu",
                    )
                ]
            ]
        )
        await message.answer("❌ عملیات لغو شد.", reply_markup=cancel_kb)
        return

    raw = persian_to_english_digits(message.text.strip())
    try:
        sec = int(raw)
        if sec < 5:
            raise ValueError()
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_inbound_monitor_menu",
                    )
                ]
            ]
        )
        await message.answer(
            "⚠️ لطفاً یک عدد معتبر بزرگتر یا مساوی ۵ (به ثانیه) وارد کنید:",
            reply_markup=err_kb,
        )
        return

    await state.clear()
    from db.models import set_inbound_monitor_config

    await set_inbound_monitor_config(interval_seconds=sec)
    success_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به تنظیمات پایش",
                    callback_data="admin_inbound_monitor_menu",
                )
            ]
        ]
    )
    await message.answer(
        f"✅ بازه زمانی پایش سلامت با موفقیت به <b>{to_persian_digits(sec)} ثانیه</b> تغییر یافت.",
        reply_markup=success_kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_inbound_monitor_timeout_menu")
async def admin_inbound_monitor_timeout_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    await state.clear()

    from db.models import get_inbound_monitor_config

    cfg = await get_inbound_monitor_config()
    current_timeout = cfg.get("timeout_seconds", 5.0)

    text = (
        "⏳ <b>تنظیم مهلت زمان تایم‌اوت اتصال (Connection Timeout):</b>\n\n"
        f"تایم‌اوت فعلی: <b>{to_persian_digits(current_timeout)} ثانیه</b>\n\n"
        "در صورتی که پورت اینباند در این مدت پاسخ ندهد، قطعی ثبت شده و هشدار ارسال می‌شود.\n"
        "یکی از مقادیر آماده زیر را انتخاب کنید یا عدد دلخواه خود را وارد نمایید:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="۲ ثانیه" + (" 🔘" if current_timeout == 2.0 else ""),
                    callback_data="admin_inbound_monitor_set_timeout_2",
                ),
                InlineKeyboardButton(
                    text="۳ ثانیه" + (" 🔘" if current_timeout == 3.0 else ""),
                    callback_data="admin_inbound_monitor_set_timeout_3",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="۵ ثانیه" + (" 🔘" if current_timeout == 5.0 else ""),
                    callback_data="admin_inbound_monitor_set_timeout_5",
                ),
                InlineKeyboardButton(
                    text="۱۰ ثانیه" + (" 🔘" if current_timeout == 10.0 else ""),
                    callback_data="admin_inbound_monitor_set_timeout_10",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ ورود دستی تایم‌اوت",
                    callback_data="admin_inbound_monitor_timeout_prompt",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به تنظیمات پایش",
                    callback_data="admin_inbound_monitor_menu",
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


@router.callback_query(F.data.startswith("admin_inbound_monitor_set_timeout_"))
async def admin_inbound_monitor_set_timeout(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    sec = float(callback.data.split("_")[-1])
    from db.models import set_inbound_monitor_config

    await set_inbound_monitor_config(timeout_seconds=sec)
    await callback.answer(f"✅ مهلت تایم‌اوت به {sec} ثانیه تنظیم شد.", show_alert=True)
    await admin_inbound_monitor_menu(callback, state)


@router.callback_query(F.data == "admin_inbound_monitor_timeout_prompt")
async def admin_inbound_monitor_timeout_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    await state.set_state(AdminControlStates.waiting_inbound_monitor_timeout)
    text = (
        "✏️ <b>لطفاً مهلت تایم‌اوت را به ثانیه وارد نمایید (مثلاً 2.5 یا 5):</b>\n\n"
        "حداقل مقدار مجاز: ۰.۵ ثانیه\n\n"
        "💡 <i>جهت انصراف از دکمه زیر یا دستور /cancel استفاده کنید.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data="admin_inbound_monitor_menu",
                )
            ]
        ]
    )
    await safe_edit_text(
        callback.message, text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_inbound_monitor_timeout, F.text)
async def admin_inbound_monitor_timeout_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() in ("/cancel", "انصراف"):
        await state.clear()
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 بازگشت به تنظیمات پایش",
                        callback_data="admin_inbound_monitor_menu",
                    )
                ]
            ]
        )
        await message.answer("❌ عملیات لغو شد.", reply_markup=cancel_kb)
        return

    raw = persian_to_english_digits(message.text.strip())
    try:
        sec = float(raw)
        if sec < 0.5:
            raise ValueError()
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_inbound_monitor_menu",
                    )
                ]
            ]
        )
        await message.answer(
            "⚠️ لطفاً یک عدد معتبر بزرگتر یا مساوی ۰.۵ (به ثانیه) وارد کنید:",
            reply_markup=err_kb,
        )
        return

    await state.clear()
    from db.models import set_inbound_monitor_config

    await set_inbound_monitor_config(timeout_seconds=sec)
    success_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به تنظیمات پایش",
                    callback_data="admin_inbound_monitor_menu",
                )
            ]
        ]
    )
    await message.answer(
        f"✅ زمان تایم‌اوت پایش با موفقیت به <b>{to_persian_digits(sec)} ثانیه</b> تغییر یافت.",
        reply_markup=success_kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_inbound_monitor_host_prompt")
async def admin_inbound_monitor_host_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    await state.set_state(AdminControlStates.waiting_inbound_monitor_target_host)

    from db.models import get_inbound_monitor_config
    from services.inbound_monitor import get_default_target_host

    cfg = await get_inbound_monitor_config()
    current_host = cfg.get("target_host") or ""
    default_host = get_default_target_host()

    text = (
        "🖥 <b>تنظیم آدرس سرور جهت تست اتصال اینباندها (Target Host):</b>\n\n"
        f"آدرس فعلی تنظیم‌شده: <code>{current_host if current_host else 'خالی (استفاده از پیش‌فرض)'}</code>\n"
        f"آدرس پیش‌فرض استخراج‌شده از پنل: <code>{default_host}</code>\n\n"
        "لطفاً IP یا دامنه مورد نظر را ارسال نمایید.\n"
        "برای بازگشت به آدرس پیش‌فرض پنل، عبارت <code>default</code> را ارسال کنید.\n\n"
        "💡 <i>جهت انصراف از دکمه زیر یا دستور /cancel استفاده کنید.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data="admin_inbound_monitor_menu",
                )
            ]
        ]
    )
    await safe_edit_text(
        callback.message, text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_inbound_monitor_target_host, F.text)
async def admin_inbound_monitor_host_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() in ("/cancel", "انصراف"):
        await state.clear()
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 بازگشت به تنظیمات پایش",
                        callback_data="admin_inbound_monitor_menu",
                    )
                ]
            ]
        )
        await message.answer("❌ عملیات لغو شد.", reply_markup=cancel_kb)
        return

    raw = message.text.strip()
    from db.models import set_inbound_monitor_config

    await state.clear()
    if raw.lower() in ("default", "پیشفرض", "پیش‌فرض", "reset"):
        await set_inbound_monitor_config(target_host="")
        msg = "✅ آدرس سرور پایش به حالت پیش‌فرض پنل بازنشانی گردید."
    else:
        clean_host = (
            raw.replace("http://", "")
            .replace("https://", "")
            .split("/")[0]
            .split(":")[0]
        )
        await set_inbound_monitor_config(target_host=clean_host)
        msg = f"✅ آدرس سرور پایش با موفقیت به <code>{clean_host}</code> تغییر یافت."

    success_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به تنظیمات پایش",
                    callback_data="admin_inbound_monitor_menu",
                )
            ]
        ]
    )
    await message.answer(msg, reply_markup=success_kb, parse_mode="HTML")


@router.callback_query(F.data == "admin_inbound_monitor_reset")
async def admin_inbound_monitor_reset(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "inbounds"):
        return
    from db.models import reset_inbound_monitor_config

    await reset_inbound_monitor_config()
    await callback.answer(
        "🔄 تنظیمات پایش سلامت اینباندها به حالت پیش‌فرض بازنشانی گردید.",
        show_alert=True,
    )
    await admin_inbound_monitor_menu(callback, state)


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
                text="🔙 بازگشت به مدیریت کاربران", callback_data="admin_sub_users_menu"
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_cancel_to_users"
                    )
                ]
            ]
        ),
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_cancel_to_users"
                    )
                ]
            ]
        ),
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
    card_enabled = card_config.get("enabled", True)

    status_str = "🟢 فعال" if card_enabled else "🔴 غیرفعال"
    toggle_btn_text = (
        "🔴 غیرفعال‌سازی کارت به کارت" if card_enabled else "🟢 فعال‌سازی کارت به کارت"
    )

    text = (
        f"💳 <b>تنظیمات کارت بانکی جهت واریز کارت به کارت</b>\n\n"
        f"🔢 <b>شماره کارت فعلی:</b> <code>{card_number}</code>\n"
        f"👤 <b>نام صاحب کارت فعلی:</b> <b>{card_holder}</b>\n"
        f"🔘 <b>وضعیت پرداخت کارت به کارت:</b> <b>{status_str}</b>\n\n"
        f"لطفاً یکی از گزینه‌های زیر را برای تغییر انتخاب کنید:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_btn_text,
                    callback_data="admin_card_toggle_enable",
                )
            ],
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
                    text="🔙 بازگشت به قیمت و مالی", callback_data="admin_pricing_menu"
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
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data == "admin_card_toggle_enable")
async def admin_card_toggle_enable(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "card_config"):
        return

    from db.models import get_card_config, set_card_config

    card_config = await get_card_config()
    current_enabled = card_config.get("enabled", True)
    new_status = not current_enabled
    await set_card_config(enabled=new_status)

    msg = "فعال" if new_status else "غیرفعال"
    await callback.answer(f"✅ پرداخت کارت به کارت {msg} شد.")
    await admin_card_menu(callback, state)


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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_pricing",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_pricing",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک شماره کارت ۱۶ رقمی معتبر وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_pricing",
                    )
                ]
            ]
        ),
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


async def _build_receipt_config_menu_content() -> tuple[str, InlineKeyboardMarkup]:
    from db.models import get_receipt_config

    cfg = await get_receipt_config()
    overall = cfg["overall_enabled"]
    photo = cfg["photo_enabled"]
    text_en = cfg["text_enabled"]
    action = cfg["disabled_action"]
    custom_text = cfg["disabled_text"]

    overall_badge = "🟢 فعال" if overall else "🔴 غیرفعال"
    photo_badge = "🟢 مجاز" if photo else "🔴 غیرمجاز"
    text_badge = "🟢 مجاز" if text_en else "🔴 غیرمجاز"

    action_titles = {
        "both": "🔄 هر دو (پاپ‌آپ + ویرایش پیام)",
        "alert": "💬 فقط پاپ‌آپ (Alert)",
        "message": "📝 فقط ویرایش پیام",
    }
    action_badge = action_titles.get(action, action_titles["both"])

    text = (
        f"🧾 <b>تنظیمات دریافت رسید واریز (کارت به کارت)</b>\n\n"
        f"در این بخش می‌توانید نحوه دریافت رسید پرداخت کاربران (عکس، متن یا مسدودسازی کلی) را مدیریت کنید.\n\n"
        f"🔘 <b>وضعیت کلی دریافت رسید:</b> {overall_badge}\n"
        f"📸 <b>ارسال تصویر فیش (عکس):</b> {photo_badge}\n"
        f"📝 <b>ارسال متن و شناسه پیگیری:</b> {text_badge}\n"
        f"⚡️ <b>واکنش هنگام غیرفعال بودن:</b> {action_badge}\n\n"
        f"💬 <b>متن پیام هنگام غیرفعال بودن:</b>\n"
        f"<code>{custom_text}</code>\n"
    )

    overall_btn_text = "🔴 غیرفعال‌سازی کلی" if overall else "🟢 فعال‌سازی کلی"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔘 وضعیت کلی: {overall_badge} ({overall_btn_text})",
                    callback_data="admin_toggle_receipt_overall",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📸 عکس: {photo_badge}",
                    callback_data="admin_toggle_receipt_photo",
                ),
                InlineKeyboardButton(
                    text=f"📝 متن: {text_badge}",
                    callback_data="admin_toggle_receipt_text",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"⚡️ واکنش: {action_badge}",
                    callback_data="admin_cycle_receipt_action",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ ویرایش متن پیام غیرفعال بودن",
                    callback_data="admin_edit_receipt_disabled_text",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 بازنشانی متن به پیش‌فرض",
                    callback_data="admin_reset_receipt_disabled_text",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به قیمت و مالی",
                    callback_data="admin_pricing_menu",
                )
            ],
        ]
    )
    return text, keyboard


@router.callback_query(F.data == "admin_receipt_config_menu")
async def admin_receipt_config_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "receipt_config"):
        return
    await state.clear()

    from utils.helpers import safe_edit_text

    text, keyboard = await _build_receipt_config_menu_content()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_toggle_receipt_overall")
async def admin_toggle_receipt_overall(callback: types.CallbackQuery) -> None:
    if not await _require_permission(callback, "receipt_config"):
        return

    from db.models import get_receipt_config, set_receipt_config
    from utils.helpers import safe_edit_text

    cfg = await get_receipt_config()
    new_val = not cfg["overall_enabled"]
    await set_receipt_config(overall_enabled=new_val)

    text, keyboard = await _build_receipt_config_menu_content()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    st_msg = (
        "🟢 دریافت رسید فعال شد."
        if new_val
        else "🔴 دریافت رسید به طور کلی غیرفعال شد."
    )
    await callback.answer(st_msg, show_alert=False)


@router.callback_query(F.data == "admin_toggle_receipt_photo")
async def admin_toggle_receipt_photo(callback: types.CallbackQuery) -> None:
    if not await _require_permission(callback, "receipt_config"):
        return

    from db.models import get_receipt_config, set_receipt_config
    from utils.helpers import safe_edit_text

    cfg = await get_receipt_config()
    new_val = not cfg["photo_enabled"]
    await set_receipt_config(photo_enabled=new_val)

    text, keyboard = await _build_receipt_config_menu_content()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    st_msg = (
        "🟢 ارسال تصویر رسید مجاز شد." if new_val else "🔴 ارسال تصویر رسید غیرمجاز شد."
    )
    await callback.answer(st_msg, show_alert=False)


@router.callback_query(F.data == "admin_toggle_receipt_text")
async def admin_toggle_receipt_text(callback: types.CallbackQuery) -> None:
    if not await _require_permission(callback, "receipt_config"):
        return

    from db.models import get_receipt_config, set_receipt_config
    from utils.helpers import safe_edit_text

    cfg = await get_receipt_config()
    new_val = not cfg["text_enabled"]
    await set_receipt_config(text_enabled=new_val)

    text, keyboard = await _build_receipt_config_menu_content()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    st_msg = (
        "🟢 ارسال متنی رسید مجاز شد." if new_val else "🔴 ارسال متنی رسید غیرمجاز شد."
    )
    await callback.answer(st_msg, show_alert=False)


@router.callback_query(F.data == "admin_cycle_receipt_action")
async def admin_cycle_receipt_action(callback: types.CallbackQuery) -> None:
    if not await _require_permission(callback, "receipt_config"):
        return

    from db.models import get_receipt_config, set_receipt_config
    from utils.helpers import safe_edit_text

    cfg = await get_receipt_config()
    current = cfg["disabled_action"]
    cycle_map = {
        "both": "alert",
        "alert": "message",
        "message": "both",
    }
    new_action = cycle_map.get(current, "both")
    await set_receipt_config(disabled_action=new_action)

    text, keyboard = await _build_receipt_config_menu_content()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_edit_receipt_disabled_text")
async def admin_edit_receipt_disabled_text_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "receipt_config"):
        return

    await state.set_state(AdminControlStates.waiting_receipt_disabled_text)
    from utils.helpers import safe_edit_text

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_receipt_config_menu"
                )
            ]
        ]
    )
    text = (
        "✏️ <b>ویرایش متن پیام غیرفعال بودن دریافت رسید:</b>\n\n"
        "لطفاً متن جدیدی که هنگام غیرفعال بودن دریافت رسید به کاربر نمایش داده می‌شود را ارسال فرمایید.\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>"
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_receipt_disabled_text, F.text)
async def admin_edit_receipt_disabled_text_save(
    message: types.Message, state: FSMContext
) -> None:
    if not message.from_user:
        return

    if message.text.strip() == "/cancel":
        await state.clear()
        text, keyboard = await _build_receipt_config_menu_content()
        await message.answer(
            f"❌ ویرایش متن لغو شد.\n\n{text}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    from db.models import set_receipt_config

    new_text = message.text.strip()
    await set_receipt_config(disabled_text=new_text)
    await state.clear()

    text, keyboard = await _build_receipt_config_menu_content()
    await message.answer(
        f"✅ متن پیام غیرفعال بودن رسید با موفقیت به‌روزرسانی شد.\n\n{text}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_reset_receipt_disabled_text")
async def admin_reset_receipt_disabled_text(callback: types.CallbackQuery) -> None:
    if not await _require_permission(callback, "receipt_config"):
        return

    from db.models import DEFAULT_RECEIPT_CONFIG, set_receipt_config
    from utils.helpers import safe_edit_text

    await set_receipt_config(disabled_text=DEFAULT_RECEIPT_CONFIG["disabled_text"])

    text, keyboard = await _build_receipt_config_menu_content()
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer("✅ متن پیام به پیش‌فرض بازنشانی شد.", show_alert=False)


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
                    text="🔙 بازگشت به هشدارها", callback_data="admin_alerts_menu"
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد معتبر به گیگابایت وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر به روز وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر (به دقیقه) وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
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
                    text="🔙 بازگشت به قیمت و مالی", callback_data="admin_pricing_menu"
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
                    text="❌ انصراف و بازگشت", callback_data=f"admin_ip_cancel_{email}"
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
                    text="❌ انصراف و بازگشت", callback_data=f"admin_ip_cancel_{email}"
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        ),
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_alerts",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر (به دقیقه) وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
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


async def _render_invoices_list(
    message: types.Message | types.CallbackQuery,
    status_filter: str,
    page: int,
    search_query: str | None = None,
) -> None:
    from db.models import get_all_invoices_paginated
    from keyboards.inline_kb import admin_invoices_list_keyboard

    page_size = 5
    invoices, total_invoices, total_pages = await get_all_invoices_paginated(
        status_filter=status_filter,
        page=page,
        page_size=page_size,
        search_query=search_query,
    )

    if page >= total_pages and total_pages > 0:
        page = total_pages - 1
        invoices, total_invoices, total_pages = await get_all_invoices_paginated(
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            search_query=search_query,
        )

    status_titles = {
        "all": "همه وضعیت‌ها",
        "approved": "پرداخت‌شده / تأییدشده 🟢",
        "pending": "در انتظار تأیید 🟡",
        "rejected": "رد شده 🔴",
        "expired": "منقضی شده ⌛️",
    }
    status_title = status_titles.get(status_filter, status_filter)

    status_badges = {
        "approved": "🟢 تأییدشده",
        "paid": "🟢 پرداخت‌شده",
        "pending": "🟡 در انتظار",
        "rejected": "🔴 ردشده",
        "expired": "⌛️ منقضی",
    }

    search_badge = (
        f"\n🔍 نتیجه جستجو برای: <b>«{search_query}»</b>" if search_query else ""
    )

    if not invoices:
        if search_query:
            text = (
                f"🧾 <b>مدیریت و آرشیو فاکتورها</b>\n"
                f"🔍 فیلتر وضعیت: <b>{status_title}</b>{search_badge}\n\n"
                f"<i>هیچ فاکتوری مطابق با عبارت «{search_query}» یافت نشد.</i>"
            )
        else:
            text = (
                f"🧾 <b>مدیریت و آرشیو فاکتورها</b>\n"
                f"🔍 فیلتر فعلی: <b>{status_title}</b>\n\n"
                f"<i>هیچ فاکتوری با این وضعیت یافت نشد.</i>"
            )
    else:
        lines = [
            f"🧾 <b>مدیریت و آرشیو فاکتورها</b> (فیلتر: <b>{status_title}</b> | کل: <b>{to_persian_digits(total_invoices)}</b> فاکتور){search_badge}\n"
        ]
        digit_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        for idx, inv in enumerate(invoices):
            emoji = digit_emojis[idx] if idx < len(digit_emojis) else f"{idx + 1}"
            inv_id = inv["id"]
            short_id = inv_id[:8] + "..." if len(inv_id) > 10 else inv_id
            u_name = inv.get("full_name") or "کاربر"
            username = inv.get("username")
            u_str = f"@{username}" if username else "بدون نام‌کاربری"
            amount_str = format_price(inv.get("amount", 0))
            st = inv.get("status", "pending")
            st_badge = status_badges.get(st, st)
            method_str = (
                "کارت به کارت 💳"
                if inv.get("payment_method") == "card"
                else "کیف پول 👛"
            )
            dt_str = (
                format_datetime(inv.get("created_at"))
                if inv.get("created_at")
                else "نامشخص"
            )

            target_email = inv.get("target_email")
            if target_email == "TOPUP" or (
                inv.get("duration_days") == 0 and inv.get("data_gb") == 0
            ):
                service_desc = "💵 شارژ کیف پول"
            elif target_email:
                service_desc = f"🔄 تمدید اشتراک: <code>{target_email}</code>"
            else:
                gb = inv.get("data_gb", 0)
                dur = inv.get("duration_days", 0)
                service_desc = (
                    f"🛍 خرید بسته: {format_size_gb(gb)} | {to_persian_digits(dur)} روز"
                )

            lines.append(
                f"{emoji} <b>فاکتور:</b> <code>{short_id}</code> | {st_badge}\n"
                f"   👤 کاربر: <b>{u_name}</b> ({u_str}) | 🆔 <code>{inv['tg_id']}</code>\n"
                f"   📦 عملیات: {service_desc}\n"
                f"   💰 مبلغ: <b>{amount_str}</b> ({method_str})\n"
                f"   📅 تاریخ ثبت: <code>{dt_str}</code>\n"
            )
        text = "\n".join(lines)

    keyboard = admin_invoices_list_keyboard(
        invoices, status_filter, page, total_pages, search_query=search_query
    )

    if isinstance(message, types.CallbackQuery):
        await safe_edit_text(
            message.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    elif (
        isinstance(message, types.Message)
        and message.from_user
        and not message.from_user.is_bot
    ):
        await message.answer(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        msg_obj = message.message if hasattr(message, "message") else message
        await safe_edit_text(
            msg_obj,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("admin_invoices_"))
async def admin_invoices_list(callback: types.CallbackQuery, state: FSMContext) -> None:
    from db.models import has_admin_permission

    if not (
        await has_admin_permission(callback.from_user.id, "view_invoices")
        or await has_admin_permission(callback.from_user.id, "approve_invoices")
    ):
        await callback.answer(
            "⛔️ شما دسترسی به بخش «مشاهده لیست فاکتورها» را ندارید.",
            show_alert=True,
        )
        return
    await state.set_state(None)

    parts = callback.data.split("_")
    status_filter = parts[2] if len(parts) >= 3 else "all"
    try:
        page = int(parts[3]) if len(parts) >= 4 else 0
    except ValueError:
        page = 0

    data = await state.get_data()
    search_query = data.get("invoice_search_query")
    await state.update_data(
        current_invoice_filter=status_filter, current_invoice_page=page
    )

    await _render_invoices_list(
        callback.message, status_filter, page, search_query=search_query
    )
    await callback.answer()


@router.callback_query(F.data == "admin_inv_search_start")
async def admin_inv_search_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    from db.models import has_admin_permission

    if not (
        await has_admin_permission(callback.from_user.id, "view_invoices")
        or await has_admin_permission(callback.from_user.id, "approve_invoices")
    ):
        await callback.answer(
            "⛔️ شما دسترسی به بخش «مشاهده لیست فاکتورها» را ندارید.",
            show_alert=True,
        )
        return

    data = await state.get_data()
    status_filter = data.get("current_invoice_filter", "all")
    page = data.get("current_invoice_page", 0)

    await state.set_state(AdminControlStates.waiting_invoice_search)

    from keyboards.inline_kb import admin_invoice_search_prompt_keyboard

    text = (
        "🔍 <b>جستجوی پیشرفته و همه‌جانبه در فاکتورها</b>\n\n"
        "لطفاً عبارت مورد نظر خود را جهت جستجو ارسال نمایید.\n\n"
        "💡 <b>جستجو بر روی تمامی موارد زیر انجام می‌شود:</b>\n"
        "• کد فاکتور (مانند: <code>INV...</code>)\n"
        "• شناسه عددی تلگرام کاربر (User ID)\n"
        "• نام کاربری (Username) یا نام خریدار\n"
        "• نام سرویس / ایمیل اشتراک (مانند: <code>user123_...</code>)\n"
        "• متن و شماره پیگیری رسید واریزی\n"
        "• کد تخفیف یا مبلغ پرداختی\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>"
    )

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=admin_invoice_search_prompt_keyboard(status_filter, page),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminControlStates.waiting_invoice_search, F.text)
async def admin_inv_search_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    status_filter = data.get("current_invoice_filter", "all")
    page = data.get("current_invoice_page", 0)

    if message.text and message.text.strip() == "/cancel":
        await state.set_state(None)
        search_query = data.get("invoice_search_query")
        await _render_invoices_list(
            message, status_filter, page, search_query=search_query
        )
        return

    query = message.text.strip() if message.text else ""
    if not query:
        from keyboards.inline_kb import admin_invoice_search_prompt_keyboard

        await message.answer(
            "⚠️ لطفاً یک عبارت معتبر برای جستجو ارسال کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=admin_invoice_search_prompt_keyboard(status_filter, page),
            parse_mode="HTML",
        )
        return

    await state.set_state(None)
    await state.update_data(invoice_search_query=query, current_invoice_page=0)

    await _render_invoices_list(message, status_filter, 0, search_query=query)


@router.callback_query(F.data == "admin_inv_search_clear")
async def admin_inv_search_clear(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(None)
    await state.update_data(invoice_search_query=None, current_invoice_page=0)
    data = await state.get_data()
    status_filter = data.get("current_invoice_filter", "all")

    await _render_invoices_list(callback.message, status_filter, 0, search_query=None)
    await callback.answer("✅ فیلتر جستجو پاک شد و همه فاکتورها نمایش داده شدند.")


async def _render_invoice_details(
    message: types.Message,
    user_id: int,
    inv_id: str,
    status_filter: str,
    page: int,
) -> bool:
    from db.models import get_invoice_details, has_admin_permission
    from keyboards.inline_kb import admin_invoice_detail_keyboard

    inv = await get_invoice_details(inv_id)
    if not inv:
        return False

    status_badges = {
        "approved": "🟢 تأییدشده",
        "paid": "🟢 پرداخت‌شده",
        "pending": "🟡 در انتظار تأیید",
        "rejected": "🔴 ردشده",
        "expired": "⌛️ منقضی‌شده",
    }

    tg_id = inv["tg_id"]
    u_name = inv.get("full_name") or "نامشخص"
    username = inv.get("username")
    u_str = f"@{username}" if username else "بدون نام‌کاربری"
    amount = inv.get("amount", 0)
    orig_amount = inv.get("original_amount") or amount
    discount_code = inv.get("discount_code") or "اعمال نشده"
    st = inv.get("status", "pending")
    st_badge = status_badges.get(st, st)
    method_str = (
        "کارت به کارت 💳" if inv.get("payment_method") == "card" else "کیف پول 👛"
    )
    created_at = (
        format_datetime(inv.get("created_at")) if inv.get("created_at") else "نامشخص"
    )
    expires_at = (
        format_datetime(inv.get("expires_at")) if inv.get("expires_at") else "نامشخص"
    )

    target_email = inv.get("target_email")
    if target_email == "TOPUP" or (
        inv.get("duration_days") == 0 and inv.get("data_gb") == 0
    ):
        type_str = "💵 افزایش موجودی / شارژ کیف پول"
        spec_str = "شارژ مستقیم کیف پول کاربر"
    elif target_email:
        type_str = "🔄 تمدید اشتراک موجود"
        spec_str = (
            f"نام سرویس: <code>{target_email}</code>\n"
            f"   • حجم: {format_size_gb(inv.get('data_gb', 0))} | مدت: {to_persian_digits(inv.get('duration_days', 0))} روز"
        )
    else:
        type_str = "🛍 خرید اشتراک جدید"
        spec_str = (
            f"حجم: {format_size_gb(inv.get('data_gb', 0))} | "
            f"مدت: {to_persian_digits(inv.get('duration_days', 0))} روز | "
            f"تعداد کاربر: {to_persian_digits(inv.get('users_count', 1))} کاربر"
        )

    receipt_text = inv.get("receipt_text")
    receipt_info = ""
    if receipt_text:
        receipt_info += (
            f"\n📝 <b>توضیحات واریز کاربر:</b>\n<code>{receipt_text}</code>\n"
        )
    if inv.get("receipt_file_id"):
        receipt_info += "\n📎 <i>این فاکتور دارای تصویر رسید واریزی است (با دکمه زیر قابل مشاهده است).</i>\n"

    text = (
        f"🧾 <b>جزئیات کامل فاکتور و تراکنش</b>\n\n"
        f"🆔 <b>کد فاکتور:</b> <code>{inv_id}</code>\n"
        f"🔘 <b>وضعیت فعلی:</b> {st_badge}\n"
        f"📌 <b>نوع تراکنش:</b> {type_str}\n\n"
        f"👤 <b>اطلاعات خریدار:</b>\n"
        f"   • نام: <b>{u_name}</b>\n"
        f"   • یوزرنیم: <b>{u_str}</b>\n"
        f"   • شناسه عددی: <code>{tg_id}</code>\n\n"
        f"📦 <b>مشخصات سرویس:</b>\n"
        f"   • {spec_str}\n\n"
        f"💰 <b>اطلاعات مالی:</b>\n"
        f"   • مبلغ اولیه: {format_price(orig_amount)}\n"
        f"   • کد تخفیف: <code>{discount_code}</code>\n"
        f"   • مبلغ نهایی پرداختی: <b>{format_price(amount)}</b>\n"
        f"   • روش پرداخت: <b>{method_str}</b>\n\n"
        f"📅 <b>زمان‌بندی:</b>\n"
        f"   • تاریخ ثبت: <code>{created_at}</code>\n"
        f"   • مهلت پرداخت: <code>{expires_at}</code>"
        f"{receipt_info}"
    )

    can_approve = await has_admin_permission(user_id, "approve_invoices")
    can_reapprove = await has_admin_permission(user_id, "reapprove_invoices")
    can_delete = await has_admin_permission(user_id, "delete_invoices")

    keyboard = admin_invoice_detail_keyboard(
        inv,
        status_filter,
        page,
        can_approve=can_approve,
        can_reapprove=can_reapprove,
        can_delete=can_delete,
    )

    await safe_edit_text(
        message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return True


@router.callback_query(F.data.startswith("admin_inv_view_"))
async def admin_invoice_view(callback: types.CallbackQuery, state: FSMContext) -> None:
    from db.models import has_admin_permission

    if not (
        await has_admin_permission(callback.from_user.id, "view_invoices")
        or await has_admin_permission(callback.from_user.id, "approve_invoices")
        or await has_admin_permission(callback.from_user.id, "reapprove_invoices")
    ):
        await callback.answer(
            "⛔️ شما دسترسی به بخش «مشاهده لیست فاکتورها» را ندارید.",
            show_alert=True,
        )
        return
    await state.clear()

    parts = callback.data.split("_")
    if len(parts) < 6:
        await callback.answer("خطای نامعتبر بودن پارامترها.", show_alert=True)
        return

    inv_id = parts[3]
    status_filter = parts[4]
    try:
        page = int(parts[5])
    except ValueError:
        page = 0

    found = await _render_invoice_details(
        callback.message, callback.from_user.id, inv_id, status_filter, page
    )
    if not found:
        await callback.answer("❌ فاکتور یافت نشد.", show_alert=True)
        return

    await callback.answer()


@router.callback_query(F.data.startswith("admin_inv_receipt_"))
async def admin_invoice_receipt_view(callback: types.CallbackQuery, bot: Bot) -> None:
    from db.models import has_admin_permission

    if not (
        await has_admin_permission(callback.from_user.id, "view_invoices")
        or await has_admin_permission(callback.from_user.id, "approve_invoices")
    ):
        await callback.answer(
            "⛔️ شما دسترسی به بخش «مشاهده لیست فاکتورها» را ندارید.",
            show_alert=True,
        )
        return

    parts = callback.data.split("_")
    if len(parts) < 6:
        await callback.answer("خطای نامعتبر بودن پارامترها.", show_alert=True)
        return

    inv_id = parts[3]

    from db.models import get_invoice_details

    inv = await get_invoice_details(inv_id)
    if not inv or not inv.get("receipt_file_id"):
        await callback.answer(
            "❌ تصویر رسیدی برای این فاکتور یافت نشد.", show_alert=True
        )
        return

    photo_file_id = inv["receipt_file_id"]
    receipt_text = inv.get("receipt_text") or ""
    amount_str = format_price(inv.get("amount", 0))

    caption = (
        f"🧾 <b>تصویر رسید فاکتور:</b> <code>{inv_id}</code>\n"
        f"💰 مبلغ: <b>{amount_str}</b>\n"
        f"👤 کاربر: <code>{inv['tg_id']}</code>"
    )
    if receipt_text:
        caption += f"\n📝 توضیحات: <code>{receipt_text}</code>"

    await bot.send_photo(
        chat_id=callback.from_user.id,
        photo=photo_file_id,
        caption=caption,
        parse_mode="HTML",
    )
    await callback.answer("✅ تصویر فیش برای شما ارسال شد.", show_alert=False)


@router.callback_query(F.data.startswith("admin_inv_del_ask_"))
async def admin_invoice_delete_ask(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "delete_invoices"):
        return
    await state.clear()

    parts = callback.data.split("_")
    if len(parts) < 7:
        await callback.answer("خطای نامعتبر بودن پارامترها.", show_alert=True)
        return

    inv_id = parts[4]
    status_filter = parts[5]
    try:
        page = int(parts[6])
    except ValueError:
        page = 0

    from db.models import get_invoice_details
    from keyboards.inline_kb import admin_invoice_delete_confirm_keyboard

    inv = await get_invoice_details(inv_id)
    if not inv:
        await callback.answer("❌ فاکتور یافت نشد.", show_alert=True)
        return

    amount_str = format_price(inv.get("amount", 0))
    u_name = inv.get("full_name") or "کاربر"

    text = (
        f"🗑 <b>تأیید حذف کامل فاکتور از دیتابیس</b>\n\n"
        f"⚠️ <b>آیا از حذف دائمی این فاکتور اطمینان دارید؟</b>\n\n"
        f"🆔 <b>کد فاکتور:</b> <code>{inv_id}</code>\n"
        f"👤 <b>کاربر:</b> {u_name} (<code>{inv.get('tg_id')}</code>)\n"
        f"💰 <b>مبلغ:</b> {amount_str}\n\n"
        f"🚨 <b>هشدار:</b> این عملیات غیرقابل بازگشت است و تمام رکوردهای این فاکتور برای همیشه پاک خواهد شد."
    )

    keyboard = admin_invoice_delete_confirm_keyboard(inv_id, status_filter, page)
    await safe_edit_text(
        callback.message, text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_inv_del_confirm_"))
async def admin_invoice_delete_confirm(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "delete_invoices"):
        return

    parts = callback.data.split("_")
    if len(parts) < 7:
        await callback.answer("خطای نامعتبر بودن پارامترها.", show_alert=True)
        return

    inv_id = parts[4]
    status_filter = parts[5]
    try:
        page = int(parts[6])
    except ValueError:
        page = 0

    from db.models import delete_invoice

    success = await delete_invoice(inv_id)
    alert_msg = (
        "✅ فاکتور با موفقیت از دیتابیس حذف شد."
        if success
        else "⚠️ فاکتور یافت نشد یا قبلاً حذف شده است."
    )

    await _render_invoices_list(callback.message, status_filter, page)
    await callback.answer(alert_msg, show_alert=True)


@router.callback_query(F.data.startswith("admin_inv_reapprove_ask_"))
async def admin_invoice_reapprove_ask(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "reapprove_invoices"):
        return
    await state.clear()

    parts = callback.data.split("_")
    if len(parts) < 7:
        await callback.answer("خطای نامعتبر بودن پارامترها.", show_alert=True)
        return

    inv_id = parts[4]
    status_filter = parts[5]
    try:
        page = int(parts[6])
    except ValueError:
        page = 0

    from db.models import get_invoice_details
    from keyboards.inline_kb import admin_invoice_reapprove_confirm_keyboard

    inv = await get_invoice_details(inv_id)
    if not inv:
        await callback.answer("❌ فاکتور یافت نشد.", show_alert=True)
        return

    if inv.get("status") != "rejected":
        await callback.answer(
            "❌ این فاکتور در وضعیت ردشده قرار ندارد.", show_alert=True
        )
        return

    amount_str = format_price(inv.get("amount", 0))
    u_name = inv.get("full_name") or "کاربر"

    target_email = inv.get("target_email")
    if target_email == "TOPUP" or (
        inv.get("duration_days") == 0 and inv.get("data_gb") == 0
    ):
        op_desc = f"شارژ مستقیم کیف پول کاربر به مبلغ {amount_str}"
    elif target_email:
        op_desc = (
            f"تمدید اشتراک <code>{target_email}</code> "
            f"({to_persian_digits(inv.get('duration_days', 0))} روز | {format_size_gb(inv.get('data_gb', 0))})"
        )
    else:
        op_desc = (
            f"ساخت اشتراک جدید "
            f"({to_persian_digits(inv.get('duration_days', 0))} روز | {format_size_gb(inv.get('data_gb', 0))})"
        )

    text = (
        f"🔄 <b>بازبینی و تأیید مجدد فاکتور رد شده</b>\n\n"
        f"آیا از تأیید مجدد این فاکتور و فعال‌سازی سرویس اطمینان دارید؟\n\n"
        f"🆔 <b>کد فاکتور:</b> <code>{inv_id}</code>\n"
        f"👤 <b>کاربر:</b> {u_name} (<code>{inv.get('tg_id')}</code>)\n"
        f"💰 <b>مبلغ پرداختی:</b> {amount_str}\n"
        f"⚙️ <b>عملیات اجرایی:</b> {op_desc}\n\n"
        f"💡 <i>پس از تأیید، وضعیت فاکتور به «تأییدشده» تغییر کرده و پیام فعال‌سازی مجدد به همراه اطلاعات اشتراک برای کاربر ارسال خواهد شد.</i>"
    )

    keyboard = admin_invoice_reapprove_confirm_keyboard(inv_id, status_filter, page)
    await safe_edit_text(
        callback.message, text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_inv_reapprove_confirm_"))
async def admin_invoice_reapprove_confirm(
    callback: types.CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not await _require_permission(callback, "reapprove_invoices"):
        return
    await state.clear()

    parts = callback.data.split("_")
    if len(parts) < 7:
        await callback.answer("خطای نامعتبر بودن پارامترها.", show_alert=True)
        return

    inv_id = parts[4]
    status_filter = parts[5]
    try:
        page = int(parts[6])
    except ValueError:
        page = 0

    from services.invoice_service import approve_invoice

    success, msg, _ = await approve_invoice(inv_id, bot, is_reapproval=True)
    if not success:
        await callback.answer(f"❌ {msg}", show_alert=True)
        return

    import html

    admin_name = html.escape(
        callback.from_user.full_name
        or (f"@{callback.from_user.username}" if callback.from_user.username else "")
        or str(callback.from_user.id)
    )
    admin_mention = f'<a href="tg://user?id={callback.from_user.id}">{admin_name}</a>'

    if status_filter == "notif":
        from handlers.admin import render_admin_notification_view

        await render_admin_notification_view(
            callback.message,
            inv_id,
            callback.from_user.id,
            status_note=f"✅ <b>تأیید شد پس از بازبینی توسط {admin_mention} — {msg}</b>",
        )
    else:
        await _render_invoice_details(
            callback.message, callback.from_user.id, inv_id, status_filter, page
        )
    await callback.answer("✅ فاکتور با موفقیت تأیید و فعال شد.", show_alert=True)


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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_cancel_to_users"
                    )
                ]
            ]
        ),
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
            err_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ انصراف و بازگشت",
                            callback_data="admin_cancel_to_users",
                        )
                    ]
                ]
            )
            await message.answer(
                "⚠️ لطفاً یک شناسه عددی معتبر تلگرام یا پیام فورواردی ارسال کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=err_kb,
                parse_mode="HTML",
            )
            return

    if new_tg_id <= 0:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data="admin_cancel_to_users",
                    )
                ]
            ]
        )
        await message.answer(
            "⚠️ شناسه تلگرام وارد شده معتبر نیست.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
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
    from services.xui_api import get_online_clients, get_server_status

    stats = await get_bot_statistics()
    server_status = await get_server_status()
    online_clients = await get_online_clients()
    online_count = len(online_clients)

    net_traffic = server_status.get("netTraffic") or {}
    sent_bytes = net_traffic.get("sent", 0) if isinstance(net_traffic, dict) else 0
    recv_bytes = net_traffic.get("recv", 0) if isinstance(net_traffic, dict) else 0
    total_traffic_bytes = sent_bytes + recv_bytes

    traffic_text = ""
    if sent_bytes > 0 or recv_bytes > 0:
        traffic_text = (
            f"  • 📥 دانلود کل سرور (Recv): <b>{format_size(recv_bytes)}</b>\n"
            f"  • 📤 آپلود کل سرور (Sent): <b>{format_size(sent_bytes)}</b>\n"
            f"  • 🌐 ترافیک کل مصرفی: <b>{format_size(total_traffic_bytes)}</b>\n"
        )

    text = (
        f"📊 <b>آمار و گزارشات جامع ربات</b>\n\n"
        f"👥 <b>آمار کاربران</b>\n"
        f"  • 🟢 کاربران آنلاین هم‌اکنون: <b>{to_persian_digits(online_count)}</b> نفر\n"
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
        f"{traffic_text}"
        f"  • اینباندهای فعال اختصاصی: <b>{to_persian_digits(stats['active_inbounds_count'])}</b> عدد\n"
        f"  • اشتراک‌های مسدود (تخطی IP): <b>{to_persian_digits(stats['suspended_count'])}</b> عدد\n"
        f"  • تعداد ادمین‌ها: <b>{to_persian_digits(stats['admins_count'])}</b> نفر\n\n"
        f"💡 <i>اطلاعات فوق به صورت زنده از دیتابیس ربات محاسبه شده‌اند.</i>"
    )

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=admin_stats_keyboard(online_count=online_count),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_online_clients_"))
async def admin_online_clients_list(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "stats"):
        return

    page_str = callback.data[len("admin_online_clients_") :]
    try:
        page = int(page_str)
    except ValueError:
        page = 0

    import asyncio
    import math
    from db.models import get_user
    from keyboards.inline_kb import admin_online_clients_keyboard
    from services.xui_api import get_client, get_client_traffic, get_online_clients

    online_emails = await get_online_clients()
    total_onlines = len(online_emails)

    if total_onlines == 0:
        empty_text = (
            "🟢 <b>کاربران آنلاین سرور</b>\n\n"
            "<i>در حال حاضر هیچ کاربری به سرور متصل نیست.</i>"
        )
        empty_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 بروزرسانی",
                        callback_data="admin_online_clients_0",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 بازگشت به آمار",
                        callback_data="admin_stats_menu",
                    )
                ],
            ]
        )
        await safe_edit_text(
            callback.message,
            empty_text,
            reply_markup=empty_kb,
            parse_mode="HTML",
        )
        await callback.answer()
        return

    page_size = 5
    total_pages = max(1, math.ceil(total_onlines / page_size))
    page = max(0, min(page, total_pages - 1))

    start_idx = page * page_size
    page_emails = online_emails[start_idx : start_idx + page_size]

    await state.update_data(sub_back_callback=f"admin_online_clients_{page}")

    lines = [
        f"🟢 <b>لیست کاربران آنلاین سرور</b> ({to_persian_digits(total_onlines)} نفر آنلاین)\n",
        f"<i>صفحه {to_persian_digits(page + 1)} از {to_persian_digits(total_pages)}</i>\n",
    ]

    async def _fetch_client_and_traffic(email: str):
        c_res, tr_res = await asyncio.gather(
            get_client(email),
            get_client_traffic(email),
            return_exceptions=True,
        )
        c_dict = c_res if isinstance(c_res, dict) else None
        tr_dict = tr_res if isinstance(tr_res, dict) else None
        return email, c_dict, tr_dict

    fetched_items = await asyncio.gather(
        *[_fetch_client_and_traffic(em) for em in page_emails]
    )

    clients_page_data = []
    for idx, (email, client, traffic) in enumerate(fetched_items, start=start_idx + 1):
        clients_page_data.append(client or {"email": email})

        total_bytes = 0
        if client:
            total_bytes = client.get("totalGB", 0) or 0
        if not total_bytes and traffic:
            total_bytes = traffic.get("total", 0) or 0

        used_bytes = 0
        if traffic:
            used_bytes = (traffic.get("up", 0) or 0) + (traffic.get("down", 0) or 0)
        elif client and "usedTraffic" in client:
            used_bytes = client.get("usedTraffic", 0) or 0

        used_str = format_size(used_bytes)
        total_str = format_size(total_bytes) if total_bytes > 0 else "نامحدود"

        if total_bytes > 0:
            rem_bytes = max(0, total_bytes - used_bytes)
            vol_line = f"   📊 مصرف: <b>{used_str}</b> از <b>{total_str}</b> (🔋 باقیمانده: <b>{format_size(rem_bytes)}</b>)"
        else:
            vol_line = f"   📊 مصرف: <b>{used_str}</b> (سقف: <b>نامحدود</b>)"

        tg_id = client.get("tgId", 0) or 0 if client else 0
        tg_info = ""
        if tg_id > 0:
            u = await get_user(tg_id)
            if u:
                uname = (
                    f"@{u['username']}"
                    if u.get("username")
                    else (u.get("full_name") or "")
                )
                tg_info = f"\n   👤 کاربر: <code>{tg_id}</code> ({uname})"
            else:
                tg_info = f"\n   👤 کاربر: <code>{tg_id}</code>"

        grp = client.get("group") if client else ""
        grp_str = f" | گروه: <code>{grp}</code>" if grp else ""

        lines.append(
            f"<b>{to_persian_digits(idx)}.</b> <code>{email}</code>{grp_str}\n"
            f"{vol_line}{tg_info}\n"
        )

    lines.append(
        "<i>جهت مشاهده مشخصات کامل و مدیریت هر اشتراک، روی دکمه آن کلیک کنید:</i>"
    )
    text = "\n".join(lines)

    kb = admin_online_clients_keyboard(clients_page_data, page, total_pages)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
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
        joined_at_raw = u.get("joined_at")
        joined_at = format_datetime(joined_at_raw) if joined_at_raw else "نامشخص"
        test_used_val = u.get("test_used", 0)
        test_badge = (
            f"🎁 تست: دارد ({to_persian_digits(test_used_val)})"
            if test_used_val > 0
            else "🎁 تست: ندارد"
        )

        lines.append(
            f"{to_persian_digits(idx)}️⃣ <b>{full_name}</b> ({username_str})\n"
            f"   🆔 <code>{tg_id}</code> | 💰 کیف پول: {bal_str}\n"
            f"   💳 کل پرداختی‌ها: <b>{total_paid_str}</b> ({to_persian_digits(paid_count)} موفق) | {test_badge}\n"
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
                text="🔙 بازگشت به تنظیمات", callback_data="admin_settings_menu"
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_start_msg_menu"
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
        [
            InlineKeyboardButton(
                text="❌ انصراف و بازگشت", callback_data="admin_start_msg_menu"
            )
        ]
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
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_start_msg_menu"
                )
            ]
        ]
    )
    if callback.message:
        await callback.message.answer(
            "🔘 <b>افزودن دکمه شیشه‌ای جدید</b>\n\n"
            "لطفاً <b>عنوان (متن روی دکمه)</b> را ارسال کنید:\n"
            "مثال: <code>کانال اطلاع‌رسانی</code>\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=cancel_kb,
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

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_start_msg_menu"
                )
            ]
        ]
    )
    await message.answer(
        f"🔗 عنوان دکمه: <b>{title}</b>\n\n"
        "حالا لطفاً <b>لینک دکمه (URL)</b> را ارسال کنید:\n"
        "مثال: <code>https://t.me/your_channel</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
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
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="admin_start_msg_menu"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لینک نامعتبر است. لینک باید با <code>https://</code> یا <code>t.me/</code> شروع شود.\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
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
        [
            InlineKeyboardButton(
                text="❌ انصراف و بازگشت", callback_data="admin_start_msg_menu"
            )
        ],
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
                        text="❌ انصراف و بازگشت", callback_data="admin_start_msg_menu"
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
                text="🔙 بازگشت به قیمت و مالی", callback_data="admin_pricing_menu"
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_pricing_disp_menu"
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
                            text="❌ انصراف و بازگشت",
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
                            text="❌ انصراف و بازگشت",
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
                        text="❌ انصراف و بازگشت",
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
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data="admin_pricing_disp_menu",
                )
            ]
        ]
    )
    if callback.message:
        await callback.message.answer(
            "✍️ لطفاً <b>متن (کپشن) دلخواه</b> برای تصویر را ارسال کنید:\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=cancel_kb,
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
                    text="❌ انصراف و بازگشت",
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
                    text="🔙 بازگشت به تنظیمات",
                    callback_data="admin_settings_menu",
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_channel_lock_menu"
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
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="admin_channel_lock_menu"
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
