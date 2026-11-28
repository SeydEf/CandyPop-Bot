from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import SUB_BASE_URL
from db.models import (
    credit_wallet,
    get_active_client_group,
    get_active_inbound_ids,
    get_balance,
    get_user,
    search_users,
)
from services import xui_api
from services.pricing import format_volume_label, is_unlimited_volume
from utils.formatting import (
    format_datetime,
    format_detailed_traffic,
    format_price,
    format_size_gb,
    format_size_persian_gb,
    persian_to_english_digits,
    to_persian_digits,
)
from utils.helpers import gb_to_bytes, generate_email, generate_qr, safe_edit_text

logger = logging.getLogger(__name__)
router = Router(name="admin_sub_manage")


async def _is_admin(event: types.CallbackQuery | types.Message) -> bool:
    from db.models import has_admin_permission

    if event.from_user is None:
        return False
    permitted = await has_admin_permission(event.from_user.id, "manage_subs")
    if not permitted:
        msg = "⛔️ شما دسترسی به بخش «جستجو و مدیریت اشتراک‌ها» را ندارید."
        if isinstance(event, types.CallbackQuery):
            await event.answer(msg, show_alert=True)
        else:
            await event.answer(msg)
        return False
    return True


class AdminSearchStates(StatesGroup):
    waiting_search_query = State()
    waiting_add_gb = State()
    waiting_add_days = State()
    waiting_limit_ip = State()
    waiting_rename_email = State()
    waiting_user_wallet = State()
    waiting_create_sub_gb = State()
    waiting_create_sub_dur = State()
    waiting_ban_custom_msg = State()
    waiting_unban_custom_msg = State()
    waiting_user_direct_msg = State()
    waiting_sub_custom_group = State()
    waiting_sub_tgid = State()
    waiting_res_gb = State()
    waiting_res_dur = State()
    waiting_res_users = State()
    waiting_res_edit_val = State()
    waiting_cdate_range = State()


@router.message(Command("search", "find", "find_user"))
@router.callback_query(F.data == "admin_search_start")
async def admin_search_start(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(event):
        return

    await state.set_state(AdminSearchStates.waiting_search_query)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data="admin_sub_users_menu",
                )
            ]
        ]
    )

    text = (
        "🔍 <b>جستجوی کاربر و مدیریت اشتراک‌ها</b>\n\n"
        "لطفاً عبارتی برای جستجو وارد کنید:\n"
        "• <b>آیدی عددی تلگرام:</b> <code>123456789</code>\n"
        "• <b>نام کاربری (Username):</b> <code>@username</code>\n"
        "• <b>نام سرویس یا ایمیل اشتراک:</b> <code>user_123456</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>"
    )

    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=cancel_kb, parse_mode="HTML")
    else:
        await safe_edit_text(
            event.message,
            text,
            reply_markup=cancel_kb,
            parse_mode="HTML",
        )
        await event.answer()


