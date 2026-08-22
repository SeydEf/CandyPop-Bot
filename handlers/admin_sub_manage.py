from __future__ import annotations

from datetime import datetime, timezone
import logging
import time

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_CHAT_ID, SUB_BASE_URL
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


def _is_admin(event: types.CallbackQuery | types.Message) -> bool:
    return event.from_user is not None and event.from_user.id == ADMIN_CHAT_ID


class AdminSearchStates(StatesGroup):
    waiting_search_query = State()
    waiting_add_gb = State()
    waiting_add_days = State()
    waiting_limit_ip = State()
    waiting_rename_email = State()
    waiting_user_wallet = State()
    waiting_create_sub_gb = State()
    waiting_create_sub_dur = State()


@router.message(Command("search", "find", "find_user"))
@router.callback_query(F.data == "admin_search_start")
async def admin_search_start(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(event):
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
                        text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
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
                        callback_data=f"admin_manage_user_{u_id}",
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
                text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
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
        f"📊 <b>حجم کل:</b> {format_size_gb(total_gb)}\n"
        f"📉 <b>حجم مصرفی:</b> {format_size_gb(used_gb)}\n"
        f"🔋 <b>حجم باقیمانده:</b> {format_size_gb(rem_gb)}\n\n"
        f"⏱ <b>تاریخ انقضا:</b> {time_str}\n\n"
        f"🔗 <b>لینک ساب‌اسکریپشن:</b>\n<code>{sub_link}</code>"
    )

    toggle_btn_text = "🔴 غیرفعال‌سازی سرویس" if is_enabled else "🟢 فعال‌سازی سرویس"

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
                    text="👤 تغییر سقف کاربر (IP)",
                    callback_data=f"admin_sub_ip_menu_{email}",
                ),
                InlineKeyboardButton(
                    text="✏️ تغییر نام (Email)",
                    callback_data=f"admin_sub_rename_menu_{email}",
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
                    callback_data=f"admin_sub_delete_{email}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به جستجو", callback_data="admin_search_back"
                )
            ],
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

    notice_block = f"{notice}\n\n" if notice else ""

    text = (
        f"{notice_block}"
        f"👤 <b>مدیریت کاربر:</b> <code>{tg_id}</code>\n\n"
        f"نام و نام‌خانوادگی: <b>{full_name}</b>\n"
        f"یوزرنیم: <b>{username_str}</b>\n"
        f"💰 <b>موجودی کیف پول:</b> {format_price(bal)}\n"
        f"👥 <b>معرفی‌کننده:</b> <code>{referrer}</code>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
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
                    text="🔙 بازگشت به جستجو", callback_data="admin_search_back"
                )
            ],
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


