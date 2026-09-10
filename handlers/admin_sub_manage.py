from __future__ import annotations

from datetime import datetime, timezone
import logging
import time

from aiogram import F, Router, types
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
from utils.formatting import (
    format_datetime,
    format_price,
    format_size_gb,
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


@router.message(Command("search", "find", "find_user"))
@router.callback_query(F.data == "admin_search_start")
async def admin_search_start(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(event):
        return

    await state.set_state(AdminSearchStates.waiting_search_query)

    text = (
        "🔍 <b>جستجوی کاربر و مدیریت اشتراک‌ها</b>\n\n"
        "لطفاً عبارتی برای جستجو وارد کنید:\n"
        "• <b>آیدی عددی تلگرام:</b> <code>123456789</code>\n"
        "• <b>نام کاربری (Username):</b> <code>@username</code>\n"
        "• <b>نام سرویس یا ایمیل اشتراک:</b> <code>user_123456</code>\n\n"
        "<i>جهت انصراف، دستور /cancel را ارسال کنید.</i>"
    )

    if isinstance(event, types.Message):
        await event.answer(text, parse_mode="HTML")
    else:
        await safe_edit_text(
            event.message,
            text,
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
            total_gb = (c.get("totalGB") or 0) / (1024**3)
            lines.append(f"• {status_icon} <b>{email}</b> ({format_size_gb(total_gb)})")
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
    total_gb = total_bytes / (1024**3)
    used_gb = used_bytes / (1024**3)
    rem_gb = max(0.0, total_gb - used_gb)

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
    sub_id = client.get("subId", "")
    sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

    notice_block = f"{notice}\n\n" if notice else ""

    text = (
        f"{notice_block}"
        f"⚙️ <b>مدیریت اشتراک:</b> <code>{email}</code>\n\n"
        f"🔘 <b>وضعیت:</b> {status_str}\n"
        f"👥 <b>سقف کاربر (IP):</b> {limit_ip_str}\n"
        f"🏷 <b>گروه مشتری:</b> <code>{group_name}</code>\n"
        f"🆔 <b>آیدی تلگرام:</b> <code>{client.get('tgId', 'نامشخص')}</code>\n\n"
        f"📡 <b>وضعیت اتصال و آنلاین:</b>\n"
        f"   • 🌐 آخرین اتصال: {last_online_str}\n"
        f"   • 🔄 آخرین دریافت ساب: {last_sub_fetch_str}\n\n"
        f"📊 <b>حجم کل:</b> {format_size_gb(total_gb)}\n"
        f"📉 <b>حجم مصرفی:</b> {format_size_gb(used_gb)}\n"
        f"🔋 <b>حجم باقیمانده:</b> {format_size_gb(rem_gb)}\n\n"
        f"⏱ <b>تاریخ انقضا:</b> {time_str}\n\n"
        f"🔗 <b>لینک ساب‌اسکریپشن:</b>\n<code>{sub_link}</code>"
    )

    toggle_btn_text = "🔴 غیرفعال‌سازی سرویس" if is_enabled else "🟢 فعال‌سازی سرویس"

    data = await state.get_data()
    sub_back = data.get("sub_back_callback")
    if sub_back:
        if "admin_user_subs_" in sub_back:
            back_btn_text = "🔙 بازگشت به اشتراک‌های کاربر"
        else:
            back_btn_text = "🔙 بازگشت به مدیریت کاربر"
        back_btn_callback = sub_back
    else:
        back_btn_text = "🔙 بازگشت به جستجو"
        back_btn_callback = "admin_search_back"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
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
        "<i>برای انصراف دکمه زیر را لمس کرده یا /cancel را ارسال کنید.</i>",
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
        await message.answer("❌ لطفاً یک عدد معتبر وارد کنید (مثال: 20 یا set:50).")
        return

    current_total_gb = (client.get("totalGB") or 0) / (1024**3)
    new_total_gb = gb_val if is_set else (current_total_gb + gb_val)
    new_total_bytes = gb_to_bytes(new_total_gb)

    update_data = dict(client)
    update_data["totalGB"] = new_total_bytes
    update_data["enable"] = True

    try:
        await xui_api.update_client(email, update_data)
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
        "<i>برای انصراف دکمه زیر را لمس کرده یا /cancel را ارسال کنید.</i>",
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
        await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید (مثال: 30).")
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
        "<i>برای انصراف دکمه زیر را لمس کرده یا /cancel را ارسال کنید.</i>",
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
        await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید (0 برای نامحدود).")
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
        "<i>برای انصراف دکمه زیر را لمس کرده یا /cancel را ارسال کنید.</i>",
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
                    text="🔙 انصراف و بازگشت",
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
        "<i>برای انصراف /cancel یا دکمه زیر را بزنید.</i>",
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
        await message.answer("❌ لطفاً یک عدد صحیح معتبر به تومان وارد کنید.")
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
                "<i>برای انصراف /cancel یا دکمه زیر را بزنید:</i>"
            )
        else:
            await state.set_state(AdminSearchStates.waiting_unban_custom_msg)
            prompt = (
                f"✍️ <b>ارسال پیام دلخواه رفع مسدودی به کاربر <code>{tg_id}</code>:</b>\n\n"
                "لطفاً متن پیام ارسالی به کاربر را تایپ کنید:\n\n"
                "<i>برای انصراف /cancel یا دکمه زیر را بزنید:</i>"
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
        "<i>برای انصراف /cancel یا دکمه زیر را بزنید:</i>"
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
                    text="🔙 انصراف و بازگشت",
                    callback_data=f"admin_manage_user_{tg_id}_back",
                )
            ]
        ]
    )

    await callback.message.edit_text(
        f"➕ <b>ساخت اشتراک اختصاصی جدید برای کاربر {tg_id}:</b>\n\n"
        "<b>مرحله ۱:</b> لطفاً حجم اشتراک را به گیگابایت وارد کنید:\n"
        "مثال: <code>30</code>\n\n"
        "<i>برای انصراف /cancel یا دکمه زیر را بزنید.</i>",
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
        await message.answer("❌ لطفاً یک عدد معتبر وارد کنید (مثال: 30).")
        return

    await state.update_data(new_sub_gb=gb_val)
    await state.set_state(AdminSearchStates.waiting_create_sub_dur)

    await message.answer(
        "<b>مرحله ۲:</b> مدت اعتبار اشتراک را به روز وارد کنید:\n"
        "مثال: <code>30</code>\n\n"
        "<i>برای انصراف /cancel را بزنید.</i>",
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
        await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید (مثال: 30).")
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

        await _clear_fsm_keep_nav(state)
        await message.answer(
            f"🎉 <b>اشتراک جدید با موفقیت ایجاد شد!</b>\n\n"
            f"👤 کاربر: <code>{tg_id}</code>\n"
            f"🏷 نام سرویس: <code>{email}</code>\n"
            f"📊 حجم: {format_size_gb(gb_val)}\n"
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
        amount = format_price(inv["amount"])
        status = inv["status"]
        status_str = (
            "✅ موفق/تأییدشده"
            if status in ("paid", "approved")
            else ("⏳ در انتظار" if status == "pending" else "❌ ردشده/ناموفق")
        )
        pm = inv.get("payment_method") or "card"
        pm_str = "💰 کیف پول" if pm == "wallet" else "💳 کارت به کارت"
        created_at = inv.get("created_at") or ""

        lines.append(
            f"🔹 <b>فاکتور #{inv_id}</b> | {amount}\n"
            f"   روش: {pm_str} | وضعیت: <b>{status_str}</b>\n"
            f"   📅 تاریخ: <code>{created_at}</code>\n"
        )

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