@router.message(AdminSearchStates.waiting_search_query, F.text)
async def admin_search_process(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات جستجو لغو شد.")
        return

    query = message.text.strip()
    await state.update_data(last_search_query=query)

    await _perform_search_and_render(message, state, query)


async def _perform_search_and_render(
    event: types.Message | types.CallbackQuery, state: FSMContext, query: str
) -> None:
    db_users = await search_users(query)
    xui_clients = await xui_api.search_clients_all(query)

    if not db_users and not xui_clients:
        msg_text = (
            f"🔍 <b>نتایج جستجو برای:</b> <code>{query}</code>\n\n"
            f"❌ هیچ کاربر یا اشتراکی با این مشخصات یافت نشد."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 جستجوی مجدد", callback_data="admin_search_start"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 بازگشت به مدیریت کاربران",
                        callback_data="admin_sub_users_menu",
                    )
                ],
            ]
        )
        if isinstance(event, types.Message):
            await event.answer(msg_text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await safe_edit_text(
                event.message,
                msg_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        return

    lines = [f"🔍 <b>نتایج جستجو برای:</b> <code>{query}</code>\n"]
    keyboard_rows = []

    if db_users:
        lines.append("👤 <b>کاربران یافت‌شده در ربات:</b>")
        for u in db_users[:5]:
            u_id = u["tg_id"]
            u_name = u.get("username") or u.get("full_name") or "بدون نام"
            bal = await get_balance(u_id)
            lines.append(
                f"• <b>{u_name}</b> (<code>{u_id}</code>) | موجودی: {format_price(bal)}"
            )
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"👤 مدیریت کاربر {u_id}",
                        callback_data=f"admin_manage_user_{u_id}_search",
                    )
                ]
            )
        lines.append("")

    if xui_clients:
        lines.append("📋 <b>اشتراک‌های یافت‌شده در پنل:</b>")
        for c in xui_clients[:10]:
            email = c.get("email", "نامشخص")
            enable = c.get("enable", True)
            status_icon = "🟢" if enable else "🔴"
            c_total_bytes = c.get("totalGB") or 0
            c_total_gb = c_total_bytes / (1024**3)
            c_gb_int = int(round(c_total_gb))
            c_is_unlimited = (c_total_bytes == 0) or (
                await is_unlimited_volume(c_gb_int)
            )
            if c_is_unlimited:
                if c_total_bytes == 0:
                    gb_badge = "♾️ نامحدود"
                else:
                    gb_badge = f"♾️ نامحدود ({to_persian_digits(c_gb_int)} گیگ)"
            else:
                gb_badge = format_size_gb(c_total_gb)
            lines.append(f"• {status_icon} <b>{email}</b> ({gb_badge})")
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"⚙️ مدیریت اشتراک {email}",
                        callback_data=f"admin_manage_sub_{email}",
                    )
                ]
            )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔄 جستجوی مجدد", callback_data="admin_search_start"
            )
        ]
    )
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به مدیریت کاربران", callback_data="admin_sub_users_menu"
            )
        ]
    )

    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await safe_edit_text(
            event.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


def _format_ts_and_relative(
    ts_ms: int | None,
    is_online_check: bool = False,
    default_text: str = "ثبت نشده ⚪️",
) -> str:
    if not ts_ms or ts_ms <= 0:
        return f"<i>{default_text}</i>"

    now_ms = int(time.time() * 1000)
    diff_sec = max(0, int((now_ms - ts_ms) / 1000))

    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    dt_str = format_datetime(dt.isoformat())

    if is_online_check and diff_sec <= 180:
        return "<b>🟢 آنلاین (هم‌اکنون)</b>"

    if diff_sec < 60:
        rel_str = "لحظاتی پیش"
    elif diff_sec < 3600:
        mins = max(1, diff_sec // 60)
        rel_str = f"{to_persian_digits(mins)} دقیقه پیش"
    elif diff_sec < 86400:
        hrs = diff_sec // 3600
        rel_str = f"{to_persian_digits(hrs)} ساعت پیش"
    else:
        days = diff_sec // 86400
        rel_str = f"{to_persian_digits(days)} روز پیش"

    return f"<code>{dt_str}</code> ({rel_str})"


async def _render_sub_dashboard(
    event: types.CallbackQuery | types.Message,
    email: str,
    state: FSMContext,
    notice: str = "",
) -> None:
    client = await xui_api.get_client(email)

    if not client:
        err_msg = "❌ اشتراک یافت نشد یا حذف شده است."
        if isinstance(event, types.CallbackQuery):
            await event.answer(err_msg, show_alert=True)
        else:
            await event.answer(err_msg)
        return

    await state.update_data(manage_email=email)

    traffic = await xui_api.get_client_traffic(email)
    up_bytes = traffic.get("up", 0) if traffic else 0
    down_bytes = traffic.get("down", 0) if traffic else 0
    used_bytes = up_bytes + down_bytes

    last_online_ms = traffic.get("lastOnline", 0) if traffic else 0
    last_sub_fetch_ms = traffic.get("lastSubFetch", 0) if traffic else 0

    last_online_str = _format_ts_and_relative(
        last_online_ms, is_online_check=True, default_text="هرگز متصل نشده ⚪️"
    )
    last_sub_fetch_str = _format_ts_and_relative(
        last_sub_fetch_ms, is_online_check=False, default_text="هرگز دریافت نشده ⚪️"
    )

    total_bytes = client.get("totalGB", 0)
    total_gb_int = int(round(total_bytes / (1024**3))) if total_bytes > 0 else 0
    is_unlimited = (total_bytes == 0) or (await is_unlimited_volume(total_gb_int))

    if is_unlimited:
        if total_bytes > 0:
            total_str = f"♾️ نامحدود (سقف منصفانه {format_size_persian_gb(total_bytes)})"
            rem_str = f"{format_detailed_traffic(max(0, total_bytes - used_bytes))} (از سقف منصفانه)"
        else:
            total_str = "♾️ نامحدود"
            rem_str = "♾️ نامحدود"
    else:
        total_str = (
            format_size_persian_gb(total_bytes) if total_bytes > 0 else "نامحدود"
        )
        rem_str = (
            format_detailed_traffic(max(0, total_bytes - used_bytes))
            if total_bytes > 0
            else "نامحدود"
        )
    used_str = format_detailed_traffic(used_bytes)

    expiry_ms = client.get("expiryTime", 0)
    now_ms = int(time.time() * 1000)

    if expiry_ms > 0:
        expiry_dt = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)
        expiry_str = format_datetime(expiry_dt.isoformat())
        rem_days = max(0, int((expiry_ms - now_ms) / (86400 * 1000)))
        time_str = (
            f"{expiry_str} ({to_persian_digits(rem_days)} روز مانده)"
            if rem_days > 0
            else f"{expiry_str} (منقضی شده)"
        )
    elif expiry_ms < 0:
        initial_days = abs(expiry_ms) // (86400 * 1000)
        time_str = f"⏳ در انتظار اولین اتصال ({to_persian_digits(initial_days)} روز اعتبار پس از اولین اتصال)"
    else:
        time_str = "بدون محدودیت زمانی"

    is_enabled = client.get("enable", True)
    status_str = "🟢 فعال" if is_enabled else "🔴 غیرفعال"
    limit_ip = client.get("limitIp", 0)
    limit_ip_str = f"{to_persian_digits(limit_ip)} کاربر" if limit_ip > 0 else "نامحدود"
    group_name = client.get("group", "") or "بدون گروه"
    tg_id_val = client.get("tgId", 0) or 0
    try:
        tg_id_int = int(tg_id_val)
    except (ValueError, TypeError):
        tg_id_int = 0
    tg_id_display = (
        f"<code>{tg_id_int}</code>" if tg_id_int > 0 else "نامشخص (بدون کاربر)"
    )
    sub_id = client.get("subId", "")
    sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

    notice_block = f"{notice}\n\n" if notice else ""
    plan_type_line = "📦 <b>نوع اشتراک:</b> ♾️ نامحدود\n" if is_unlimited else ""

    text = (
        f"{notice_block}"
        f"⚙️ <b>مدیریت اشتراک:</b> <code>{email}</code>\n\n"
        f"🔘 <b>وضعیت:</b> {status_str}\n"
        f"{plan_type_line}"
        f"👥 <b>سقف کاربر (IP):</b> {limit_ip_str}\n"
        f"🏷 <b>گروه مشتری:</b> <code>{group_name}</code>\n"
        f"🆔 <b>آیدی تلگرام:</b> {tg_id_display}\n\n"
        f"📡 <b>وضعیت اتصال و آنلاین:</b>\n"
        f"   • 🌐 آخرین اتصال: {last_online_str}\n"
        f"   • 🔄 آخرین دریافت ساب: {last_sub_fetch_str}\n\n"
        f"📊 <b>حجم کل:</b> {total_str}\n"
        f"📉 <b>حجم مصرفی:</b> {used_str}\n"
        f"🔋 <b>حجم باقیمانده:</b> {rem_str}\n\n"
        f"⏱ <b>تاریخ انقضا:</b> {time_str}\n\n"
        f"🔗 <b>لینک ساب‌اسکریپشن:</b>\n<code>{sub_link}</code>"
    )

    from db.models import get_reserved_renewal

    res_rec = await get_reserved_renewal(email)
    if res_rec:
        res_dur = res_rec.get("duration_days", 0)
        res_gb = res_rec.get("data_gb", 0)
        res_users = res_rec.get("users_count", 1)
        res_inv = res_rec.get("invoice_id")
        inv_str = f" (فاکتور: <code>{res_inv}</code>)" if res_inv else ""
        res_vol_str = await format_volume_label(res_gb, with_fair_policy=True)
        text += (
            f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>بسته تمدید رزرو شده:</b>\n"
            f"   • ⏱ مدت زمان: {res_dur} روز\n"
            f"   • 📊 حجم ترافیک: {res_vol_str}\n"
            f"   • 👥 سقف کاربر: {to_persian_digits(res_users)} کاربر{inv_str}\n"
            f"   • 💡 <i>این بسته پس از انقضای فعلی به صورت خودکار فعال می‌شود.</i>"
        )

    toggle_btn_text = "🔴 غیرفعال‌سازی سرویس" if is_enabled else "🟢 فعال‌سازی سرویس"

    data = await state.get_data()
    sub_back = data.get("sub_back_callback")
    if sub_back:
        if "admin_user_subs_" in sub_back:
            back_btn_text = "🔙 بازگشت به اشتراک‌های کاربر"
        elif "admin_online_clients_" in sub_back:
            back_btn_text = "🔙 بازگشت به لیست آنلاین‌ها"
        elif sub_back.startswith("admin_cdate_page_"):
            back_btn_text = "🔙 بازگشت به لیست تاریخ ساخت"
        else:
            back_btn_text = "🔙 بازگشت به مدیریت کاربر"
        back_btn_callback = sub_back
    else:
        back_btn_text = "🔙 بازگشت به جستجو"
        back_btn_callback = "admin_search_back"

    rows = [
        [
            InlineKeyboardButton(
                text="📊 افزودن / تغییر حجم",
                callback_data=f"admin_sub_gb_menu_{email}",
            ),
            InlineKeyboardButton(
                text="⏱ تمدید / تغییر زمان",
                callback_data=f"admin_sub_days_menu_{email}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="👥 سقف کاربر (IP)",
                callback_data=f"admin_sub_ip_menu_{email}",
            ),
            InlineKeyboardButton(
                text="✏️ تغییر نام (Email)",
                callback_data=f"admin_sub_rename_menu_{email}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🏷 تغییر گروه",
                callback_data=f"admin_sub_group_menu_{email}",
            ),
            InlineKeyboardButton(
                text="👤 تغییر کاربر (Telegram ID)",
                callback_data=f"admin_sub_tgid_menu_{email}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=toggle_btn_text,
                callback_data=f"admin_sub_toggle_enable_{email}",
            ),
            InlineKeyboardButton(
                text="🔄 صفر کردن مصرف",
                callback_data=f"admin_sub_reset_traffic_{email}",
            ),
        ],
    ]

    if res_rec:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⚡️ فعال‌سازی آنی بسته رزرو",
                    callback_data=f"admin_sub_res_act_{email}",
                ),
                InlineKeyboardButton(
                    text="❌ حذف بسته رزرو",
                    callback_data=f"admin_sub_res_del_{email}",
                ),
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="🔗 بارکد (QR) و ارسال لینک",
                    callback_data=f"admin_sub_qr_{email}",
                ),
                InlineKeyboardButton(
                    text="🗑 حذف کامل اشتراک",
                    callback_data=f"admin_sub_del_ask_{email}",
                ),
            ],
            [InlineKeyboardButton(text=back_btn_text, callback_data=back_btn_callback)],
        ]
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

    if isinstance(event, types.CallbackQuery):
        await safe_edit_text(
            event.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await event.answer()
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def _render_user_dashboard(
    event: types.CallbackQuery | types.Message,
    tg_id: int,
    state: FSMContext,
    notice: str = "",
) -> None:
    user = await get_user(tg_id)

    if not user:
        err_msg = "❌ کاربر در دیتابیس یافت نشد."
        if isinstance(event, types.CallbackQuery):
            await event.answer(err_msg, show_alert=True)
        else:
            await event.answer(err_msg)
        return

    await state.update_data(manage_user_id=tg_id)

    bal = await get_balance(tg_id)
    username = user.get("username")
    username_str = f"@{username}" if username else "نامشخص"
    full_name = user.get("full_name") or "نامشخص"
    referrer = user.get("referrer_id") or "بدون معرفی‌کننده"

    from db.models import get_user_financial_summary

    fin_summary = await get_user_financial_summary(tg_id)
    total_paid_str = format_price(fin_summary["total_paid"])
    paid_count = fin_summary["paid_count"]
    topups_str = format_price(fin_summary["topups_amount"])
    subs_str = format_price(fin_summary["subs_amount"])

    test_used_count = user.get("test_used", 0)
    last_test_at_raw = user.get("last_test_at")
    if test_used_count > 0 or last_test_at_raw:
        last_test_str = (
            format_datetime(last_test_at_raw) if last_test_at_raw else "نامشخص"
        )
        test_status_str = f"✅ دریافت کرده ({to_persian_digits(test_used_count)} بار | آخرین بار: <code>{last_test_str}</code>)"
    else:
        test_status_str = "❌ دریافت نکرده (مجاز به دریافت)"

    is_banned = bool(user.get("is_banned", 0))
    ban_status_str = "⛔️ مسدود شده" if is_banned else "🟢 مجاز (فعال)"

    clients = await xui_api.get_normalized_clients_by_tg_id(tg_id)
    subs_count = len(clients)
    subs_count_str = (
        f"{to_persian_digits(subs_count)} اشتراک" if subs_count > 0 else "بدون اشتراک"
    )

    notice_block = f"{notice}\n\n" if notice else ""

    text = (
        f"{notice_block}"
        f"👤 <b>مدیریت کاربر:</b> <code>{tg_id}</code>\n\n"
        f"نام و نام‌خانوادگی: <b>{full_name}</b>\n"
        f"یوزرنیم: <b>{username_str}</b>\n"
        f"🚫 <b>وضعیت دسترسی:</b> <b>{ban_status_str}</b>\n"
        f"📱 <b>اشتراک‌های سرور:</b> <b>{subs_count_str}</b>\n"
        f"🎁 <b>وضعیت اشتراک تست:</b> {test_status_str}\n"
        f"💰 <b>موجودی کیف پول:</b> {format_price(bal)}\n"
        f"💳 <b>کل پرداختی‌های موفق:</b> {total_paid_str} ({to_persian_digits(paid_count)} تراکنش)\n"
        f"   ├ 🛍 خرید/تمدید مستقیم: {subs_str}\n"
        f"   └ 💵 شارژ کیف پول: {topups_str}\n"
        f"👥 <b>معرفی‌کننده:</b> <code>{referrer}</code>"
    )

    data = await state.get_data()
    inv_back = data.get("invoice_back_callback")
    last_query = data.get("last_search_query")
    list_page = data.get("current_list_page")

    if inv_back:
        back_btn_text = "🔙 بازگشت به جزئیات فاکتور"
        back_btn_callback = inv_back
    elif last_query:
        back_btn_text = "🔙 بازگشت به جستجو"
        back_btn_callback = "admin_search_back"
    else:
        back_btn_text = "🔙 بازگشت به لیست کاربران"
        page_num = list_page if list_page is not None else 0
        back_btn_callback = f"admin_users_list_{page_num}"

    from db.models import has_admin_permission

    admin_id = event.from_user.id if event.from_user else 0
    can_ban = await has_admin_permission(admin_id, "ban_users")
    can_msg = await has_admin_permission(admin_id, "send_user_message")

    user_action_rows = [
        [
            InlineKeyboardButton(
                text=f"📱 اشتراک‌های کاربر ({to_persian_digits(subs_count)} اشتراک)",
                callback_data=f"admin_user_subs_{tg_id}_0",
            )
        ],
        [
            InlineKeyboardButton(
                text="➕ ساخت اشتراک جدید برای این کاربر",
                callback_data=f"admin_user_create_sub_{tg_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 شارژ / تغییر موجودی کیف پول",
                callback_data=f"admin_user_wallet_{tg_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="📑 سابقه تراکنش‌های پرداختی کاربر",
                callback_data=f"admin_user_invoices_{tg_id}_0",
            )
        ],
        [
            InlineKeyboardButton(
                text="📦 مدیریت و لاگ رزروها",
                callback_data=f"admin_user_res_logs_{tg_id}_0",
            )
        ],
        [
            InlineKeyboardButton(
                text="♻️ بازنشانی امکان اشتراک تست کاربر",
                callback_data=f"admin_user_reset_test_{tg_id}",
            )
        ],
    ]

    if can_msg:
        user_action_rows.append(
            [
                InlineKeyboardButton(
                    text="✉️ ارسال پیام به این کاربر",
                    callback_data=f"admin_user_msg_{tg_id}",
                )
            ]
        )

    if can_ban:
        if is_banned:
            user_action_rows.append(
                [
                    InlineKeyboardButton(
                        text="✅ رفع مسدودی کاربر",
                        callback_data=f"admin_user_ban_{tg_id}",
                    )
                ]
            )
        else:
            user_action_rows.append(
                [
                    InlineKeyboardButton(
                        text="🚫 مسدود کردن کاربر",
                        callback_data=f"admin_user_ban_{tg_id}",
                    )
                ]
            )

    user_action_rows.append(
        [InlineKeyboardButton(text=back_btn_text, callback_data=back_btn_callback)]
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=user_action_rows)

    if isinstance(event, types.CallbackQuery):
        await safe_edit_text(
            event.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await event.answer()
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def _clear_fsm_keep_nav(state: FSMContext) -> None:
    data = await state.get_data()
    nav_keys = (
        "invoice_back_callback",
        "sub_back_callback",
        "user_subs_emails",
        "current_list_page",
        "last_search_query",
        "manage_user_id",
        "cdate_filter",
        "cdate_emails",
    )
    nav_data = {k: data[k] for k in nav_keys if k in data}
    await state.clear()
    if nav_data:
        await state.set_data(nav_data)


@router.callback_query(F.data.startswith("admin_manage_sub_"))
async def admin_manage_sub_dashboard(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    await _clear_fsm_keep_nav(state)
    email = callback.data[len("admin_manage_sub_") :]
    await _render_sub_dashboard(callback, email, state)


@router.callback_query(F.data.startswith("admin_sub_gb_menu_"))
async def admin_sub_gb_prompt(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_gb_menu_") :]
    await state.update_data(manage_email=email)
    await state.set_state(AdminSearchStates.waiting_add_gb)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_manage_sub_{email}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"📊 <b>افزایش یا تنظیم حجم جدید برای اشتراک «{email}»:</b>\n\n"
        "حجم اضافه یا حجم جدید را به گیگابایت وارد کنید:\n"
        "• برای افزودن حجم به اشتراک فعلی، عدد گیگابایت را وارد کنید (مثال: <code>20</code>)\n"
        "• برای ست کردن حجم مشخص، عبارت <code>set:50</code> را ارسال کنید.\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_add_gb, F.text)
async def admin_sub_gb_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("manage_email")

    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if email:
            await _render_sub_dashboard(
                message, email, state, notice="❌ تغییر حجم لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not email:
        await state.clear()
        return

    client = await xui_api.get_client(email)
    if not client:
        await state.clear()
        await message.answer("❌ اشتراک یافت نشد.")
        return

    txt = message.text.strip()
    is_set = False
    if txt.lower().startswith("set:"):
        is_set = True
        txt = txt[4:].strip()

    try:
        gb_val = float(persian_to_english_digits(txt))
        if gb_val <= 0:
            raise ValueError
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_manage_sub_{email}"
                        if email
                        else "admin_sub_users_menu",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد معتبر وارد کنید (مثال: 20 یا set:50).\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    current_total_gb = (client.get("totalGB") or 0) / (1024**3)
    new_total_gb = gb_val if is_set else (current_total_gb + gb_val)
    new_total_bytes = gb_to_bytes(new_total_gb)

    update_data = dict(client)
    update_data["totalGB"] = new_total_bytes
    update_data["enable"] = True

    try:
        await xui_api.update_client(email, update_data)
        from db.models import clear_notified_alerts

        await clear_notified_alerts(email)
        await state.clear()
        await _render_sub_dashboard(
            message,
            email,
            state,
            notice=f"✅ <b>حجم کل اشتراک به {format_size_gb(new_total_gb)} تغییر یافت.</b>",
        )
    except Exception as e:
        logger.error("Failed to update GB for %s: %s", email, e)
        await message.answer(f"❌ خطا در بروزرسانی حجم: {e}")


@router.callback_query(F.data.startswith("admin_sub_days_menu_"))
async def admin_sub_days_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_days_menu_") :]
    await state.update_data(manage_email=email)
    await state.set_state(AdminSearchStates.waiting_add_days)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_manage_sub_{email}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"⏱ <b>افزایش یا تمدید زمان برای اشتراک «{email}»:</b>\n\n"
        "تعداد روز جدید یا اضافی را وارد کنید:\n"
        "• برای <b>تمدید و افزودن روز</b>، عدد روزها را وارد کنید (مثال: <code>30</code>)\n"
        "• برای <b>تنظیم دقیق روزهای مانده از الان</b>، عبارت <code>set:60</code> را بفرستید.\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_add_days, F.text)
async def admin_sub_days_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("manage_email")

    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if email:
            await _render_sub_dashboard(
                message, email, state, notice="❌ تغییر زمان لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not email:
        await state.clear()
        return

    client = await xui_api.get_client(email)
    if not client:
        await state.clear()
        await message.answer("❌ اشتراک یافت نشد.")
        return

    txt = message.text.strip()
    is_set = False
    if txt.lower().startswith("set:"):
        is_set = True
        txt = txt[4:].strip()

    try:
        days_val = int(persian_to_english_digits(txt))
        if days_val <= 0:
            raise ValueError
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_manage_sub_{email}"
                        if email
                        else "admin_sub_users_menu",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید (مثال: 30).\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    now_ms = int(time.time() * 1000)
    current_expiry_ms = client.get("expiryTime", 0)

    if is_set:
        from db.models import get_start_first_use_config

        start_first_use = await get_start_first_use_config()
        if start_first_use and days_val > 0:
            new_expiry_ms = -int(days_val * 86400 * 1000)
        else:
            new_expiry_ms = now_ms + (days_val * 86400 * 1000)
    elif current_expiry_ms < 0:
        new_expiry_ms = current_expiry_ms - (days_val * 86400 * 1000)
    elif current_expiry_ms < now_ms:
        new_expiry_ms = now_ms + (days_val * 86400 * 1000)
    else:
        new_expiry_ms = current_expiry_ms + (days_val * 86400 * 1000)

    update_data = dict(client)
    update_data["expiryTime"] = new_expiry_ms
    update_data["enable"] = True

    try:
        await xui_api.update_client(email, update_data)
        from db.models import clear_notified_alerts

        await clear_notified_alerts(email)
        await state.clear()
        await _render_sub_dashboard(
            message,
            email,
            state,
            notice="✅ <b>زمان اشتراک با موفقیت بروزرسانی شد.</b>",
        )
    except Exception as e:
        logger.error("Failed to update expiry for %s: %s", email, e)
        await message.answer(f"❌ خطا در بروزرسانی تاریخ انقضا: {e}")


@router.callback_query(F.data.startswith("admin_sub_ip_menu_"))
async def admin_sub_ip_prompt(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_ip_menu_") :]
    await state.update_data(manage_email=email)
    await state.set_state(AdminSearchStates.waiting_limit_ip)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_manage_sub_{email}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"👤 <b>تغییر سقف کاربر همزمان (IP Limit) برای «{email}»:</b>\n\n"
        "تعداد کاربران مجاز همزمان را به صورت عدد وارد کنید (0 یعنی نامحدود):\n"
        "مثال: <code>2</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_limit_ip, F.text)
async def admin_sub_ip_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("manage_email")

    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if email:
            await _render_sub_dashboard(
                message, email, state, notice="❌ تغییر سقف کاربر لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not email:
        await state.clear()
        return

    try:
        limit_ip = int(persian_to_english_digits(message.text.strip()))
        if limit_ip < 0:
            raise ValueError
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_manage_sub_{email}"
                        if email
                        else "admin_sub_users_menu",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید (0 برای نامحدود).\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    client = await xui_api.get_client(email)
    if not client:
        await state.clear()
        await message.answer("❌ اشتراک یافت نشد.")
        return

    update_data = dict(client)
    update_data["limitIp"] = limit_ip

    try:
        await xui_api.update_client(email, update_data)
        await state.clear()
        await _render_sub_dashboard(
            message,
            email,
            state,
            notice="✅ <b>سقف کاربر اشتراک با موفقیت تغییر یافت.</b>",
        )
    except Exception as e:
        logger.error("Failed to update limitIp for %s: %s", email, e)
        await message.answer(f"❌ خطا در تغییر سقف کاربر: {e}")


@router.callback_query(F.data.startswith("admin_sub_rename_menu_"))
async def admin_sub_rename_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_rename_menu_") :]
    await state.update_data(manage_email=email)
    await state.set_state(AdminSearchStates.waiting_rename_email)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_manage_sub_{email}",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"✏️ <b>تغییر نام سرویس (Email) برای «{email}»:</b>\n\n"
        "نام جدید و دلخواه خود را ارسال نمایید:\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_rename_email, F.text)
async def admin_sub_rename_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    old_email = data.get("manage_email")

    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if old_email:
            await _render_sub_dashboard(
                message, old_email, state, notice="❌ تغییر نام لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    new_email = message.text.strip()[:50]
    if not old_email:
        await state.clear()
        return

    client = await xui_api.get_client(old_email)
    if not client:
        await state.clear()
        await message.answer("❌ اشتراک یافت نشد.")
        return

    update_data = dict(client)
    update_data["email"] = new_email

    try:
        await xui_api.update_client(old_email, update_data)
        from db.models import rename_client_references

        await rename_client_references(old_email, new_email)
        await state.clear()
        await _render_sub_dashboard(
            message,
            new_email,
            state,
            notice=f"✅ <b>نام سرویس از «{old_email}» به «{new_email}» تغییر یافت.</b>",
        )
    except Exception as e:
        logger.warning("Failed to rename %s: %s", old_email, e)
        err_str = str(e).lower()
        if (
            "unique" in err_str
            or "constraint" in err_str
            or "exist" in err_str
            or "already" in err_str
        ):
            await message.answer(
                "⚠️ <b>این نام قبلاً توسط سرویس دیگری استفاده شده است!</b>\nلطفاً نام متفاوتی وارد کنید:",
                parse_mode="HTML",
            )
        else:
            await message.answer(f"❌ خطا در تغییر نام سرویس: {e}")


@router.callback_query(F.data.startswith("admin_sub_group_menu_"))
async def admin_sub_group_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_group_menu_") :]
    await state.update_data(manage_email=email)
    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    current_group = client.get("group", "") or ""
    groups = await xui_api.list_client_groups()
    group_names = [g.get("name", "") for g in groups if g.get("name")]
    await state.update_data(available_groups=group_names)

    keyboard_rows = []
    for idx, g_name in enumerate(group_names):
        mark = "✅ " if g_name == current_group else "📁 "
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark}{g_name}",
                    callback_data=f"admin_sub_grpchoice_{idx}",
                )
            ]
        )

    none_mark = "✅ " if not current_group else "⚪️ "
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text=f"{none_mark}بدون گروه",
                callback_data="admin_sub_grpchoice_none",
            )
        ]
    )
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="✏️ ورود نام دلخواه گروه",
                callback_data="admin_sub_grpcust",
            )
        ]
    )
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="❌ انصراف و بازگشت",
                callback_data=f"admin_manage_sub_{email}",
            )
        ]
    )

    curr_str = f"<code>{current_group}</code>" if current_group else "<i>بدون گروه</i>"
    text = (
        f"🏷 <b>تغییر گروه مشتری برای اشتراک «<code>{email}</code>»:</b>\n\n"
        f"گروه فعلی: {curr_str}\n\n"
        "لطفاً یکی از گروه‌های موجود در پنل را انتخاب کنید یا نام دلخواه وارد نمایید:"
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_sub_grpchoice_"))
async def admin_sub_grp_select(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    data = await state.get_data()
    email = data.get("manage_email")
    if not email:
        await callback.answer("❌ نشست منقضی شده است.", show_alert=True)
        return

    choice = callback.data[len("admin_sub_grpchoice_") :]
    if choice == "none":
        new_group = ""
    else:
        try:
            idx = int(choice)
            groups = data.get("available_groups") or []
            new_group = groups[idx]
        except (ValueError, IndexError):
            await callback.answer("❌ گروه نامعتبر است.", show_alert=True)
            return

    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    update_data = dict(client)
    update_data["group"] = new_group
    try:
        await xui_api.update_client(email, update_data)
        notice = (
            f"✅ <b>گروه اشتراک به «{new_group}» تغییر یافت.</b>"
            if new_group
            else "✅ <b>گروه اشتراک حذف شد (بدون گروه).</b>"
        )
    except Exception as e:
        logger.error("Failed to update group for %s: %s", email, e)
        notice = f"❌ خطا در تغییر گروه: {e}"

    await _render_sub_dashboard(callback, email, state, notice=notice)


@router.callback_query(F.data == "admin_sub_grpcust")
async def admin_sub_grp_custom_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    data = await state.get_data()
    email = data.get("manage_email")
    if not email:
        await callback.answer("❌ نشست منقضی شده است.", show_alert=True)
        return

    await state.set_state(AdminSearchStates.waiting_sub_custom_group)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_sub_group_menu_{email}",
                )
            ]
        ]
    )
    await safe_edit_text(
        callback.message,
        f"✏️ <b>ورود نام گروه دلخواه برای اشتراک «{email}»:</b>\n\n"
        "نام گروه جدید را ارسال کنید:\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_sub_custom_group, F.text)
async def admin_sub_grp_custom_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("manage_email")
    if not message.text or message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        if email:
            await _render_sub_dashboard(
                message, email, state, notice="❌ تغییر گروه لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not email:
        await _clear_fsm_keep_nav(state)
        return

    new_group = message.text.strip()[:50]
    client = await xui_api.get_client(email)
    if not client:
        await _clear_fsm_keep_nav(state)
        await message.answer("❌ اشتراک یافت نشد.")
        return

    update_data = dict(client)
    update_data["group"] = new_group
    try:
        await xui_api.update_client(email, update_data)
        await _clear_fsm_keep_nav(state)
        await _render_sub_dashboard(
            message,
            email,
            state,
            notice=f"✅ <b>گروه اشتراک به «{new_group}» تغییر یافت.</b>",
        )
    except Exception as e:
        logger.error("Failed to update group for %s: %s", email, e)
        await message.answer(f"❌ خطا در تغییر گروه: {e}")


@router.callback_query(F.data.startswith("admin_sub_tgid_menu_"))
async def admin_sub_tgid_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_tgid_menu_") :]
    await state.update_data(manage_email=email)
    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    curr_tgid = client.get("tgId", 0) or 0
    try:
        curr_tgid_int = int(curr_tgid)
    except (ValueError, TypeError):
        curr_tgid_int = 0

    curr_tgid_str = (
        f"<code>{curr_tgid_int}</code>"
        if curr_tgid_int > 0
        else "<i>نامشخص (بدون کاربر)</i>"
    )

    await state.set_state(AdminSearchStates.waiting_sub_tgid)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚪️ بدون کاربر (لغو اتصال)",
                    callback_data=f"admin_sub_tgid_clear_{email}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_manage_sub_{email}",
                )
            ],
        ]
    )

    await safe_edit_text(
        callback.message,
        f"👤 <b>تغییر یا انتساب کاربر به اشتراک «<code>{email}</code>»:</b>\n\n"
        f"شناسه فعلی کاربر: {curr_tgid_str}\n\n"
        "لطفاً <b>آیدی عددی تلگرام</b> (مثال: <code>123456789</code>) یا <b>نام‌کاربری</b> (مثال: <code>@username</code>) کاربر مورد نظر را ارسال نمایید:\n"
        "• برای لغو اتصال کاربر به این اشتراک می‌توانید از دکمه زیر یا ارسال عدد <code>0</code> استفاده کنید.\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_sub_tgid_clear_"))