@router.callback_query(F.data.startswith("admin_manage_sub_"))
async def admin_manage_sub_dashboard(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    email = callback.data[len("admin_manage_sub_") :]
    await _render_sub_dashboard(callback, email, state)


@router.callback_query(F.data.startswith("admin_sub_gb_menu_"))
async def admin_sub_gb_prompt(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return

    email = callback.data[len("admin_sub_gb_menu_") :]
    await state.update_data(manage_email=email)
    await state.set_state(AdminSearchStates.waiting_add_gb)

    await callback.message.edit_text(
        f"📊 <b>افزایش یا تنظیم حجم جدید برای اشتراک «{email}»:</b>\n\n"
        "حجم اضافه یا حجم جدید را به گیگابایت وارد کنید:\n"
        "• برای افزودن حجم به اشتراک فعلی، عدد گیگابایت را وارد کنید (مثال: <code>20</code>)\n"
        "• برای ست کردن حجم مشخص، عبارت <code>set:50</code> را ارسال کنید.\n\n"
        "<i>برای انصراف /cancel را بزنید.</i>",
        parse_mode="HTML",
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
    if not _is_admin(callback):
        return

    email = callback.data[len("admin_sub_days_menu_") :]
    await state.update_data(manage_email=email)
    await state.set_state(AdminSearchStates.waiting_add_days)

    await callback.message.edit_text(
        f"⏱ <b>افزایش یا تمدید زمان برای اشتراک «{email}»:</b>\n\n"
        "تعداد روز جدید یا اضافی را وارد کنید:\n"
        "• برای <b>تمدید و افزودن روز</b>، عدد روزها را وارد کنید (مثال: <code>30</code>)\n"
        "• برای <b>تنظیم دقیق روزهای مانده از الان</b>، عبارت <code>set:60</code> را بفرستید.\n\n"
        "<i>برای انصراف /cancel را بزنید.</i>",
        parse_mode="HTML",
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

    if is_set or current_expiry_ms < now_ms:
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
    if not _is_admin(callback):
        return

    email = callback.data[len("admin_sub_ip_menu_") :]
    await state.update_data(manage_email=email)
    await state.set_state(AdminSearchStates.waiting_limit_ip)

    await callback.message.edit_text(
        f"👤 <b>تغییر سقف کاربر همزمان (IP Limit) برای «{email}»:</b>\n\n"
        "تعداد کاربران مجاز همزمان را به صورت عدد وارد کنید (0 یعنی نامحدود):\n"
        "مثال: <code>2</code>\n\n"
        "<i>برای انصراف /cancel را بزنید.</i>",
        parse_mode="HTML",
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
    if not _is_admin(callback):
        return

    email = callback.data[len("admin_sub_rename_menu_") :]
    await state.update_data(manage_email=email)
    await state.set_state(AdminSearchStates.waiting_rename_email)

    await callback.message.edit_text(
        f"✏️ <b>تغییر نام سرویس (Email) برای «{email}»:</b>\n\n"
        "نام جدید و دلخواه خود را ارسال نمایید:\n\n"
        "<i>برای انصراف /cancel را بزنید.</i>",
        parse_mode="HTML",
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
    if not _is_admin(callback):
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
    if not _is_admin(callback):
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
    if not _is_admin(callback):
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


@router.callback_query(F.data.startswith("admin_sub_delete_"))
async def admin_sub_delete(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback):
        return

    email = callback.data[len("admin_sub_delete_") :]
    try:
        await xui_api.delete_client(email)
        await callback.answer(f"✅ اشتراک «{email}» با موفقیت حذف شد.", show_alert=True)
    except Exception as e:
        logger.error("Failed to delete client %s: %s", email, e)
        await callback.answer(f"❌ خطا در حذف اشتراک: {e}", show_alert=True)

    data = await state.get_data()
    last_query = data.get("last_search_query")
    if last_query:
        await _perform_search_and_render(callback, state, last_query)
    else:
        await callback.message.edit_text("❌ اشتراک حذف شد.")


@router.callback_query(F.data.startswith("admin_manage_user_"))
async def admin_manage_user_dashboard(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    u_id_str = callback.data[len("admin_manage_user_") :]
    tg_id = int(u_id_str)
    await _render_user_dashboard(callback, tg_id, state)


@router.callback_query(F.data.startswith("admin_user_wallet_"))
async def admin_user_wallet_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    tg_id = int(callback.data[len("admin_user_wallet_") :])
    await state.update_data(manage_user_id=tg_id)
    await state.set_state(AdminSearchStates.waiting_user_wallet)

    await callback.message.edit_text(
        f"💳 <b>شارژ یا تغییر موجودی کیف پول کاربر ({tg_id}):</b>\n\n"
        "مبلغ (به تومان) را وارد کنید:\n"
        "• برای <b>شارژ و افزودن به موجودی</b>، عدد مثبت وارد کنید (مثال: <code>50000</code>)\n"
        "• برای <b>تنظیم مستقیم موجودی</b>، عبارت <code>set:100000</code> را ارسال کنید.\n\n"
        "<i>برای انصراف /cancel را بزنید.</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminSearchStates.waiting_user_wallet, F.text)
async def admin_user_wallet_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    tg_id = data.get("manage_user_id")

    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        if tg_id:
            await _render_user_dashboard(
                message, tg_id, state, notice="❌ تغییر کیف پول لغو شد."
            )
        else:
            await message.answer("❌ عملیات لغو شد.")
        return

    if not tg_id:
        await state.clear()
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

    await state.clear()
    await _render_user_dashboard(
        message,
        tg_id,
        state,
        notice=f"✅ <b>موجودی جدید کیف پول کاربر: {format_price(new_balance)}</b>",
    )


@router.callback_query(F.data.startswith("admin_user_create_sub_"))
async def admin_user_create_sub_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(callback):
        return

    tg_id = int(callback.data[len("admin_user_create_sub_") :])
    await state.update_data(manage_user_id=tg_id)
    await state.set_state(AdminSearchStates.waiting_create_sub_gb)

    await callback.message.edit_text(
        f"➕ <b>ساخت اشتراک اختصاصی جدید برای کاربر {tg_id}:</b>\n\n"
        "<b>مرحله ۱:</b> لطفاً حجم اشتراک را به گیگابایت وارد کنید:\n"
        "مثال: <code>30</code>\n\n"
        "<i>برای انصراف /cancel را بزنید.</i>",
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
        await state.clear()
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
        await state.clear()
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
        await state.clear()
        return

    user = await get_user(tg_id)
    username = user.get("username") if user else None
    email = generate_email(tg_id, username)

    total_bytes = gb_to_bytes(gb_val)
    expiry_ms = int((time.time() + dur_val * 86400) * 1000)

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

        await state.clear()
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
    if not _is_admin(callback):
        return

    data = await state.get_data()
    last_query = data.get("last_search_query")

    if last_query:
        await _perform_search_and_render(callback, state, last_query)
    else:
        await admin_search_start(callback, state)