async def admin_sub_tgid_clear(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_tgid_clear_") :]
    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    update_data = dict(client)
    update_data["tgId"] = 0

    try:
        await xui_api.update_client(email, update_data)
        notice = "✅ <b>اتصال کاربر به اشتراک لغو شد (بدون کاربر).</b>"
    except Exception as e:
        logger.error("Failed to clear tgId for %s: %s", email, e)
        notice = f"❌ خطا در لغو اتصال کاربر: {e}"

    await _clear_fsm_keep_nav(state)
    await _render_sub_dashboard(callback, email, state, notice=notice)


@router.message(AdminSearchStates.waiting_sub_tgid, F.text)
async def admin_sub_tgid_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("manage_email")

    if not message.text or message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        if email:
            await _render_sub_dashboard(
                message, email, state, notice="❌ تغییر کاربر لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not email:
        await _clear_fsm_keep_nav(state)
        return

    client = await xui_api.get_client(email)
    if not client:
        await _clear_fsm_keep_nav(state)
        await message.answer("❌ اشتراک یافت نشد.")
        return

    raw_text = message.text.strip()
    target_tg_id = 0
    target_name_info = ""

    if raw_text.startswith("@") or not raw_text.lstrip("-").isdigit():
        clean_uname = raw_text.lstrip("@").strip()
        users = await search_users(clean_uname)
        matched_user = None
        for u in users:
            if (u.get("username") or "").lower() == clean_uname.lower():
                matched_user = u
                break
        if not matched_user and users:
            matched_user = users[0]

        if not matched_user:
            await message.answer(
                f"❌ کاربری با مشخصات «{raw_text}» در دیتابیس ربات یافت نشد.\nلطفاً آیدی عددی تلگرام را وارد کنید یا نام کاربری صحیح را بفرستید."
            )
            return

        target_tg_id = matched_user["tg_id"]
        target_name_info = (
            f" ({matched_user.get('full_name') or matched_user.get('username') or ''})"
        )
    else:
        try:
            target_tg_id = int(persian_to_english_digits(raw_text))
            if target_tg_id < 0:
                raise ValueError
        except ValueError:
            await message.answer(
                "❌ لطفاً یک شناسه معتبر وارد کنید (مثال: 123456789 یا @username یا 0 برای حذف)."
            )
            return

        if target_tg_id > 0:
            from db.models import get_user

            u = await get_user(target_tg_id)
            if u:
                target_name_info = f" ({u.get('full_name') or u.get('username') or ''})"

    update_data = dict(client)
    update_data["tgId"] = target_tg_id

    try:
        await xui_api.update_client(email, update_data)
        await _clear_fsm_keep_nav(state)
        if target_tg_id > 0:
            notice = f"✅ <b>اشتراک با موفقیت به کاربر <code>{target_tg_id}</code>{target_name_info} منتسب شد.</b>"
        else:
            notice = "✅ <b>اتصال کاربر به اشتراک لغو شد (بدون کاربر).</b>"
        await _render_sub_dashboard(message, email, state, notice=notice)
    except Exception as e:
        logger.error("Failed to update tgId for %s: %s", email, e)
        await message.answer(f"❌ خطا در بروزرسانی شناسه کاربر: {e}")


@router.callback_query(F.data.startswith("admin_sub_toggle_enable_"))
async def admin_sub_toggle_enable(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_toggle_enable_") :]
    client = await xui_api.get_client(email)

    if not client:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    new_enable = not client.get("enable", True)
    update_data = dict(client)
    update_data["enable"] = new_enable

    try:
        await xui_api.update_client(email, update_data)
        status_msg = "فعال" if new_enable else "غیرفعال"
        notice = f"✅ <b>اشتراک «{email}» {status_msg} گردید.</b>"
    except Exception as e:
        logger.error("Failed to toggle enable for %s: %s", email, e)
        notice = f"❌ <b>خطا در تغییر وضعیت:</b> {e}"

    await _render_sub_dashboard(callback, email, state, notice=notice)


@router.callback_query(F.data.startswith("admin_sub_reset_traffic_"))
async def admin_sub_reset_traffic(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_reset_traffic_") :]
    try:
        await xui_api.reset_client_traffic(email)
        notice = f"✅ <b>مصرف ترافیک اشتراک «{email}» صفر شد.</b>"
    except Exception as e:
        logger.error("Failed to reset traffic for %s: %s", email, e)
        notice = f"❌ <b>خطا در صفر کردن مصرف:</b> {e}"

    await _render_sub_dashboard(callback, email, state, notice=notice)


@router.callback_query(F.data.startswith("admin_sub_res_act_"))
async def admin_sub_res_act(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_res_act_") :]
    from services.renewal_service import activate_reserved_renewal

    res = await activate_reserved_renewal(
        email, force=True, bot=callback.bot, activation_type="admin"
    )
    if res.get("status") == "activated":
        notice = f"✅ <b>بسته رزرو اشتراک «{email}» با موفقیت فعال شد.</b>"
    elif res.get("status") == "no_reserved":
        notice = "❌ <b>هیچ بسته رزروی برای این اشتراک وجود ندارد.</b>"
    else:
        notice = f"❌ <b>خطا در فعال‌سازی:</b> {res.get('msg', 'ناشناخته')}"

    await _render_sub_dashboard(callback, email, state, notice=notice)


@router.callback_query(F.data.startswith("admin_sub_res_del_"))
async def admin_sub_res_del(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_res_del_") :]
    from db.models import delete_reserved_renewal

    await delete_reserved_renewal(email)
    notice = f"🗑 <b>بسته رزرو اشتراک «{email}» حذف شد.</b>"
    await _render_sub_dashboard(callback, email, state, notice=notice)


@router.callback_query(F.data.startswith("admin_sub_qr_"))
async def admin_sub_qr(callback: types.CallbackQuery) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_qr_") :]
    client = await xui_api.get_client(email)

    if not client:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    sub_id = client.get("subId", "")
    sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

    if sub_link != "نامشخص":
        buf = generate_qr(sub_link)
        input_file = types.BufferedInputFile(buf.getvalue(), filename="qrcode.png")
        cap = (
            f"🔗 <b>اطلاعات اشتراک:</b> <code>{email}</code>\n\n<code>{sub_link}</code>"
        )
        if callback.message:
            await callback.message.answer_photo(
                photo=input_file, caption=cap, parse_mode="HTML"
            )
        await callback.answer()
    else:
        await callback.answer("❌ لینک اشتراک یافت نشد.", show_alert=True)


@router.callback_query(F.data.startswith("admin_sub_del_ask_"))
async def admin_sub_del_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_del_ask_") :]
    text = (
        f"⚠️ <b>آیا از حذف کامل اشتراک «<code>{email}</code>» اطمینان دارید؟</b>\n\n"
        "⚠️ <i>این عملیات غیرقابل بازگشت است و کلاینت به طور کامل از پنل و سرور حذف خواهد شد.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 بله، حذف شود",
                    callback_data=f"admin_sub_delete_{email}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_manage_sub_{email}",
                ),
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


@router.callback_query(F.data.startswith("admin_sub_delete_"))
async def admin_sub_delete(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    email = callback.data[len("admin_sub_delete_") :]
    delete_success = False
    try:
        await xui_api.delete_client(email)
        delete_success = True
        await callback.answer(f"✅ اشتراک «{email}» با موفقیت حذف شد.", show_alert=True)
    except Exception as e:
        logger.error("Failed to delete client %s: %s", email, e)
        await callback.answer(f"❌ خطا در حذف اشتراک: {e}", show_alert=True)

    if not delete_success:
        await _render_sub_dashboard(
            callback, email, state, notice=f"❌ خطا در حذف اشتراک «{email}»"
        )
        return

    data = await state.get_data()
    sub_back = data.get("sub_back_callback")
    manage_user_id = data.get("manage_user_id")
    last_query = data.get("last_search_query")
    notice_text = f"✅ اشتراک «{email}» با موفقیت حذف شد."

    if sub_back:
        if sub_back.startswith("admin_user_subs_"):
            parts = sub_back.split("_")
            tg_id = int(parts[3])
            remaining = await xui_api.get_normalized_clients_by_tg_id(tg_id)
            if not remaining:
                await _render_user_dashboard(
                    callback,
                    tg_id,
                    state,
                    notice=f"✅ اشتراک «{email}» حذف شد. کاربر هیچ اشتراک دیگری ندارد.",
                )
                return
            else:
                callback.data = sub_back
                await admin_user_subs(callback, state)
                return
        elif sub_back.startswith("admin_manage_user_"):
            parts = sub_back[len("admin_manage_user_") :].split("_")
            try:
                tg_id = int(parts[0])
                await _render_user_dashboard(callback, tg_id, state, notice=notice_text)
                return
            except (ValueError, IndexError):
                pass
        elif sub_back.startswith("admin_notif_return_"):
            inv_id = sub_back[len("admin_notif_return_") :]
            try:
                from handlers.admin_invoices import render_admin_notification_view

                await render_admin_notification_view(
                    callback.message, inv_id, callback.from_user.id
                )
                return
            except Exception as e:
                logger.warning(
                    "Failed to return to admin notif view after delete: %s", e
                )
        elif sub_back.startswith("admin_online_clients_"):
            callback.data = sub_back
            try:
                from handlers.admin_control import admin_online_clients_list

                await admin_online_clients_list(callback, state)
                return
            except Exception as e:
                logger.warning(
                    "Failed to return to admin online clients list after delete: %s", e
                )
        elif sub_back.startswith("admin_cdate_page_"):
            try:
                page = int(sub_back[len("admin_cdate_page_") :])
            except ValueError:
                page = 0
            await _render_clients_by_date_list(
                callback, state, page=page, notice=notice_text
            )
            return

    if manage_user_id:
        await _render_user_dashboard(
            callback, int(manage_user_id), state, notice=notice_text
        )
        return

    if last_query:
        await _perform_search_and_render(callback, state, last_query)
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به مدیریت کاربران",
                    callback_data="admin_sub_users_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔍 جستجوی مجدد",
                    callback_data="admin_search_start",
                ),
            ],
        ]
    )
    await safe_edit_text(
        callback.message,
        f"✅ اشتراک «<code>{email}</code>» با موفقیت حذف شد.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_manage_user_"))
async def admin_manage_user_dashboard(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    payload = callback.data[len("admin_manage_user_") :]
    parts = payload.split("_")
    tg_id = int(parts[0])

    if len(parts) > 1:
        ref_source = parts[1]
        if ref_source == "back":
            await _clear_fsm_keep_nav(state)
        elif ref_source.startswith("p"):
            try:
                page_num = int(ref_source[1:])
                await state.update_data(
                    current_list_page=page_num,
                    last_search_query=None,
                    invoice_back_callback=None,
                )
            except ValueError:
                pass
        elif ref_source == "search":
            data = await state.get_data()
            if not data.get("last_search_query"):
                await state.update_data(
                    last_search_query=str(tg_id),
                    invoice_back_callback=None,
                )
            else:
                await state.update_data(invoice_back_callback=None)
        elif ref_source == "inv" and len(parts) >= 5:
            inv_id = parts[2]
            status_filter = parts[3]
            page_num = parts[4]
            if status_filter == "notif":
                back_target = f"admin_notif_return_{inv_id}"
            else:
                back_target = f"admin_inv_view_{inv_id}_{status_filter}_{page_num}"
            await state.update_data(
                invoice_back_callback=back_target,
                last_search_query=None,
            )

    await _render_user_dashboard(callback, tg_id, state)


@router.callback_query(F.data.startswith("admin_user_wallet_"))
async def admin_user_wallet_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    tg_id = int(callback.data[len("admin_user_wallet_") :])
    await state.update_data(manage_user_id=tg_id)
    await state.set_state(AdminSearchStates.waiting_user_wallet)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_manage_user_{tg_id}_back",
                )
            ]
        ]
    )

    await callback.message.edit_text(
        f"💳 <b>شارژ یا تغییر موجودی کیف پول کاربر ({tg_id}):</b>\n\n"
        "مبلغ (به تومان) را وارد کنید:\n"
        "• برای <b>شارژ و افزودن به موجودی</b>، عدد مثبت وارد کنید (مثال: <code>50000</code>)\n"
        "• برای <b>تنظیم مستقیم موجودی</b>، عبارت <code>set:100000</code> را ارسال کنید.\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_user_wallet, F.text)
async def admin_user_wallet_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    tg_id = data.get("manage_user_id")

    if not message.text or message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        if tg_id:
            await _render_user_dashboard(
                message, tg_id, state, notice="❌ تغییر کیف پول لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not tg_id:
        await _clear_fsm_keep_nav(state)
        return

    txt = message.text.strip()
    is_set = False
    if txt.lower().startswith("set:"):
        is_set = True
        txt = txt[4:].strip()

    try:
        amount = int(persian_to_english_digits(txt))
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_manage_user_{tg_id}_back"
                        if tg_id
                        else "admin_sub_users_menu",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر به تومان وارد کنید.\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    if is_set:
        from db.database import get_db

        db = await get_db()
        await db.execute(
            "UPDATE wallets SET balance = ? WHERE tg_id = ?", (amount, tg_id)
        )
        await db.commit()
        new_balance = amount
    else:
        new_balance = await credit_wallet(tg_id, amount)

    await _clear_fsm_keep_nav(state)
    await _render_user_dashboard(
        message,
        tg_id,
        state,
        notice=f"✅ <b>موجودی جدید کیف پول کاربر: {format_price(new_balance)}</b>",
    )


@router.callback_query(F.data.startswith("admin_user_reset_test_"))
async def admin_user_reset_test(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    tg_id = int(callback.data[len("admin_user_reset_test_") :])
    from db.models import reset_user_test_sub

    await reset_user_test_sub(tg_id)

    await _render_user_dashboard(
        callback,
        tg_id,
        state,
        notice=f"✅ <b>امکان دریافت اشتراک تست برای کاربر {tg_id} با موفقیت بازنشانی شد.</b>",
    )


@router.callback_query(F.data.startswith("admin_user_ban_"))
async def admin_user_ban_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    from db.models import has_admin_permission, is_user_banned
    from keyboards.inline_kb import admin_user_ban_scope_keyboard

    if not await has_admin_permission(callback.from_user.id, "ban_users"):
        await callback.answer(
            "⛔️ شما دسترسی مسدودسازی کاربران را ندارید.", show_alert=True
        )
        return

    tg_id = int(callback.data[len("admin_user_ban_") :])
    banned = await is_user_banned(tg_id)

    await state.update_data(manage_user_id=tg_id)
    await state.set_state(None)

    if not banned:
        title = (
            f"🚫 <b>مسدودسازی دسترسی کاربر <code>{tg_id}</code></b>\n\n"
            "لطفاً محدوده مسدودسازی را مشخص کنید:\n"
            "• <b>فقط ربات:</b> کاربر امکان استفاده از ربات را نخواهد داشت ولی سرویس‌های فعلی فعال می‌مانند.\n"
            "• <b>ربات + سرور:</b> علاوه بر ربات، کلیه سرویس‌های کاربر در پنل 3x-ui نیز غیرفعال می‌شوند."
        )
    else:
        title = (
            f"✅ <b>رفع مسدودی دسترسی کاربر <code>{tg_id}</code></b>\n\n"
            "لطفاً محدوده رفع مسدودی را مشخص کنید:\n"
            "• <b>فقط ربات:</b> دسترسی کاربر به ربات مجدداً فعال می‌شود.\n"
            "• <b>ربات + سرور:</b> دسترسی به ربات فعال شده و کلیه سرویس‌های کاربر در سرور مجدداً فعال می‌شوند."
        )

    await safe_edit_text(
        callback.message,
        title,
        reply_markup=admin_user_ban_scope_keyboard(tg_id, banned),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_banscope_"))
async def admin_user_ban_scope(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    from db.models import has_admin_permission, is_user_banned
    from keyboards.inline_kb import admin_user_ban_notify_keyboard

    if not await has_admin_permission(callback.from_user.id, "ban_users"):
        await callback.answer(
            "⛔️ شما دسترسی مسدودسازی کاربران را ندارید.", show_alert=True
        )
        return

    parts = callback.data.split("_")
    tg_id = int(parts[3])
    scope = parts[4]

    await state.update_data(ban_scope=scope, manage_user_id=tg_id)
    banned = await is_user_banned(tg_id)

    scope_desc = (
        "فقط ربات"
        if scope == "bot"
        else ("ربات + سرور (غیرفعال‌سازی)" if not banned else "ربات + سرور (فعال‌سازی)")
    )
    action_str = "مسدودسازی" if not banned else "رفع مسدودی"

    text = (
        f"📩 <b>نحوه اطلاع‌رسانی ({action_str} کاربر <code>{tg_id}</code>)</b>\n\n"
        f"محدوده انتخاب‌شده: <b>{scope_desc}</b>\n\n"
        "لطفاً نحوه اطلاع‌رسانی به کاربر را انتخاب کنید:"
    )

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=admin_user_ban_notify_keyboard(tg_id, banned, scope),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_banaction_"))
async def admin_user_ban_action(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    from db.models import has_admin_permission, is_user_banned, set_user_ban_status
    from keyboards.inline_kb import admin_user_ban_cancel_keyboard
    from services.xui_api import set_user_clients_enable

    if not await has_admin_permission(callback.from_user.id, "ban_users"):
        await callback.answer(
            "⛔️ شما دسترسی مسدودسازی کاربران را ندارید.", show_alert=True
        )
        return

    parts = callback.data.split("_")
    tg_id = int(parts[3])
    scope = parts[4]
    notify = parts[5]

    banned = await is_user_banned(tg_id)

    if notify == "custom":
        await state.update_data(manage_user_id=tg_id, ban_scope=scope)
        if not banned:
            await state.set_state(AdminSearchStates.waiting_ban_custom_msg)
            prompt = (
                f"✍️ <b>ارسال پیام دلخواه مسدودسازی به کاربر <code>{tg_id}</code>:</b>\n\n"
                "لطفاً متن پیام ارسالی به کاربر را تایپ کنید:\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید:</i>"
            )
        else:
            await state.set_state(AdminSearchStates.waiting_unban_custom_msg)
            prompt = (
                f"✍️ <b>ارسال پیام دلخواه رفع مسدودی به کاربر <code>{tg_id}</code>:</b>\n\n"
                "لطفاً متن پیام ارسالی به کاربر را تایپ کنید:\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید:</i>"
            )

        await safe_edit_text(
            callback.message,
            prompt,
            reply_markup=admin_user_ban_cancel_keyboard(tg_id),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if not banned:
        await set_user_ban_status(tg_id, True)
        sub_info = ""
        if scope == "both":
            ok_cnt, _ = await set_user_clients_enable(tg_id, False)
            sub_info = f"\n🔌 سرویس‌های غیرفعال‌شده در سرور: {ok_cnt}"

        if notify == "default":
            try:
                await callback.bot.send_message(
                    chat_id=tg_id,
                    text=(
                        "⛔️ <b>کاربر گرامی، دسترسی حساب شما به ربات مسدود گردید.</b>\n\n"
                        "امکان استفاده از خدمات و ارتباط با ربات برای شما غیرفعال شده است."
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass

        notice = f"🚫 <b>کاربر {tg_id} با موفقیت مسدود شد.</b>{sub_info}"
    else:
        await set_user_ban_status(tg_id, False)
        sub_info = ""
        if scope == "both":
            ok_cnt, _ = await set_user_clients_enable(tg_id, True)
            sub_info = f"\n🔌 سرویس‌های فعال‌شده مجدد در سرور: {ok_cnt}"

        if notify == "default":
            try:
                await callback.bot.send_message(
                    chat_id=tg_id,
                    text=(
                        "✅ <b>کاربر گرامی، دسترسی حساب شما به ربات مجدداً فعال گردید.</b>\n\n"
                        "هم‌اکنون می‌توانید از خدمات و امکانات ربات استفاده نمایید."
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass

        notice = f"✅ <b>مسدودیت کاربر {tg_id} با موفقیت برطرف شد.</b>{sub_info}"

    await _clear_fsm_keep_nav(state)
    await _render_user_dashboard(callback, tg_id, state, notice=notice)
    await callback.answer()


@router.message(AdminSearchStates.waiting_ban_custom_msg, F.text)
async def admin_user_ban_custom_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    tg_id = data.get("manage_user_id")
    scope = data.get("ban_scope", "bot")

    if not message.text or message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        if tg_id:
            await _render_user_dashboard(
                message, tg_id, state, notice="❌ عملیات مسدودسازی لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not tg_id:
        await _clear_fsm_keep_nav(state)
        return

    custom_text = message.text.strip()

    from db.models import set_user_ban_status
    from services.xui_api import set_user_clients_enable

    await set_user_ban_status(tg_id, True)

    sub_info = ""
    if scope == "both":
        ok_cnt, _ = await set_user_clients_enable(tg_id, False)
        sub_info = f"\n🔌 سرویس‌های غیرفعال‌شده در سرور: {ok_cnt}"

    try:
        await message.bot.send_message(
            chat_id=tg_id,
            text=f"⛔️ <b>پیام مدیریت:</b>\n\n{custom_text}",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await _clear_fsm_keep_nav(state)
    await _render_user_dashboard(
        message,
        tg_id,
        state,
        notice=f"🚫 <b>کاربر {tg_id} مسدود شد و پیام برای وی ارسال گردید.</b>{sub_info}",
    )


@router.message(AdminSearchStates.waiting_unban_custom_msg, F.text)
async def admin_user_unban_custom_save(
    message: types.Message, state: FSMContext
) -> None:
    data = await state.get_data()
    tg_id = data.get("manage_user_id")
    scope = data.get("ban_scope", "bot")

    if not message.text or message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        if tg_id:
            await _render_user_dashboard(
                message, tg_id, state, notice="❌ عملیات رفع مسدودی لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not tg_id:
        await _clear_fsm_keep_nav(state)
        return

    custom_text = message.text.strip()

    from db.models import set_user_ban_status
    from services.xui_api import set_user_clients_enable

    await set_user_ban_status(tg_id, False)

    sub_info = ""
    if scope == "both":
        ok_cnt, _ = await set_user_clients_enable(tg_id, True)
        sub_info = f"\n🔌 سرویس‌های فعال‌شده مجدد در سرور: {ok_cnt}"

    try:
        await message.bot.send_message(
            chat_id=tg_id,
            text=f"✅ <b>پیام مدیریت:</b>\n\n{custom_text}",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await _clear_fsm_keep_nav(state)
    await _render_user_dashboard(
        message,
        tg_id,
        state,
        notice=f"✅ <b>مسدودیت کاربر {tg_id} رفع شد و پیام برای وی ارسال گردید.</b>{sub_info}",
    )


async def _render_user_msg_preview(
    event: types.Message | types.CallbackQuery,
    tg_id: int,
    state: FSMContext,
) -> None:
    from keyboards.inline_kb import admin_user_msg_preview_keyboard

    data = await state.get_data()
    msg_type = data.get("direct_msg_type", "text")
    file_id = data.get("direct_msg_file_id")
    text_content = data.get("direct_msg_text", "")
    with_header = data.get("direct_msg_with_header", True)

    header_status = (
        "فعال ✅ (همراه با «📩 پیام از طرف مدیریت ربات:»)"
        if with_header
        else "غیرفعال ❌ (ارسال عین پیام)"
    )
    type_names = {
        "text": "📝 متنی",
        "photo": "🖼 تصویر",
        "video": "🎬 ویدیو",
        "voice": "🎙 صوتی (وویس)",
        "audio": "🎵 آهنگ/صوت",
        "document": "📁 فایل / داکیومنت",
    }
    type_str = type_names.get(msg_type, msg_type)

    preview_caption = (
        f"👁 <b>پیش‌نمایش پیام ارسالی به کاربر <code>{tg_id}</code></b>\n\n"
        f"نوع پیام: <b>{type_str}</b>\n"
        f"سربرگ مدیریت: <b>{header_status}</b>\n\n"
        "👇 <b>محتوای پیام:</b>\n"
        "────────────────────\n"
    )
    if with_header:
        preview_caption += "📩 <b>پیام از طرف مدیریت ربات:</b>\n\n"
    preview_caption += text_content or "<i>(بدون متن یا کپشن)</i>"

    kb = admin_user_msg_preview_keyboard(tg_id, with_header)

    if isinstance(event, types.CallbackQuery):
        msg = event.message
        if msg:
            try:
                if msg.photo or msg.video or msg.document or msg.voice or msg.audio:
                    await msg.edit_caption(
                        caption=preview_caption, reply_markup=kb, parse_mode="HTML"
                    )
                else:
                    await msg.edit_text(
                        text=preview_caption, reply_markup=kb, parse_mode="HTML"
                    )
                await event.answer()
                return
            except Exception as e:
                logger.debug("Error updating preview: %s", e)

    bot = event.bot
    target_chat = event.from_user.id if event.from_user else 0
    if msg_type == "photo" and file_id:
        await bot.send_photo(
            chat_id=target_chat,
            photo=file_id,
            caption=preview_caption,
            reply_markup=kb,
            parse_mode="HTML",
        )
    elif msg_type == "video" and file_id:
        await bot.send_video(
            chat_id=target_chat,
            video=file_id,
            caption=preview_caption,
            reply_markup=kb,
            parse_mode="HTML",
        )
    elif msg_type == "voice" and file_id:
        await bot.send_voice(
            chat_id=target_chat,
            voice=file_id,
            caption=preview_caption,
            reply_markup=kb,
            parse_mode="HTML",
        )
    elif msg_type == "audio" and file_id:
        await bot.send_audio(
            chat_id=target_chat,
            audio=file_id,
            caption=preview_caption,
            reply_markup=kb,
            parse_mode="HTML",
        )
    elif msg_type == "document" and file_id:
        await bot.send_document(
            chat_id=target_chat,
            document=file_id,
            caption=preview_caption,
            reply_markup=kb,
            parse_mode="HTML",
        )
    else:
        await bot.send_message(
            chat_id=target_chat,
            text=preview_caption,
            reply_markup=kb,
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("admin_user_msg_"))
async def admin_user_msg_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    from db.models import has_admin_permission
    from keyboards.inline_kb import admin_user_msg_cancel_keyboard

    if not await has_admin_permission(callback.from_user.id, "send_user_message"):
        await callback.answer(
            "⛔️ شما دسترسی ارسال پیام به کاربر را ندارید.", show_alert=True
        )
        return

    tg_id = int(callback.data[len("admin_user_msg_") :])
    await state.update_data(manage_user_id=tg_id)
    await state.set_state(AdminSearchStates.waiting_user_direct_msg)

    prompt = (
        f"✉️ <b>ارسال پیام مستقیم به کاربر <code>{tg_id}</code>:</b>\n\n"
        "لطفاً پیام ارسالی خود را ارسال فرمایید:\n"
        "• می‌توانید متن ساده، متن با استایل HTML، عکس، ویدیو، وویس، صوت یا فایل با کپشن دلخواه ارسال کنید.\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید:</i>"
    )

    await safe_edit_text(
        callback.message,
        prompt,
        reply_markup=admin_user_msg_cancel_keyboard(tg_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_user_direct_msg)
async def admin_user_msg_receive(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    tg_id = data.get("manage_user_id")

    if message.text and message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        if tg_id:
            await _render_user_dashboard(
                message, tg_id, state, notice="❌ عملیات ارسال پیام لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not tg_id:
        await _clear_fsm_keep_nav(state)
        return

    msg_type = "text"
    file_id = None
    text_content = ""

    if message.photo:
        msg_type = "photo"
        file_id = message.photo[-1].file_id
        text_content = message.html_text if message.caption else ""
    elif message.video:
        msg_type = "video"
        file_id = message.video.file_id
        text_content = message.html_text if message.caption else ""
    elif message.voice:
        msg_type = "voice"
        file_id = message.voice.file_id
        text_content = message.html_text if message.caption else ""
    elif message.audio:
        msg_type = "audio"
        file_id = message.audio.file_id
        text_content = message.html_text if message.caption else ""
    elif message.document:
        msg_type = "document"
        file_id = message.document.file_id
        text_content = message.html_text if message.caption else ""
    elif message.text:
        msg_type = "text"
        text_content = message.html_text or message.text
    else:
        await message.answer(
            "❌ فرمت این پیام پشتیبانی نمی‌شود. لطفاً متن، عکس، ویدیو، صوت، فایل یا وویس ارسال فرمایید."
        )
        return

    await state.update_data(
        direct_msg_type=msg_type,
        direct_msg_file_id=file_id,
        direct_msg_text=text_content,
        direct_msg_with_header=True,
    )
    await state.set_state(None)
    await _render_user_msg_preview(message, tg_id, state)


@router.callback_query(F.data.startswith("admin_user_msgtoggle_"))
async def admin_user_msg_toggle_header(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    tg_id = int(callback.data[len("admin_user_msgtoggle_") :])
    data = await state.get_data()
    curr = data.get("direct_msg_with_header", True)
    await state.update_data(direct_msg_with_header=not curr)
    await _render_user_msg_preview(callback, tg_id, state)


@router.callback_query(F.data.startswith("admin_user_msgsend_"))
async def admin_user_msg_send_confirm(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    from aiogram.exceptions import TelegramForbiddenError

    from db.models import has_admin_permission

    if not await has_admin_permission(callback.from_user.id, "send_user_message"):
        await callback.answer(
            "⛔️ شما دسترسی ارسال پیام به کاربر را ندارید.", show_alert=True
        )
        return

    tg_id = int(callback.data[len("admin_user_msgsend_") :])
    data = await state.get_data()
    msg_type = data.get("direct_msg_type", "text")
    file_id = data.get("direct_msg_file_id")
    text_content = data.get("direct_msg_text", "")
    with_header = data.get("direct_msg_with_header", True)

    final_text = ""
    if with_header:
        final_text = "📩 <b>پیام از طرف مدیریت ربات:</b>\n\n"
    final_text += text_content or ""

    bot = callback.bot
    try:
        if msg_type == "photo" and file_id:
            await bot.send_photo(
                chat_id=tg_id,
                photo=file_id,
                caption=final_text or None,
                parse_mode="HTML",
            )
        elif msg_type == "video" and file_id:
            await bot.send_video(
                chat_id=tg_id,
                video=file_id,
                caption=final_text or None,
                parse_mode="HTML",
            )
        elif msg_type == "voice" and file_id:
            await bot.send_voice(
                chat_id=tg_id,
                voice=file_id,
                caption=final_text or None,
                parse_mode="HTML",
            )
        elif msg_type == "audio" and file_id:
            await bot.send_audio(
                chat_id=tg_id,
                audio=file_id,
                caption=final_text or None,
                parse_mode="HTML",
            )
        elif msg_type == "document" and file_id:
            await bot.send_document(
                chat_id=tg_id,
                document=file_id,
                caption=final_text or None,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(chat_id=tg_id, text=final_text, parse_mode="HTML")

        notice = f"✅ <b>پیام با موفقیت به کاربر <code>{tg_id}</code> ارسال شد.</b>"
    except TelegramForbiddenError:
        notice = "❌ <b>خطا در ارسال:</b> ربات توسط این کاربر مسدود (بلاک) شده است."
    except Exception as e:
        logger.error("Failed to send direct message to user %s: %s", tg_id, e)
        notice = f"❌ <b>خطا در ارسال پیام:</b> {e}"

    if callback.message and (
        callback.message.photo
        or callback.message.video
        or callback.message.document
        or callback.message.voice
        or callback.message.audio
    ):
        try:
            await callback.message.delete()
        except Exception:
            pass
        await _clear_fsm_keep_nav(state)
        await _render_user_dashboard(callback.message, tg_id, state, notice=notice)
        await callback.answer()
        return

    await _clear_fsm_keep_nav(state)
    await _render_user_dashboard(callback, tg_id, state, notice=notice)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_create_sub_"))
async def admin_user_create_sub_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    tg_id = int(callback.data[len("admin_user_create_sub_") :])
    await state.update_data(manage_user_id=tg_id)
    await state.set_state(AdminSearchStates.waiting_create_sub_gb)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_manage_user_{tg_id}_back",
                )
            ]
        ]
    )

    await callback.message.edit_text(
        f"➕ <b>ساخت اشتراک اختصاصی جدید برای کاربر {tg_id}:</b>\n\n"
        "<b>مرحله ۱:</b> لطفاً حجم اشتراک را به گیگابایت وارد کنید:\n"
        "مثال: <code>30</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_create_sub_gb, F.text)
async def admin_user_create_sub_gb_save(
    message: types.Message, state: FSMContext
) -> None:
    data = await state.get_data()
    tg_id = data.get("manage_user_id")

    if not message.text or message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        if tg_id:
            await _render_user_dashboard(
                message, tg_id, state, notice="❌ ساخت اشتراک لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    try:
        gb_val = float(persian_to_english_digits(message.text.strip()))
        if gb_val <= 0:
            raise ValueError
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_sub_user_profile_{tg_id}"
                        if tg_id
                        else "admin_sub_users_menu",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد معتبر وارد کنید (مثال: 30).\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    await state.update_data(new_sub_gb=gb_val)
    await state.set_state(AdminSearchStates.waiting_create_sub_dur)

    dur_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_sub_user_profile_{tg_id}"
                    if tg_id
                    else "admin_sub_users_menu",
                )
            ]
        ]
    )

    await message.answer(
        "<b>مرحله ۲:</b> مدت اعتبار اشتراک را به روز وارد کنید:\n"
        "مثال: <code>30</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=dur_kb,
        parse_mode="HTML",
    )


@router.message(AdminSearchStates.waiting_create_sub_dur, F.text)
async def admin_user_create_sub_dur_save(
    message: types.Message, state: FSMContext
) -> None:
    data = await state.get_data()
    tg_id = data.get("manage_user_id")

    if not message.text or message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        if tg_id:
            await _render_user_dashboard(
                message, tg_id, state, notice="❌ ساخت اشتراک لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    try:
        dur_val = int(persian_to_english_digits(message.text.strip()))
        if dur_val <= 0:
            raise ValueError
    except ValueError:
        err_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"admin_sub_user_profile_{tg_id}"
                        if tg_id
                        else "admin_sub_users_menu",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر وارد کنید (مثال: 30).\n\n💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=err_kb,
            parse_mode="HTML",
        )
        return

    gb_val = data.get("new_sub_gb", 10.0)

    if not tg_id:
        await _clear_fsm_keep_nav(state)
        return

    user = await get_user(tg_id)
    username = user.get("username") if user else None
    email = generate_email(tg_id, username)

    total_bytes = gb_to_bytes(gb_val)
    from db.models import get_start_first_use_config

    start_first_use = await get_start_first_use_config()
    if start_first_use and dur_val > 0:
        expiry_ms = -int(dur_val * 86400 * 1000)
    else:
        expiry_ms = int((time.time() + dur_val * 86400) * 1000) if dur_val > 0 else 0

    active_inbound_ids = await get_active_inbound_ids()
    active_group = await get_active_client_group()

    try:
        await xui_api.add_client(
            email=email,
            total_gb=total_bytes,
            expiry_time=expiry_ms,
            tg_id=tg_id,
            inbound_ids=active_inbound_ids,
            group=active_group,
        )

        client = await xui_api.get_client(email)
        sub_id = client.get("subId", "") if client else ""
        sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

        vol_label = await format_volume_label(gb_val, with_fair_policy=True)
        is_sub_unlimited = await is_unlimited_volume(gb_val)
        plan_line = "📦 نوع اشتراک: ♾️ نامحدود\n" if is_sub_unlimited else ""

        await _clear_fsm_keep_nav(state)
        await message.answer(
            f"🎉 <b>اشتراک جدید با موفقیت ایجاد شد!</b>\n\n"
            f"👤 کاربر: <code>{tg_id}</code>\n"
            f"🏷 نام سرویس: <code>{email}</code>\n"
            f"{plan_line}"
            f"📊 حجم: {vol_label}\n"
            f"⏱ مدت: {to_persian_digits(dur_val)} روز\n\n"
            f"🔗 <b>لینک اشتراک:</b>\n<code>{sub_link}</code>",
            parse_mode="HTML",
        )
        await _render_user_dashboard(
            message,
            tg_id,
            state,
            notice=f"🎉 <b>اشتراک جدید ({email}) برای این کاربر ساخته شد.</b>",
        )
    except Exception as e:
        logger.error("Failed to create custom sub for %s: %s", tg_id, e)
        await message.answer(f"❌ خطا در ساخت اشتراک: {e}")


@router.callback_query(F.data == "admin_search_back")
async def admin_search_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    data = await state.get_data()
    last_query = data.get("last_search_query")
    list_page = data.get("current_list_page")

    if last_query:
        await _perform_search_and_render(callback, state, last_query)
    else:
        page = list_page if list_page is not None else 0
        from handlers.admin_control import admin_users_list

        callback.data = f"admin_users_list_{page}"
        await admin_users_list(callback, state)


@router.callback_query(F.data.startswith("admin_user_invoices_"))
async def admin_user_invoices(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    parts = callback.data.split("_")
    u_id = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else 0
    page_size = 5
    offset = page * page_size

    from db.models import get_user, get_user_invoices_paginated

    user = await get_user(u_id)
    full_name = user.get("full_name") or str(u_id) if user else str(u_id)

    invoices, total_invoices = await get_user_invoices_paginated(
        u_id, offset=offset, limit=page_size
    )

    if not invoices:
        await callback.answer(
            "📭 هیچ تراکنشی برای این کاربر ثبت نشده است.", show_alert=True
        )
        return

    import math

    total_pages = max(1, math.ceil(total_invoices / page_size))

    lines = [
        f"📑 <b>سابقه تراکنش‌ها و پرداختی‌های کاربر {full_name}</b> (<code>{u_id}</code>)\n"
        f"کل فاکتورها: <b>{to_persian_digits(total_invoices)}</b> مورد\n"
    ]

    for inv in invoices:
        inv_id = inv["id"]
        amount = format_price(inv.get("amount", 0))
        status = inv.get("status")
        if status == "approved":
            status_str = "🟢 تأییدشده"
        elif status == "under_review":
            status_str = "⏳ در حال بررسی رسید"
        elif status == "pending":
            status_str = "🟡 در انتظار"
        elif status == "expired":
            status_str = "⌛️ منقضی‌شده"
        elif status == "rejected":
            status_str = "🔴 ردشده"
        elif status == "cancelled":
            status_str = "🚫 لغوشده"
        else:
            status_str = f"❌ {status}"

        pm = inv.get("payment_method") or "card"
        pm_str = "کیف پول 👛" if pm == "wallet" else "کارت به کارت 💳"
        created_at = inv.get("created_at")
        date_str = format_datetime(created_at) if created_at else "نامشخص"

        target_email = inv.get("target_email")
        dur = inv.get("duration_days", 0)
        gb = inv.get("data_gb", 0)
        users = inv.get("users_count", 1)

        if target_email == "TOPUP" or (dur == 0 and gb == 0):
            item_desc = "💵 شارژ مستقیم کیف پول"
            spec_line = ""
        elif target_email:
            item_desc = f"🔄 تمدید: <code>{target_email}</code>"
            vol_str = await format_volume_label(gb, with_fair_policy=True)
            spec_parts = []
            if gb > 0 or await is_unlimited_volume(gb):
                spec_parts.append(f"📊 حجم: {vol_str}")
            if dur > 0:
                spec_parts.append(f"⏱ مدت: {to_persian_digits(dur)} روز")
            if users > 0:
                spec_parts.append(f"👥 کاربر: {to_persian_digits(users)}")
            spec_line = " | ".join(spec_parts)
        else:
            item_desc = "🛍 خرید اشتراک جدید"
            vol_str = await format_volume_label(gb, with_fair_policy=True)
            spec_parts = []
            if gb > 0 or await is_unlimited_volume(gb):
                spec_parts.append(f"📊 حجم: {vol_str}")
            if dur > 0:
                spec_parts.append(f"⏱ مدت: {to_persian_digits(dur)} روز")
            if users > 0:
                spec_parts.append(f"👥 کاربر: {to_persian_digits(users)}")
            spec_line = " | ".join(spec_parts)

        card_lines = [
            f"🔹 <b>فاکتور #{inv_id}</b> | {status_str}",
            f"   بابت: {item_desc}",
        ]
        if spec_line:
            card_lines.append(f"   مشخصات: {spec_line}")

        card_lines.append(f"   💰 مبلغ: <b>{amount}</b> ({pm_str})")

        discount_code = inv.get("discount_code")
        orig_amount = inv.get("original_amount")
        if discount_code:
            disc_str = f"🏷 تخفیف: <code>{discount_code}</code>"
            if orig_amount and orig_amount > inv.get("amount", 0):
                disc_str += f" (مبلغ اولیه: {format_price(orig_amount)})"
            card_lines.append(f"   {disc_str}")

        card_lines.append(f"   📅 تاریخ: <code>{date_str}</code>")

        if inv.get("processed_at") and status in ("approved", "paid", "rejected"):
            proc_dt = format_datetime(inv.get("processed_at"))
            lbl = "زمان تأیید" if status in ("approved", "paid") else "زمان رد"
            card_lines.append(f"   ⏱ {lbl}: <code>{proc_dt}</code>")

        card_lines.append("")
        lines.append("\n".join(card_lines))

    nav_btns = []
    if page > 0:
        nav_btns.append(
            InlineKeyboardButton(
                text="◀️ قبلی",
                callback_data=f"admin_user_invoices_{u_id}_{page - 1}",
            )
        )
    nav_btns.append(
        InlineKeyboardButton(
            text=f"صفحه {to_persian_digits(page + 1)} از {to_persian_digits(total_pages)}",
            callback_data="admin_users_list_noop",
        )
    )
    if page < total_pages - 1:
        nav_btns.append(
            InlineKeyboardButton(
                text="بعدی ▶️",
                callback_data=f"admin_user_invoices_{u_id}_{page + 1}",
            )
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            nav_btns,
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به مدیریت کاربر",
                    callback_data=f"admin_manage_user_{u_id}_back",
                )
            ],
        ]
    )

    text = "\n".join(lines)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


async def _notify_user_reserved(bot: Bot | None, tg_id: int, text: str) -> bool:
    if not bot or tg_id <= 0:
        return False
    try:
        await bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
        return True
    except Exception as e:
        logger.warning("Failed to notify user %s about reserved renewal: %s", tg_id, e)
        return False


def _parse_int_input(raw: str | None, min_val: int = 1) -> int | None:
    if not raw:
        return None
    try:
        val = int(persian_to_english_digits(raw.strip()))
    except ValueError:
        return None
    return val if val >= min_val else None


async def _render_user_res_panel(
    event: types.CallbackQuery | types.Message,
    u_id: int,
    state: FSMContext,
    page: int = 0,
    notice: str = "",
) -> None:
    import math

    from db.models import (
        get_reserved_renewals_by_user,
        get_user_reserved_renewal_logs_paginated,
    )

    page_size = 5
    user = await get_user(u_id)
    full_name = user.get("full_name") or str(u_id) if user else str(u_id)

    pending_res = await get_reserved_renewals_by_user(u_id)
    _, total_logs = await get_user_reserved_renewal_logs_paginated(
        u_id, offset=0, limit=1
    )
    total_pages = max(1, math.ceil(total_logs / page_size))
    page = max(0, min(page, total_pages - 1))
    logs, total_logs = await get_user_reserved_renewal_logs_paginated(
        u_id, offset=page * page_size, limit=page_size
    )

    lines: list[str] = []
    if notice:
        lines.append(f"{notice}\n")
    lines.append(
        f"📦 <b>مدیریت و لاگ بسته‌های رزرو کاربر {full_name}</b> (<code>{u_id}</code>)\n"
    )

    lines.append(
        f"⏳ <b>بسته‌های رزرو در انتظار اعمال ({to_persian_digits(len(pending_res))} مورد):</b>"
    )
    rows: list[list[InlineKeyboardButton]] = []
    if not pending_res:
        lines.append("<i>در حال حاضر بسته رزرو در انتظاری وجود ندارد.</i>\n")
    else:
        for pr in pending_res:
            pr_id = pr["id"]
            pr_email = pr.get("email", "")
            pr_dur = pr.get("duration_days", 0)
            pr_gb = pr.get("data_gb", 0)
            pr_users = pr.get("users_count", 1)
            pr_created = (
                format_datetime(pr.get("created_at"))
                if pr.get("created_at")
                else "نامشخص"
            )
            pr_vol = await format_volume_label(pr_gb, with_fair_policy=True)
            inv_str = f" | فاکتور: #{pr['invoice_id']}" if pr.get("invoice_id") else ""
            lines.append(
                f"🔹 سرویس: <code>{pr_email}</code>{inv_str}\n"
                f"   مشخصات: 📊 {pr_vol} | ⏱ {to_persian_digits(pr_dur)} روز | 👥 {to_persian_digits(pr_users)} کاربر\n"
                f"   📅 تاریخ ثبت: <code>{pr_created}</code>\n"
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"⚙️ مدیریت رزرو «{pr_email}»",
                        callback_data=f"admin_res_item_{pr_id}",
                    )
                ]
            )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ افزودن بسته رزرو جدید",
                callback_data=f"admin_res_new_{u_id}",
            )
        ]
    )

    lines.append(
        f"📋 <b>تاریخچه بسته‌های فعال‌شده ({to_persian_digits(total_logs)} مورد):</b>"
    )
    if not logs:
        lines.append("<i>هنوز هیچ بسته رزروی برای این کاربر فعال نشده است.</i>\n")
    else:
        for log in logs:
            log_email = log.get("email", "")
            log_dur = log.get("duration_days", 0)
            log_gb = log.get("data_gb", 0)
            log_users = log.get("users_count", 1)
            act_type = log.get("activation_type", "auto")
            if act_type == "early":
                type_badge = "⚡️ زودهنگام توسط کاربر"
            elif act_type == "admin":
                type_badge = "👨‍💼 دستی توسط مدیریت"
            else:
                type_badge = "🟢 خودکار پس از انقضا"

            act_time = (
                format_datetime(log.get("activated_at"))
                if log.get("activated_at")
                else "نامشخص"
            )
            res_time = (
                format_datetime(log.get("created_at"))
                if log.get("created_at")
                else "نامشخص"
            )
            log_vol = await format_volume_label(log_gb, with_fair_policy=True)

            inv_str = (
                f" | فاکتور: #{log['invoice_id']}" if log.get("invoice_id") else ""
            )
            lines.append(
                f"✅ سرویس: <code>{log_email}</code>{inv_str}\n"
                f"   نحوه فعال‌سازی: <b>{type_badge}</b>\n"
                f"   مشخصات: 📊 {log_vol} | ⏱ {to_persian_digits(log_dur)} روز | 👥 {to_persian_digits(log_users)} کاربر\n"
                f"   ⏱ فعال‌سازی: <code>{act_time}</code> | 📅 رزرو: <code>{res_time}</code>\n"
            )

    nav_btns: list[InlineKeyboardButton] = []
    if page > 0:
        nav_btns.append(
            InlineKeyboardButton(
                text="◀️ قبلی",
                callback_data=f"admin_user_res_logs_{u_id}_{page - 1}",
            )
        )
    if total_pages > 1 or page > 0:
        nav_btns.append(
            InlineKeyboardButton(
                text=f"صفحه {to_persian_digits(page + 1)} از {to_persian_digits(total_pages)}",
                callback_data="admin_users_list_noop",
            )
        )
    if page < total_pages - 1:
        nav_btns.append(
            InlineKeyboardButton(
                text="بعدی ▶️",
                callback_data=f"admin_user_res_logs_{u_id}_{page + 1}",
            )
        )

    if nav_btns:
        rows.append(nav_btns)
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به مدیریت کاربر",
                callback_data=f"admin_manage_user_{u_id}_back",
            )
        ]
    )

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    text = "\n".join(lines)
    if isinstance(event, types.CallbackQuery):
        await safe_edit_text(event.message, text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


async def _render_res_item(
    event: types.CallbackQuery | types.Message,
    res_id: int,
    state: FSMContext,
    notice: str = "",
) -> None:
    from db.models import get_reserved_renewal_by_id

    rec = await get_reserved_renewal_by_id(res_id)
    if not rec or rec.get("status") != "pending":
        msg = "❌ این بسته رزرو دیگر در انتظار نیست (فعال یا حذف شده است)."
        if rec and rec.get("tg_id"):
            await _render_user_res_panel(
                event, int(rec["tg_id"]), state, page=0, notice=msg
            )
            return
        if isinstance(event, types.CallbackQuery):
            await event.answer(msg, show_alert=True)
        else:
            await event.answer(msg)
        return

    email = rec["email"]
    u_id = int(rec["tg_id"])
    vol_label = await format_volume_label(
        int(rec.get("data_gb", 0)), with_fair_policy=True
    )
    dur = int(rec.get("duration_days", 0))
    users_cnt = int(rec.get("users_count", 1) or 1)
    created_str = (
        format_datetime(rec.get("created_at")) if rec.get("created_at") else "نامشخص"
    )
    inv_str = (
        f"\n🧾 <b>فاکتور مرتبط:</b> #{rec['invoice_id']}"
        if rec.get("invoice_id")
        else ""
    )

    notice_block = f"{notice}\n\n" if notice else ""
    text = (
        f"{notice_block}"
        f"📦 <b>مدیریت بسته رزرو سرویس <code>{email}</code></b>\n\n"
        f"📊 <b>حجم بسته:</b> {vol_label}\n"
        f"⏱ <b>مدت اعتبار:</b> {to_persian_digits(dur)} روز\n"
        f"👥 <b>سقف کاربر:</b> {to_persian_digits(users_cnt)} کاربر\n"
        f"📅 <b>تاریخ ثبت رزرو:</b> <code>{created_str}</code>"
        f"{inv_str}\n\n"
        f"💡 <i>این بسته به محض پایان حجم یا زمان سرویس فعلی به‌صورت خودکار فعال می‌شود.</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 ویرایش حجم",
                    callback_data=f"admin_res_edit_gb_{res_id}",
                ),
                InlineKeyboardButton(
                    text="⏱ ویرایش مدت",
                    callback_data=f"admin_res_edit_dur_{res_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 ویرایش سقف کاربر",
                    callback_data=f"admin_res_edit_usr_{res_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚡️ فعال‌سازی آنی",
                    callback_data=f"admin_res_act_{res_id}",
                ),
                InlineKeyboardButton(
                    text="🗑 حذف رزرو",
                    callback_data=f"admin_res_del_{res_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به لیست رزروها",
                    callback_data=f"admin_user_res_logs_{u_id}_0",
                )
            ],
        ]
    )

    if isinstance(event, types.CallbackQuery):
        await safe_edit_text(event.message, text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_user_res_logs_"))
async def admin_user_res_logs(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    parts = callback.data.split("_")
    if len(parts) < 5:
        await callback.answer("خطای داده نامعتبر.", show_alert=True)
        return

    await _clear_fsm_keep_nav(state)
    u_id = int(parts[4])
    page = int(parts[5]) if len(parts) > 5 else 0
    await _render_user_res_panel(callback, u_id, state, page=page)


@router.callback_query(F.data.startswith("admin_res_item_"))
async def admin_res_item_view(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    await _clear_fsm_keep_nav(state)
    try:
        res_id = int(callback.data[len("admin_res_item_") :])
    except ValueError:
        await callback.answer("شناسه رزرو نامعتبر است.", show_alert=True)
        return

    from db.models import get_reserved_renewal_by_id

    rec = await get_reserved_renewal_by_id(res_id)
    if not rec or rec.get("status") != "pending":
        await callback.answer("❌ این بسته رزرو دیگر در انتظار نیست.", show_alert=True)
        if rec and rec.get("tg_id"):
            await _render_user_res_panel(callback, int(rec["tg_id"]), state, page=0)
        return

    await _render_res_item(callback, res_id, state)


# ─── Create Reserved Renewal ──────────────────────────────────────────────────


@router.callback_query(F.data.startswith("admin_res_new_"))
async def admin_res_new_select_sub(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    u_id = int(callback.data[len("admin_res_new_") :])
    clients = await xui_api.get_normalized_clients_by_tg_id(u_id)
    if not clients:
        await callback.answer(
            "❌ این کاربر هیچ اشتراکی در سرور ندارد.", show_alert=True
        )
        return

    from db.models import get_reserved_renewals_by_user

    pending = await get_reserved_renewals_by_user(u_id)
    pending_emails = {p["email"] for p in pending}

    emails = [c.get("email", "") for c in clients if c.get("email")]
    await state.update_data(res_add_emails=emails, res_add_tg_id=u_id)

    rows: list[list[InlineKeyboardButton]] = []
    for idx, c in enumerate(clients):
        em = c.get("email", "")
        if not em:
            continue
        badge = " 📦 (دارای رزرو)" if em in pending_emails else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔹 {em}{badge}",
                    callback_data=f"admin_res_pick_{u_id}_{idx}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="❌ انصراف و بازگشت",
                callback_data=f"admin_user_res_logs_{u_id}_0",
            )
        ]
    )

    await safe_edit_text(
        callback.message,
        f"➕ <b>افزودن بسته رزرو جدید برای کاربر <code>{u_id}</code></b>\n\n"
        "سرویس مورد نظر را انتخاب کنید:\n"
        "<i>(اگر سرویسی از قبل بسته رزرو داشته باشد، بسته جدید جایگزین آن می‌شود.)</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_res_pick_"))
async def admin_res_pick_sub(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    parts = callback.data.split("_")
    u_id = int(parts[3])
    idx = int(parts[4])

    data = await state.get_data()
    emails = data.get("res_add_emails") or []
    email = emails[idx] if 0 <= idx < len(emails) else None
    if not email:
        clients = await xui_api.get_normalized_clients_by_tg_id(u_id)
        if 0 <= idx < len(clients):
            email = clients[idx].get("email")
    if not email:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    client = await xui_api.get_client(email)
    curr_users = max(1, int((client or {}).get("limitIp") or 1))

    await state.update_data(
        res_add_tg_id=u_id,
        res_add_email=email,
        res_add_default_users=curr_users,
    )
    await state.set_state(AdminSearchStates.waiting_res_gb)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_user_res_logs_{u_id}_0",
                )
            ]
        ]
    )
    await safe_edit_text(
        callback.message,
        f"➕ <b>بسته رزرو برای سرویس <code>{email}</code></b>\n\n"
        "<b>مرحله ۱ از ۳:</b> حجم بسته رزرو را به گیگابایت وارد کنید:\n"
        "• مثال: <code>50</code>\n"
        "• برای حجم نامحدود عدد <code>0</code> را بفرستید.\n\n"
        "💡 <i>برای انصراف می‌توانید /cancel بفرستید یا دکمه زیر را بزنید.</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_res_gb, F.text)
async def admin_res_gb_input(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    u_id = int(data.get("res_add_tg_id") or 0)

    if message.text and message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        await _render_user_res_panel(
            message, u_id, state, notice="❌ ساخت بسته رزرو لغو شد."
        )
        return

    gb_val = _parse_int_input(message.text, min_val=0)
    if gb_val is None:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف", callback_data=f"admin_user_res_logs_{u_id}_0"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً یک عدد صحیح معتبر (۰ یا بیشتر) وارد کنید.",
            reply_markup=kb,
        )
        return

    await state.update_data(res_add_gb=gb_val)
    await state.set_state(AdminSearchStates.waiting_res_dur)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_user_res_logs_{u_id}_0",
                )
            ]
        ]
    )
    await message.answer(
        "<b>مرحله ۲ از ۳:</b> مدت اعتبار بسته رزرو را به روز وارد کنید:\n"
        "مثال: <code>30</code>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.message(AdminSearchStates.waiting_res_dur, F.text)
async def admin_res_dur_input(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    u_id = int(data.get("res_add_tg_id") or 0)

    if message.text and message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        await _render_user_res_panel(
            message, u_id, state, notice="❌ ساخت بسته رزرو لغو شد."
        )
        return

    dur_val = _parse_int_input(message.text, min_val=1)
    if dur_val is None:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف", callback_data=f"admin_user_res_logs_{u_id}_0"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً تعداد روز معتبر (بزرگ‌تر از صفر) وارد کنید.",
            reply_markup=kb,
        )
        return

    default_users = int(data.get("res_add_default_users") or 1)
    await state.update_data(res_add_dur=dur_val)
    await state.set_state(AdminSearchStates.waiting_res_users)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ استفاده از سقف فعلی ({to_persian_digits(default_users)} کاربر)",
                    callback_data="admin_res_usr_keep",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_user_res_logs_{u_id}_0",
                )
            ],
        ]
    )
    await message.answer(
        "<b>مرحله ۳ از ۳:</b> سقف کاربر همزمان را وارد کنید یا از دکمه زیر سقف فعلی سرویس را تایید کنید:\n"
        "مثال: <code>2</code>",
        reply_markup=kb,
        parse_mode="HTML",
    )


async def _show_res_add_confirm(
    event: types.CallbackQuery | types.Message,
    state: FSMContext,
    users_val: int,
) -> None:
    from db.models import get_reserved_renewal

    data = await state.get_data()
    u_id = int(data.get("res_add_tg_id") or 0)
    email = data.get("res_add_email", "")
    gb_val = int(data.get("res_add_gb") or 0)
    dur_val = int(data.get("res_add_dur") or 30)

    await state.update_data(res_add_users=users_val)
    await state.set_state(None)

    existing = await get_reserved_renewal(email)
    replace_warn = (
        "\n⚠️ <i>این سرویس یک بسته رزرو در انتظار دارد؛ با ثبت، بسته جدید جایگزین می‌شود.</i>"
        if existing
        else ""
    )
    vol_label = await format_volume_label(gb_val, with_fair_policy=True)
    text = (
        f"📋 <b>تایید ثبت بسته رزرو جدید</b>\n\n"
        f"🔹 <b>سرویس:</b> <code>{email}</code>\n"
        f"📊 <b>حجم:</b> {vol_label}\n"
        f"⏱ <b>مدت:</b> {to_persian_digits(dur_val)} روز\n"
        f"👥 <b>سقف کاربر:</b> {to_persian_digits(users_val)} کاربر"
        f"{replace_warn}\n\n"
        "نحوه ثبت را انتخاب کنید:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ ثبت رزرو (بی‌صدا)", callback_data="admin_res_save_0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ ثبت + 📩 اطلاع به کاربر", callback_data="admin_res_save_1"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data=f"admin_user_res_logs_{u_id}_0"
                )
            ],
        ]
    )
    if isinstance(event, types.CallbackQuery):
        await safe_edit_text(event.message, text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "admin_res_usr_keep")
async def admin_res_usr_keep_cb(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    data = await state.get_data()
    default_users = int(data.get("res_add_default_users") or 1)
    await _show_res_add_confirm(callback, state, default_users)


@router.message(AdminSearchStates.waiting_res_users, F.text)
async def admin_res_users_input(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    u_id = int(data.get("res_add_tg_id") or 0)

    if message.text and message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        await _render_user_res_panel(
            message, u_id, state, notice="❌ ساخت بسته رزرو لغو شد."
        )
        return

    users_val = _parse_int_input(message.text, min_val=1)
    if users_val is None:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف", callback_data=f"admin_user_res_logs_{u_id}_0"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ لطفاً تعداد کاربر معتبر (۱ یا بیشتر) وارد کنید.",
            reply_markup=kb,
        )
        return

    await _show_res_add_confirm(message, state, users_val)


@router.callback_query(F.data.startswith("admin_res_save_"))
async def admin_res_add_save(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    notify = callback.data.endswith("_1")
    data = await state.get_data()
    u_id = int(data.get("res_add_tg_id") or 0)
    email = data.get("res_add_email")
    gb_val = data.get("res_add_gb")
    dur_val = data.get("res_add_dur")
    users_val = int(data.get("res_add_users") or 1)

    if not email or gb_val is None or not dur_val or not u_id:
        await callback.answer("❌ اطلاعات رزرو منقضی شده است.", show_alert=True)
        return

    from db.models import create_reserved_renewal

    await create_reserved_renewal(
        email=email,
        tg_id=u_id,
        duration_days=int(dur_val),
        data_gb=int(gb_val),
        users_count=users_val,
    )

    notify_note = ""
    if notify:
        vol_label = await format_volume_label(int(gb_val), with_fair_policy=True)
        sent = await _notify_user_reserved(
            callback.bot,
            u_id,
            f"📦 <b>یک بسته تمدید رزرو جدید برای سرویس شما ثبت شد!</b>\n\n"
            f"🔹 <b>نام سرویس:</b> <code>{email}</code>\n"
            f"📊 <b>حجم:</b> {vol_label}\n"
            f"⏱ <b>مدت اعتبار:</b> {to_persian_digits(int(dur_val))} روز\n"
            f"👥 <b>سقف کاربر:</b> {to_persian_digits(users_val)} کاربر\n\n"
            f"💡 <i>این بسته پس از اتمام حجم یا زمان سرویس فعلی به‌صورت خودکار فعال می‌شود.</i>",
        )
        notify_note = (
            " (پیام به کاربر ارسال شد)" if sent else " (ارسال پیام به کاربر ناموفق بود)"
        )

    await _clear_fsm_keep_nav(state)
    await _render_user_res_panel(
        callback,
        u_id,
        state,
        notice=f"✅ <b>بسته رزرو برای «{email}» ثبت شد{notify_note}.</b>",
    )


# ─── Edit Reserved Renewal ────────────────────────────────────────────────────

_RES_EDIT_META = {
    "gb": ("data_gb", "حجم (گیگابایت)", 0, "۰ برای نامحدود، یا عدد مثبت مثل ۵۰"),
    "dur": ("duration_days", "مدت اعتبار (روز)", 1, "عدد بزرگ‌تر از صفر مثل ۳۰"),
    "usr": ("users_count", "سقف کاربر همزمان", 1, "عدد ۱ یا بیشتر مثل ۲"),
}


@router.callback_query(F.data.startswith("admin_res_edit_"))
async def admin_res_edit_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    payload = callback.data[len("admin_res_edit_") :]
    field_key, id_str = payload.split("_", 1)
    if field_key not in _RES_EDIT_META or not id_str.isdigit():
        await callback.answer("خطای داده نامعتبر.", show_alert=True)
        return

    res_id = int(id_str)
    from db.models import get_reserved_renewal_by_id

    rec = await get_reserved_renewal_by_id(res_id)
    if not rec or rec.get("status") != "pending":
        await callback.answer("❌ بسته رزرو در انتظار یافت نشد.", show_alert=True)
        return

    col, label, _, hint = _RES_EDIT_META[field_key]
    await state.update_data(res_edit_id=res_id, res_edit_field=field_key)
    await state.set_state(AdminSearchStates.waiting_res_edit_val)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"admin_res_item_{res_id}",
                )
            ]
        ]
    )
    await safe_edit_text(
        callback.message,
        f"✏️ <b>ویرایش {label} برای بسته رزرو <code>{rec['email']}</code></b>\n\n"
        f"مقدار فعلی: <code>{to_persian_digits(rec.get(col, 0))}</code>\n"
        f"مقدار جدید را وارد کنید ({hint}):",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_res_edit_val, F.text)
async def admin_res_edit_val_input(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    res_id = int(data.get("res_edit_id") or 0)
    field_key = data.get("res_edit_field", "")

    if message.text and message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        await _render_res_item(message, res_id, state, notice="❌ ویرایش لغو شد.")
        return

    if field_key not in _RES_EDIT_META:
        await _clear_fsm_keep_nav(state)
        return

    _, label, min_val, hint = _RES_EDIT_META[field_key]
    val = _parse_int_input(message.text, min_val=min_val)
    if val is None:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف", callback_data=f"admin_res_item_{res_id}"
                    )
                ]
            ]
        )
        await message.answer(
            f"❌ مقدار نامعتبر است. لطفاً عدد صحیح معتبر وارد کنید ({hint}).",
            reply_markup=kb,
        )
        return

    await state.update_data(res_edit_new_val=val)
    await state.set_state(None)

    val_display = (
        await format_volume_label(val, with_fair_policy=True)
        if field_key == "gb"
        else to_persian_digits(val)
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ ذخیره (بی‌صدا)", callback_data="admin_res_esave_0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ ذخیره + 📩 اطلاع به کاربر",
                    callback_data="admin_res_esave_1",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data=f"admin_res_item_{res_id}"
                )
            ],
        ]
    )
    await message.answer(
        f"✏️ <b>تایید تغییر {label}:</b>\n\n"
        f"مقدار جدید: <b>{val_display}</b>\n"
        "چطور ثبت شود؟",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_res_esave_"))
async def admin_res_edit_save(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    notify = callback.data.endswith("_1")
    data = await state.get_data()
    res_id = int(data.get("res_edit_id") or 0)
    field_key = data.get("res_edit_field", "")
    new_val = data.get("res_edit_new_val")

    if not res_id or field_key not in _RES_EDIT_META or new_val is None:
        await callback.answer("❌ اطلاعات ویرایش منقضی شده است.", show_alert=True)
        return

    from db.models import get_reserved_renewal_by_id, update_reserved_renewal

    col, label, _, _ = _RES_EDIT_META[field_key]
    await update_reserved_renewal(res_id, **{col: int(new_val)})
    rec = await get_reserved_renewal_by_id(res_id)

    notify_note = ""
    if notify and rec:
        vol_label = await format_volume_label(
            int(rec.get("data_gb", 0)), with_fair_policy=True
        )
        sent = await _notify_user_reserved(
            callback.bot,
            int(rec["tg_id"]),
            f"✏️ <b>مشخصات بسته رزرو سرویس شما توسط مدیریت ویرایش شد:</b>\n\n"
            f"🔹 <b>نام سرویس:</b> <code>{rec['email']}</code>\n"
            f"📊 <b>حجم:</b> {vol_label}\n"
            f"⏱ <b>مدت اعتبار:</b> {to_persian_digits(int(rec.get('duration_days', 0)))} روز\n"
            f"👥 <b>سقف کاربر:</b> {to_persian_digits(int(rec.get('users_count', 1) or 1))} کاربر",
        )
        notify_note = (
            " (به کاربر اطلاع داده شد)" if sent else " (ارسال پیام به کاربر ناموفق بود)"
        )

    await _clear_fsm_keep_nav(state)
    await _render_res_item(
        callback,
        res_id,
        state,
        notice=f"✅ <b>{label} با موفقیت ویرایش شد{notify_note}.</b>",
    )


# ─── Instant Activate & Delete from Reserved Panel ────────────────────────────


@router.callback_query(F.data.startswith("admin_res_act_"))
async def admin_res_activate_ask(callback: types.CallbackQuery) -> None:
    if not await _is_admin(callback):
        return

    res_id = int(callback.data[len("admin_res_act_") :])
    from db.models import get_reserved_renewal_by_id

    rec = await get_reserved_renewal_by_id(res_id)
    if not rec or rec.get("status") != "pending":
        await callback.answer("❌ این بسته رزرو در انتظار نیست.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡️ بله، همین الان فعال شود",
                    callback_data=f"admin_res_actgo_{res_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data=f"admin_res_item_{res_id}",
                )
            ],
        ]
    )
    await safe_edit_text(
        callback.message,
        f"⚡️ <b>فعال‌سازی آنی بسته رزرو</b>\n\n"
        f"آیا مطمئن هستید که بسته رزرو سرویس <code>{rec['email']}</code> همین الان فعال شود؟\n"
        "<i>(با این کار ترافیک فعلی صفر، مشخصات جدید روی سرویس اعمال و پیام فعال‌سازی به کاربر ارسال می‌شود.)</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_res_actgo_"))
async def admin_res_activate_go(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    res_id = int(callback.data[len("admin_res_actgo_") :])
    from db.models import get_reserved_renewal_by_id
    from services.renewal_service import activate_reserved_renewal

    rec = await get_reserved_renewal_by_id(res_id)
    if not rec or rec.get("status") != "pending":
        await callback.answer("❌ بسته رزرو در انتظار یافت نشد.", show_alert=True)
        return

    email = rec["email"]
    u_id = int(rec["tg_id"])
    res = await activate_reserved_renewal(
        email, force=True, bot=callback.bot, activation_type="admin"
    )
    if res.get("status") == "activated":
        notice = f"✅ <b>بسته رزرو «{email}» با موفقیت فعال شد.</b>"
    elif res.get("status") == "no_reserved":
        notice = "❌ <b>بسته رزروی برای این اشتراک یافت نشد.</b>"
    else:
        notice = f"❌ <b>خطا در فعال‌سازی:</b> {res.get('msg', 'ناشناخته')}"

    await _render_user_res_panel(callback, u_id, state, page=0, notice=notice)


@router.callback_query(F.data.startswith("admin_res_del_"))
async def admin_res_delete_ask(callback: types.CallbackQuery) -> None:
    if not await _is_admin(callback):
        return

    res_id = int(callback.data[len("admin_res_del_") :])
    from db.models import get_reserved_renewal_by_id

    rec = await get_reserved_renewal_by_id(res_id)
    if not rec or rec.get("status") != "pending":
        await callback.answer("❌ این بسته رزرو در انتظار نیست.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 حذف (بی‌صدا)",
                    callback_data=f"admin_res_delgo_{res_id}_0",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 حذف + 📩 اطلاع به کاربر",
                    callback_data=f"admin_res_delgo_{res_id}_1",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data=f"admin_res_item_{res_id}",
                )
            ],
        ]
    )
    await safe_edit_text(
        callback.message,
        f"🗑 <b>حذف بسته رزرو</b>\n\n"
        f"آیا از حذف بسته رزرو سرویس <code>{rec['email']}</code> مطمئن هستید؟\n"
        "<i>(این کار موجودی کیف پول کاربر را تغییر نمی‌دهد.)</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_res_delgo_"))
async def admin_res_delete_go(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    payload = callback.data[len("admin_res_delgo_") :]
    id_str, notify_flag = payload.split("_", 1)
    res_id = int(id_str)
    notify = notify_flag == "1"

    from db.models import delete_reserved_renewal, get_reserved_renewal_by_id

    rec = await get_reserved_renewal_by_id(res_id)
    if not rec or rec.get("status") != "pending":
        await callback.answer("❌ بسته رزرو در انتظار یافت نشد.", show_alert=True)
        return

    email = rec["email"]
    u_id = int(rec["tg_id"])
    await delete_reserved_renewal(email)

    notify_note = ""
    if notify:
        sent = await _notify_user_reserved(
            callback.bot,
            u_id,
            f"🗑 <b>بسته تمدید رزرو سرویس <code>{email}</code> توسط مدیریت لغو و حذف شد.</b>\n"
            "در صورت نیاز می‌توانید با پشتیبانی در ارتباط باشید.",
        )
        notify_note = (
            " (به کاربر اطلاع داده شد)" if sent else " (ارسال پیام به کاربر ناموفق بود)"
        )

    await _render_user_res_panel(
        callback,
        u_id,
        state,
        page=0,
        notice=f"🗑 <b>بسته رزرو «{email}» حذف شد{notify_note}.</b>",
    )


@router.callback_query(F.data.startswith("admin_user_subs_"))
async def admin_user_subs(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    parts = callback.data.split("_")
    tg_id = int(parts[3])
    page = int(parts[4]) if len(parts) >= 5 else 0

    clients = await xui_api.get_normalized_clients_by_tg_id(tg_id)

    if not clients:
        await callback.answer(
            "❌ این کاربر هیچ اشتراکی در سرور ندارد.", show_alert=True
        )
        return

    if len(clients) == 1:
        email = clients[0]["email"]
        await state.update_data(
            sub_back_callback=f"admin_manage_user_{tg_id}_back",
            manage_user_id=tg_id,
        )
        await _render_sub_dashboard(callback, email, state)
        return

    import math

    from keyboards.inline_kb import admin_user_subs_list_keyboard

    page_size = 5
    total_clients = len(clients)
    total_pages = max(1, math.ceil(total_clients / page_size))
    page = max(0, min(page, total_pages - 1))

    start_idx = page * page_size
    page_clients = clients[start_idx : start_idx + page_size]

    emails = [c.get("email") for c in clients if c.get("email")]
    await state.update_data(
        user_subs_emails=emails,
        sub_back_callback=f"admin_user_subs_{tg_id}_{page}",
        manage_user_id=tg_id,
    )

    keyboard = admin_user_subs_list_keyboard(
        page_clients, tg_id, page, total_pages, page_size=page_size
    )
    text = (
        f"📱 <b>اشتراک‌های کاربر <code>{tg_id}</code> در سرور:</b>\n"
        f"تعداد کل اشتراک‌ها: <b>{to_persian_digits(total_clients)}</b> مورد\n\n"
        "جهت مشاهده جزئیات و مدیریت هر سرویس، آن را انتخاب کنید:"
    )

    await safe_edit_text(
        callback.message, text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_sub_idx_"))
async def admin_user_sub_idx_select(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    try:
        idx = int(callback.data[len("admin_user_sub_idx_") :])
    except ValueError:
        await callback.answer("خطای نامعتبر بودن اشتراک.", show_alert=True)
        return

    data = await state.get_data()
    emails = data.get("user_subs_emails")
    tg_id = data.get("manage_user_id")

    email: str | None = None
    if emails and 0 <= idx < len(emails):
        email = emails[idx]
    elif tg_id:
        clients = await xui_api.get_normalized_clients_by_tg_id(tg_id)
        if 0 <= idx < len(clients):
            email = clients[idx].get("email")

    if not email:
        await callback.answer("❌ اشتراک مورد نظر یافت نشد.", show_alert=True)
        return

    await _render_sub_dashboard(callback, email, state)


@router.callback_query(F.data.startswith("admin_user_subsel_"))
async def admin_user_subsel(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    payload = callback.data[len("admin_user_subsel_") :]
    parts = payload.rsplit("_", 1)
    page = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0
    main_part = parts[0]
    tg_parts = main_part.split("_", 1)
    tg_id = int(tg_parts[0]) if tg_parts[0].isdigit() else 0
    email = tg_parts[1] if len(tg_parts) == 2 else main_part

    await state.update_data(
        sub_back_callback=f"admin_user_subs_{tg_id}_{page}",
        manage_user_id=tg_id,
    )
    await _render_sub_dashboard(callback, email, state)


# ─── Clients By Creation Date ────────────────────────────────────────────────

_CDATE_PRESETS: dict[str, tuple[int, str]] = {
    "1h": (3_600_000, "۱ ساعت اخیر"),
    "6h": (6 * 3_600_000, "۶ ساعت اخیر"),
    "24h": (24 * 3_600_000, "۲۴ ساعت اخیر"),
    "7d": (7 * 86_400_000, "۷ روز اخیر"),
    "30d": (30 * 86_400_000, "۳۰ روز اخیر"),
    "all": (0, "همه کانفیگ‌ها"),
}

_CDATE_KIND_CYCLE = ("all", "main", "test")
_CDATE_KIND_LABELS = {
    "all": "همه",
    "main": "فقط اصلی",
    "test": "فقط تست",
}


def _to_ms(raw: Any) -> int:
    try:
        val = int(float(raw or 0))
    except (TypeError, ValueError):
        return 0
    if val <= 0:
        return 0
    return val * 1000 if val < 100_000_000_000 else val


def _parse_cdate_input(raw: str | None) -> dict[str, Any] | None:
    import re
    from datetime import timedelta

    import jdatetime

    if not raw or not raw.strip():
        return None

    txt = persian_to_english_digits(raw.strip())
    tz_iran = timezone(timedelta(hours=3, minutes=30))

    date_matches = re.findall(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", txt)
    if date_matches:
        if len(date_matches) > 2:
            return None
        parsed_dates: list[jdatetime.datetime] = []
        for dm in date_matches:
            parts = re.split(r"[/-]", dm)
            try:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                parsed_dates.append(
                    jdatetime.datetime(y, m, d, 0, 0, 0, tzinfo=tz_iran)
                )
            except ValueError:
                return None

        if len(parsed_dates) == 1:
            d1 = d2 = parsed_dates[0]
            label = f"تاریخ {to_persian_digits(d1.strftime('%Y/%m/%d'))}"
        else:
            d1, d2 = sorted(parsed_dates)
            label = (
                f"از {to_persian_digits(d1.strftime('%Y/%m/%d'))} "
                f"تا {to_persian_digits(d2.strftime('%Y/%m/%d'))}"
            )

        start_ms = int(d1.togregorian().timestamp() * 1000)
        end_dt = d2 + jdatetime.timedelta(days=1)
        end_ms = int(end_dt.togregorian().timestamp() * 1000) - 1
        return {"rel_ms": 0, "start_ms": start_ms, "end_ms": end_ms, "label": label}

    m = re.match(
        r"^(\d+(?:\.\d+)?)\s*(m|min|دقیقه|h|hr|ساعت|d|day|روز|w|week|هفته)?$",
        txt.lower(),
    )
    if not m:
        return None

    val = float(m.group(1))
    unit = m.group(2) or "h"
    if val <= 0:
        return None

    val_str = to_persian_digits(int(val) if val.is_integer() else val)
    if unit in ("m", "min", "دقیقه"):
        rel_ms = int(val * 60_000)
        label = f"{val_str} دقیقه اخیر"
    elif unit in ("d", "day", "روز"):
        rel_ms = int(val * 86_400_000)
        label = f"{val_str} روز اخیر"
    elif unit in ("w", "week", "هفته"):
        rel_ms = int(val * 7 * 86_400_000)
        label = f"{val_str} هفته اخیر"
    else:
        rel_ms = int(val * 3_600_000)
        label = f"{val_str} ساعت اخیر"

    return {"rel_ms": rel_ms, "start_ms": 0, "end_ms": 0, "label": label}


def _filter_and_sort_clients(
    clients: list[dict[str, Any]],
    flt: dict[str, Any],
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rel_ms = int(flt.get("rel_ms") or 0)
    start_ms = int(flt.get("start_ms") or 0)
    end_ms = int(flt.get("end_ms") or 0)
    kind = flt.get("kind", "all")
    sort_desc = flt.get("sort", "desc") == "desc"

    if rel_ms > 0:
        start_ms = now_ms - rel_ms
        end_ms = now_ms

    has_window = start_ms > 0 or end_ms > 0
    matched: list[tuple[int, int, dict[str, Any]]] = []
    for c in clients:
        email = c.get("email", "")
        if not email:
            continue
        is_test = email.endswith("_test")
        if kind == "test" and not is_test:
            continue
        if kind == "main" and is_test:
            continue

        c_ms = _to_ms(c.get("createdAt"))
        if has_window:
            if c_ms <= 0:
                continue
            if start_ms > 0 and c_ms < start_ms:
                continue
            if end_ms > 0 and c_ms > end_ms:
                continue

        matched.append((c_ms, int(c.get("id") or 0), c))

    matched.sort(key=lambda item: (item[0], item[1]), reverse=sort_desc)
    return [item[2] for item in matched]


async def _render_clients_by_date_list(
    event: types.CallbackQuery | types.Message,
    state: FSMContext,
    page: int = 0,
    notice: str = "",
) -> None:
    import math

    data = await state.get_data()
    flt = data.get("cdate_filter") or {
        "rel_ms": 0,
        "start_ms": 0,
        "end_ms": 0,
        "label": "همه کانفیگ‌ها",
        "sort": "desc",
        "kind": "all",
    }

    clients = await xui_api.list_clients()
    filtered = _filter_and_sort_clients(clients, flt)

    page_size = 6
    total = len(filtered)
    total_pages = max(1, math.ceil(total / page_size))
    page = max(0, min(page, total_pages - 1))

    start_idx = page * page_size
    page_items = filtered[start_idx : start_idx + page_size]

    await state.update_data(
        cdate_filter=flt,
        cdate_emails=[c.get("email", "") for c in page_items],
        sub_back_callback=f"admin_cdate_page_{page}",
        manage_user_id=None,
    )

    sort_dir = flt.get("sort", "desc")
    sort_label = "جدیدترین اول" if sort_dir == "desc" else "قدیمی‌ترین اول"
    kind_key = flt.get("kind", "all")
    kind_label = _CDATE_KIND_LABELS.get(kind_key, "همه")

    lines: list[str] = []
    if notice:
        lines.append(f"{notice}\n")
    lines.append(
        f"📅 <b>کانفیگ‌ها بر اساس تاریخ ساخت</b>\n"
        f"🕒 بازه: <b>{flt.get('label', 'همه')}</b> | 🔘 نوع: <b>{kind_label}</b>\n"
        f"🔢 تعداد: <b>{to_persian_digits(total)}</b> کانفیگ | ترتیب: <b>{sort_label}</b>\n"
    )

    rows: list[list[InlineKeyboardButton]] = []
    if not page_items:
        lines.append("<i>هیچ کانفیگی در این بازه زمانی یافت نشد.</i>")
    else:
        for idx, c in enumerate(page_items):
            num = start_idx + idx + 1
            email = c.get("email", "")
            c_ms = _to_ms(c.get("createdAt"))
            c_str = format_datetime(c_ms) if c_ms > 0 else "نامشخص"
            total_b = c.get("totalGB", 0) or 0
            used_b = (c.get("up", 0) or 0) + (c.get("down", 0) or 0)
            vol_str = format_size_persian_gb(total_b) if total_b > 0 else "نامحدود"
            used_str = format_detailed_traffic(used_b)
            tg_id = c.get("tgId", 0) or 0

            badges = []
            if email.endswith("_test"):
                badges.append("🎁 تست")
            if not c.get("enable", True):
                badges.append("🔴 غیرفعال")
            badge_str = f" ({' | '.join(badges)})" if badges else ""
            tg_str = f" | 👤 <code>{tg_id}</code>" if tg_id else ""

            lines.append(
                f"<b>{to_persian_digits(num)}.</b> <code>{email}</code>{badge_str}\n"
                f"   📅 ساخت: <code>{c_str}</code>{tg_str}\n"
                f"   📊 مصرف: <b>{used_str}</b> از <b>{vol_str}</b>\n"
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"⚙️ مدیریت: {email}",
                        callback_data=f"admin_cdate_sub_{page}_{idx}",
                    )
                ]
            )

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="◀️ قبلی", callback_data=f"admin_cdate_page_{page - 1}"
            )
        )
    if total_pages > 1:
        nav_row.append(
            InlineKeyboardButton(
                text=f"صفحه {to_persian_digits(page + 1)} از {to_persian_digits(total_pages)}",
                callback_data="admin_users_list_noop",
            )
        )
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="بعدی ▶️", callback_data=f"admin_cdate_page_{page + 1}"
            )
        )
    if nav_row:
        rows.append(nav_row)

    sort_btn_text = "⬇️ ترتیب: جدیدترین" if sort_dir == "desc" else "⬆️ ترتیب: قدیمی‌ترین"
    rows.append(
        [
            InlineKeyboardButton(text=sort_btn_text, callback_data="admin_cdate_sort"),
            InlineKeyboardButton(
                text=f"🔘 نوع: {kind_label}", callback_data="admin_cdate_kind"
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 بروزرسانی", callback_data=f"admin_cdate_page_{page}"
            ),
            InlineKeyboardButton(
                text="🕒 تغییر بازه", callback_data="admin_clients_by_date_menu"
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به مدیریت کاربران",
                callback_data="admin_sub_users_menu",
            )
        ]
    )

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    text = "\n".join(lines)
    if isinstance(event, types.CallbackQuery):
        await safe_edit_text(event.message, text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "admin_clients_by_date_menu")
async def admin_clients_by_date_menu(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    await _clear_fsm_keep_nav(state)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🕒 ۱ ساعت اخیر", callback_data="admin_cdate_preset_1h"
                ),
                InlineKeyboardButton(
                    text="🕕 ۶ ساعت اخیر", callback_data="admin_cdate_preset_6h"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📅 ۲۴ ساعت اخیر", callback_data="admin_cdate_preset_24h"
                ),
                InlineKeyboardButton(
                    text="📆 ۷ روز اخیر", callback_data="admin_cdate_preset_7d"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗓 ۳۰ روز اخیر", callback_data="admin_cdate_preset_30d"
                ),
                InlineKeyboardButton(
                    text="📋 همه کانفیگ‌ها", callback_data="admin_cdate_preset_all"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ وارد کردن دستی بازه", callback_data="admin_cdate_manual"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به مدیریت کاربران",
                    callback_data="admin_sub_users_menu",
                )
            ],
        ]
    )
    await safe_edit_text(
        callback.message,
        "📅 <b>مشاهده و مرتب‌سازی کانفیگ‌ها بر اساس تاریخ ساخت</b>\n\n"
        "یکی از بازه‌های آماده زیر را انتخاب کنید یا بازه دلخواهتان را دستی وارد کنید:",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_cdate_preset_"))
async def admin_cdate_preset(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return

    key = callback.data[len("admin_cdate_preset_") :]
    rel_ms, label = _CDATE_PRESETS.get(key, (0, "همه کانفیگ‌ها"))
    await state.update_data(
        cdate_filter={
            "rel_ms": rel_ms,
            "start_ms": 0,
            "end_ms": 0,
            "label": label,
            "sort": "desc",
            "kind": "all",
        }
    )
    await _render_clients_by_date_list(callback, state, page=0)


@router.callback_query(F.data == "admin_cdate_manual")
async def admin_cdate_manual_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    await state.set_state(AdminSearchStates.waiting_cdate_range)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data="admin_clients_by_date_menu",
                )
            ]
        ]
    )
    await safe_edit_text(
        callback.message,
        "✏️ <b>ورود دستی بازه تاریخ ساخت</b>\n\n"
        "می‌توانید به یکی از روش‌های زیر بازه را بفرستید:\n"
        "• <b>مدت اخیر:</b> <code>30m</code> (دقیقه)، <code>12h</code> یا <code>12</code> (ساعت)، <code>5d</code> (روز)، <code>2w</code> (هفته)\n"
        "• <b>یک تاریخ شمسی:</b> <code>1405/09/08</code>\n"
        "• <b>بازه دو تاریخ شمسی:</b> <code>1405/09/01 تا 1405/09/08</code> یا <code>1405/09/01-1405/09/08</code>\n\n"
        "💡 <i>برای انصراف /cancel بفرستید یا دکمه زیر را بزنید.</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_cdate_range, F.text)
async def admin_cdate_manual_save(message: types.Message, state: FSMContext) -> None:
    if message.text and message.text.strip() == "/cancel":
        await _clear_fsm_keep_nav(state)
        await _render_clients_by_date_list(
            message, state, page=0, notice="❌ ورود دستی بازه لغو شد."
        )
        return

    parsed = _parse_cdate_input(message.text)
    if not parsed:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف", callback_data="admin_clients_by_date_menu"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ فرمت بازه معتبر نیست.\n"
            "نمونه معتبر: <code>12h</code>، <code>5d</code>، <code>1405/09/08</code> یا <code>1405/09/01-1405/09/08</code>",
            reply_markup=kb,
            parse_mode="HTML",
        )
        return

    await state.set_state(None)
    await state.update_data(
        cdate_filter={
            **parsed,
            "sort": "desc",
            "kind": "all",
        }
    )
    await _render_clients_by_date_list(message, state, page=0)


@router.callback_query(F.data.startswith("admin_cdate_page_"))
async def admin_cdate_page(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback):
        return
    try:
        page = int(callback.data[len("admin_cdate_page_") :])
    except ValueError:
        page = 0
    await _render_clients_by_date_list(callback, state, page=page)


@router.callback_query(F.data == "admin_cdate_sort")
async def admin_cdate_sort_toggle(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    data = await state.get_data()
    flt = dict(
        data.get("cdate_filter")
        or {"label": "همه کانفیگ‌ها", "sort": "desc", "kind": "all"}
    )
    flt["sort"] = "asc" if flt.get("sort", "desc") == "desc" else "desc"
    await state.update_data(cdate_filter=flt)
    await _render_clients_by_date_list(callback, state, page=0)


@router.callback_query(F.data == "admin_cdate_kind")
async def admin_cdate_kind_cycle(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    data = await state.get_data()
    flt = dict(
        data.get("cdate_filter")
        or {"label": "همه کانفیگ‌ها", "sort": "desc", "kind": "all"}
    )
    curr = flt.get("kind", "all")
    next_idx = (
        (_CDATE_KIND_CYCLE.index(curr) + 1) % len(_CDATE_KIND_CYCLE)
        if curr in _CDATE_KIND_CYCLE
        else 0
    )
    flt["kind"] = _CDATE_KIND_CYCLE[next_idx]
    await state.update_data(cdate_filter=flt)
    await _render_clients_by_date_list(callback, state, page=0)


@router.callback_query(F.data.startswith("admin_cdate_sub_"))
async def admin_cdate_sub_select(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    parts = callback.data.split("_")
    try:
        page = int(parts[3])
        idx = int(parts[4])
    except (IndexError, ValueError):
        await callback.answer("خطای انتخاب اشتراک.", show_alert=True)
        return

    data = await state.get_data()
    emails = data.get("cdate_emails") or []
    if not (0 <= idx < len(emails)) or not emails[idx]:
        await callback.answer(
            "❌ لیست قدیمی شده است؛ لطفاً بروزرسانی کنید.", show_alert=True
        )
        return

    await state.update_data(
        sub_back_callback=f"admin_cdate_page_{page}",
        manage_user_id=None,
    )
    await _render_sub_dashboard(callback, emails[idx], state)
