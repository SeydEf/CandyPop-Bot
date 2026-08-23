from __future__ import annotations

import logging
import time

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import SUB_BASE_URL
from db.models import (
    get_active_client_group,
    get_active_inbound_ids,
    get_user,
    search_users,
)
from services import xui_api
from utils.formatting import (
    format_size_gb,
    persian_to_english_digits,
    to_persian_digits,
)
from utils.helpers import gb_to_bytes, generate_email, generate_qr, safe_edit_text

logger = logging.getLogger(__name__)
router = Router(name="admin_create_sub")


async def _is_admin(event: types.CallbackQuery | types.Message) -> bool:
    from db.models import has_admin_permission

    if event.from_user is None:
        return False
    permitted = await has_admin_permission(event.from_user.id, "create_sub")
    if not permitted:
        msg = "⛔️ شما دسترسی به بخش «ساخت اشتراک سفارشی» را ندارید."
        if isinstance(event, types.CallbackQuery):
            await event.answer(msg, show_alert=True)
        else:
            await event.answer(msg)
        return False
    return True


class AdminCreateSubStates(StatesGroup):
    waiting_user_id = State()
    waiting_email = State()
    waiting_gb = State()
    waiting_dur = State()
    waiting_ip = State()
    waiting_inbounds = State()
    waiting_group = State()
    waiting_confirm = State()


@router.message(Command("create_sub", "new_sub", "add_sub"))
@router.callback_query(F.data == "admin_create_sub_start")
@router.callback_query(F.data == "admin_create_sub_step_user")
async def admin_create_sub_start(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(event):
        return

    if isinstance(event, types.Message) or event.data == "admin_create_sub_start":
        await state.clear()

    await _prompt_step_user(event, state)


async def _prompt_step_user(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(AdminCreateSubStates.waiting_user_id)

    text = (
        "➕ <b>ساخت اشتراک اختصاصی (گام ۱ از ۷) - انتخاب کاربر مقصد</b>\n\n"
        "لطفاً <b>آیدی عددی تلگرام</b> یا <b>نام کاربری (@username)</b> مشتری را وارد کنید:\n\n"
        "• برای ساخت اشتراک مستقل (بدون انتساب به کاربر)، روی دکمه زیر کلیک کنید.\n"
        "<i>جهت انصراف، /cancel را ارسال کنید.</i>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 ساخت اشتراک مستقل (بدون کاربر)",
                    callback_data="admin_create_sub_nouser",
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
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await safe_edit_text(
            event.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await event.answer()


@router.callback_query(
    F.data == "admin_create_sub_nouser", AdminCreateSubStates.waiting_user_id
)
async def admin_create_sub_nouser(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    await state.update_data(
        target_tg_id=0, target_user_name="مستقل (بدون کاربر)", target_username=None
    )
    await _prompt_step_email(callback, state)


@router.message(AdminCreateSubStates.waiting_user_id, F.text)
async def admin_create_sub_user_input(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ ساخت اشتراک لغو شد.")
        return

    txt = message.text.strip()
    users = await search_users(txt)

    if users:
        u = users[0]
        tg_id = u["tg_id"]
        username = u.get("username")
        name = username or u.get("full_name") or f"User {tg_id}"
        await state.update_data(
            target_tg_id=tg_id, target_user_name=name, target_username=username
        )
    elif txt.lstrip("@").isdigit():
        tg_id = int(txt.lstrip("@"))
        user = await get_user(tg_id)
        username = user.get("username") if user else None
        name = (username or user.get("full_name")) if user else f"User {tg_id}"
        await state.update_data(
            target_tg_id=tg_id, target_user_name=name, target_username=username
        )
    else:
        await message.answer(
            "⚠️ کاربر با این آیدی یافت نشد، اما به عنوان آیدی جدید ثبت می‌گردد."
        )
        clean_name = txt.lstrip("@")
        await state.update_data(
            target_tg_id=0, target_user_name=txt, target_username=clean_name
        )

    await _prompt_step_email(message, state)


@router.callback_query(F.data == "admin_create_sub_step_email")
async def admin_create_sub_step_email_nav(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await _prompt_step_email(callback, state)


async def _prompt_step_email(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(AdminCreateSubStates.waiting_email)
    data = await state.get_data()
    tg_id = data.get("target_tg_id", 0)
    target_username = data.get("target_username")

    auto_email = (
        generate_email(tg_id, username=target_username)
        if (tg_id or target_username)
        else f"custom_{int(time.time()) % 100000}"
    )
    await state.update_data(suggested_email=auto_email)

    text = (
        f"🏷 <b>تعیین نام سرویس / ایمیل (گام ۲ از ۷)</b>\n\n"
        f"نام پیشنهادی سیستم: <code>{auto_email}</code>\n\n"
        f"• می‌توانید نام دلخواه خود را تایپ و ارسال کنید.\n"
        f"• یا برای استفاده از نام پیشنهادی، دکمه زیر را بزنید:\n"
        f"<i>جهت انصراف، /cancel را ارسال کنید.</i>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ استفاده از {auto_email}",
                    callback_data="admin_create_sub_use_suggested_email",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 گام قبلی (انتخاب کاربر)",
                    callback_data="admin_create_sub_step_user",
                )
            ],
        ]
    )

    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await safe_edit_text(
            event.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await event.answer()


@router.callback_query(
    F.data == "admin_create_sub_use_suggested_email",
    AdminCreateSubStates.waiting_email,
)
async def admin_create_sub_use_suggested_email(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    data = await state.get_data()
    email = data.get("suggested_email", f"custom_{int(time.time()) % 100000}")
    await state.update_data(final_email=email)
    await _prompt_step_gb(callback, state)


@router.message(AdminCreateSubStates.waiting_email, F.text)
async def admin_create_sub_email_input(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ ساخت اشتراک لغو شد.")
        return

    email = message.text.strip()[:50]
    await state.update_data(final_email=email)
    await _prompt_step_gb(message, state)


@router.callback_query(F.data == "admin_create_sub_step_gb")
async def admin_create_sub_step_gb_nav(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await _prompt_step_gb(callback, state)


async def _prompt_step_gb(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(AdminCreateSubStates.waiting_gb)

    text = (
        "📊 <b>تعیین حجم ترافیک (گام ۳ از ۷)</b>\n\n"
        "حجم اشتراک را به گیگابایت انتخاب کرده یا عدد مورد نظر خود را تایپ کنید:\n"
        "<i>(مثال برای تایپ: 45)</i>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="۱۰ گیگ", callback_data="admin_create_sub_gb_10"
                ),
                InlineKeyboardButton(
                    text="۲۰ گیگ", callback_data="admin_create_sub_gb_20"
                ),
                InlineKeyboardButton(
                    text="۳۰ گیگ", callback_data="admin_create_sub_gb_30"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="۵۰ گیگ", callback_data="admin_create_sub_gb_50"
                ),
                InlineKeyboardButton(
                    text="۱۰۰ گیگ", callback_data="admin_create_sub_gb_100"
                ),
                InlineKeyboardButton(
                    text="♾ نامحدود (0)", callback_data="admin_create_sub_gb_0"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 گام قبلی (نام سرویس)",
                    callback_data="admin_create_sub_step_email",
                )
            ],
        ]
    )

    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await safe_edit_text(
            event.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await event.answer()


@router.callback_query(
    F.data.startswith("admin_create_sub_gb_"), AdminCreateSubStates.waiting_gb
)
async def admin_create_sub_gb_select(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    gb_val = float(callback.data[len("admin_create_sub_gb_") :])
    await state.update_data(final_gb=gb_val)
    await _prompt_step_dur(callback, state)


@router.message(AdminCreateSubStates.waiting_gb, F.text)
async def admin_create_sub_gb_input(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ ساخت اشتراک لغو شد.")
        return

    try:
        gb_val = float(persian_to_english_digits(message.text.strip()))
        if gb_val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد معتبر به گیگابایت وارد کنید.")
        return

    await state.update_data(final_gb=gb_val)
    await _prompt_step_dur(message, state)


@router.callback_query(F.data == "admin_create_sub_step_dur")
async def admin_create_sub_step_dur_nav(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await _prompt_step_dur(callback, state)


async def _prompt_step_dur(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(AdminCreateSubStates.waiting_dur)

    text = (
        "⏱ <b>تعیین مدت اعتبار (گام ۴ از ۷)</b>\n\n"
        "مدت زمان اشتراک را به روز انتخاب کرده یا عدد دلخواه خود را تایپ کنید:\n"
        "<i>(مثال برای تایپ: 45)</i>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="۳۰ روز (۱ ماه)", callback_data="admin_create_sub_dur_30"
                ),
                InlineKeyboardButton(
                    text="۶۰ روز (۲ ماه)", callback_data="admin_create_sub_dur_60"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="۹۰ روز (۳ ماه)", callback_data="admin_create_sub_dur_90"
                ),
                InlineKeyboardButton(
                    text="۳۶۵ روز (۱ سال)", callback_data="admin_create_sub_dur_365"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="♾ نامحدود زمانی (0)", callback_data="admin_create_sub_dur_0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 گام قبلی (حجم ترافیک)",
                    callback_data="admin_create_sub_step_gb",
                )
            ],
        ]
    )

    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await safe_edit_text(
            event.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await event.answer()


@router.callback_query(
    F.data.startswith("admin_create_sub_dur_"), AdminCreateSubStates.waiting_dur
)
async def admin_create_sub_dur_select(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    dur_val = int(callback.data[len("admin_create_sub_dur_") :])
    await state.update_data(final_dur=dur_val)
    await _prompt_step_ip(callback, state)


@router.message(AdminCreateSubStates.waiting_dur, F.text)
async def admin_create_sub_dur_input(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ ساخت اشتراک لغو شد.")
        return

    try:
        dur_val = int(persian_to_english_digits(message.text.strip()))
        if dur_val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر به روز وارد کنید.")
        return

    await state.update_data(final_dur=dur_val)
    await _prompt_step_ip(message, state)


@router.callback_query(F.data == "admin_create_sub_step_ip")
async def admin_create_sub_step_ip_nav(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await _prompt_step_ip(callback, state)


async def _prompt_step_ip(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(AdminCreateSubStates.waiting_ip)

    text = (
        "👤 <b>تعیین سقف کاربر همزمان - IP Limit (گام ۵ از ۷)</b>\n\n"
        "تعداد کاربران مجاز همزمان را انتخاب کنید یا عدد تایپ نمایید:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="۱ کاربر", callback_data="admin_create_sub_ip_1"
                ),
                InlineKeyboardButton(
                    text="۲ کاربر", callback_data="admin_create_sub_ip_2"
                ),
                InlineKeyboardButton(
                    text="۳ کاربر", callback_data="admin_create_sub_ip_3"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="♾ نامحدود (0)", callback_data="admin_create_sub_ip_0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 گام قبلی (مدت اعتبار)",
                    callback_data="admin_create_sub_step_dur",
                )
            ],
        ]
    )

    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await safe_edit_text(
            event.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await event.answer()


@router.callback_query(
    F.data.startswith("admin_create_sub_ip_"), AdminCreateSubStates.waiting_ip
)
async def admin_create_sub_ip_select(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    ip_val = int(callback.data[len("admin_create_sub_ip_") :])
    await state.update_data(final_ip=ip_val)
    await _prompt_step_inbounds(callback, state)


@router.message(AdminCreateSubStates.waiting_ip, F.text)
async def admin_create_sub_ip_input(message: types.Message, state: FSMContext) -> None:
    if not message.text or message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ ساخت اشتراک لغو شد.")
        return

    try:
        ip_val = int(persian_to_english_digits(message.text.strip()))
        if ip_val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید.")
        return

    await state.update_data(final_ip=ip_val)
    await _prompt_step_inbounds(message, state)


@router.callback_query(F.data == "admin_create_sub_step_inbounds")
async def admin_create_sub_step_inbounds_nav(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await _prompt_step_inbounds(callback, state)


async def _prompt_step_inbounds(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(AdminCreateSubStates.waiting_inbounds)
    data = await state.get_data()

    inbounds = await xui_api.list_inbounds()
    active_defaults = set(await get_active_inbound_ids())

    selected_ids = data.get("final_inbound_ids")
    if selected_ids is None:
        selected_ids = list(active_defaults)
        await state.update_data(final_inbound_ids=selected_ids)

    sel_set = set(selected_ids)

    text = (
        "📡 <b>انتخاب اینباندهای مجاز برای اشتراک (گام ۶ از ۷)</b>\n\n"
        "اینباندهای مورد نظر خود را با کلیک روی آنها روی وضعیت 🟢 فعال یا 🔴 غیرفعال تنظیم کنید:\n"
        "<i>(در صورت عدم تغییر، از اینباندهای پیش‌فرض مشتریان استفاده می‌شود)</i>"
    )

    keyboard_rows = []

    if inbounds:
        for ib in inbounds:
            ib_id = ib.get("id")
            remark = ib.get("remark") or ib.get("tag") or f"Inbound #{ib_id}"
            proto = ib.get("protocol", "").upper()
            port = ib.get("port", 0)
            is_sel = ib_id in sel_set
            icon = "🟢" if is_sel else "🔴"

            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{icon} #{ib_id} | {remark} ({proto}:{port})",
                        callback_data=f"admin_create_sub_ib_toggle_{ib_id}",
                    )
                ]
            )

        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text="⭐️ ریست به اینباندهای پیش‌فرض مشتری",
                    callback_data="admin_create_sub_ib_reset_default",
                ),
            ]
        )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="➡️ گام بعدی (انتخاب گروه)",
                callback_data="admin_create_sub_step_group",
            )
        ]
    )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔙 گام قبلی (سقف کاربر)",
                callback_data="admin_create_sub_step_ip",
            )
        ]
    )

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
        await event.answer()


@router.callback_query(
    F.data.startswith("admin_create_sub_ib_toggle_"),
    AdminCreateSubStates.waiting_inbounds,
)
async def admin_create_sub_ib_toggle(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    ib_id = int(callback.data[len("admin_create_sub_ib_toggle_") :])
    data = await state.get_data()
    selected_ids = set(data.get("final_inbound_ids") or [])

    if ib_id in selected_ids:
        selected_ids.remove(ib_id)
    else:
        selected_ids.add(ib_id)

    await state.update_data(final_inbound_ids=sorted(selected_ids))
    await _prompt_step_inbounds(callback, state)


@router.callback_query(
    F.data == "admin_create_sub_ib_reset_default",
    AdminCreateSubStates.waiting_inbounds,
)
async def admin_create_sub_ib_reset_default(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    active_defaults = await get_active_inbound_ids()
    await state.update_data(final_inbound_ids=active_defaults)
    await callback.answer("✅ به اینباندهای پیش‌فرض ریست شد.", show_alert=True)
    await _prompt_step_inbounds(callback, state)


@router.callback_query(F.data == "admin_create_sub_step_group")
async def admin_create_sub_step_group_nav(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return
    await _prompt_step_group(callback, state)


async def _prompt_step_group(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(AdminCreateSubStates.waiting_group)

    groups = await xui_api.list_client_groups()
    active_group = await get_active_client_group()

    text = (
        "🏷 <b>تعیین گروه مشتری (گام ۷ از ۷)</b>\n\n"
        "لطفاً گروه مورد نظر برای این اشتراک را انتخاب کنید:"
    )

    keyboard_rows = []
    if groups:
        for g in groups:
            g_name = g.get("name", "")
            if not g_name:
                continue
            star = " (فعال خریداران)" if g_name == active_group else ""
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"📁 {g_name}{star}",
                        callback_data=f"admin_create_sub_grp_{g_name}",
                    )
                ]
            )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="⚪️ بدون گروه", callback_data="admin_create_sub_grp_none"
            )
        ]
    )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔙 گام قبلی (انتخاب اینباند)",
                callback_data="admin_create_sub_step_inbounds",
            )
        ]
    )

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
        await event.answer()


@router.callback_query(
    F.data.startswith("admin_create_sub_grp_"), AdminCreateSubStates.waiting_group
)
async def admin_create_sub_group_select(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    grp_raw = callback.data[len("admin_create_sub_grp_") :]
    final_group = "" if grp_raw == "none" else grp_raw

    await state.update_data(final_group=final_group)
    await _show_summary_and_confirm(callback, state)


async def _show_summary_and_confirm(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(AdminCreateSubStates.waiting_confirm)
    data = await state.get_data()

    user_name = data.get("target_user_name", "بدون کاربر")
    tg_id = data.get("target_tg_id", 0)
    email = data.get("final_email", "custom_sub")
    gb_val = data.get("final_gb", 30.0)
    dur_val = data.get("final_dur", 30)
    ip_val = data.get("final_ip", 0)
    group_val = data.get("final_group", "") or "بدون گروه"
    inbound_ids = data.get("final_inbound_ids") or await get_active_inbound_ids()

    gb_str = format_size_gb(gb_val) if gb_val > 0 else "نامحدود"
    dur_str = f"{to_persian_digits(dur_val)} روز" if dur_val > 0 else "نامحدود"
    ip_str = f"{to_persian_digits(ip_val)} کاربر" if ip_val > 0 else "نامحدود"
    ib_str = ", ".join(f"#{i}" for i in inbound_ids) if inbound_ids else "هیچکدام"

    text = (
        f"📋 <b>پیش‌نمایش و تایید نهایی ساخت اشتراک سفارشی:</b>\n\n"
        f"👤 <b>کاربر مقصد:</b> {user_name} (<code>{tg_id}</code>)\n"
        f"🏷 <b>نام سرویس (Email):</b> <code>{email}</code>\n"
        f"📊 <b>حجم ترافیک:</b> {gb_str}\n"
        f"⏱ <b>مدت اعتبار:</b> {dur_str}\n"
        f"👥 <b>سقف کاربر (IP):</b> {ip_str}\n"
        f"📡 <b>اینباندهای فعال:</b> <code>{ib_str}</code>\n"
        f"📁 <b>گروه مشتری:</b> <code>{group_val}</code>\n\n"
        f"آیا از ایجاد این اشتراک با مشخصات فوق اطمینان دارید؟"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تایید نهایی و صدور اشتراک",
                    callback_data="admin_create_sub_execute",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 گام قبلی (انتخاب گروه)",
                    callback_data="admin_create_sub_step_group",
                )
            ],
            [InlineKeyboardButton(text="❌ انصراف", callback_data="admin_price_main")],
        ]
    )

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(
    F.data == "admin_create_sub_execute", AdminCreateSubStates.waiting_confirm
)
async def admin_create_sub_execute(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _is_admin(callback):
        return

    data = await state.get_data()
    tg_id = data.get("target_tg_id", 0)
    email = data.get("final_email", f"custom_{int(time.time())}")
    gb_val = data.get("final_gb", 30.0)
    dur_val = data.get("final_dur", 30)
    ip_val = data.get("final_ip", 0)
    group_val = data.get("final_group", "")
    inbound_ids = data.get("final_inbound_ids") or await get_active_inbound_ids()

    total_bytes = gb_to_bytes(gb_val) if gb_val > 0 else 0
    expiry_ms = int((time.time() + dur_val * 86400) * 1000) if dur_val > 0 else 0

    try:
        await xui_api.add_client(
            email=email,
            total_gb=total_bytes,
            expiry_time=expiry_ms,
            tg_id=tg_id,
            inbound_ids=inbound_ids,
            limit_ip=ip_val,
            group=group_val,
        )

        client = await xui_api.get_client(email)
        sub_id = client.get("subId", "") if client else ""
        sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

        await state.clear()

        if sub_link != "نامشخص":
            buf = generate_qr(sub_link)
            input_file = types.BufferedInputFile(buf.getvalue(), filename="qrcode.png")
            cap = (
                f"🎉 <b>اشتراک اختصاصی سفارشی با موفقیت ساخته شد!</b>\n\n"
                f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                f"👤 <b>آیدی کاربر:</b> <code>{tg_id}</code>\n"
                f"📊 <b>حجم:</b> {format_size_gb(gb_val) if gb_val > 0 else 'نامحدود'}\n"
                f"⏱ <b>مدت:</b> {to_persian_digits(dur_val)} روز\n\n"
                f"🔗 <b>لینک اشتراک:</b>\n<code>{sub_link}</code>"
            )
            if callback.message:
                await callback.message.answer_photo(
                    photo=input_file, caption=cap, parse_mode="HTML"
                )
        else:
            await callback.message.edit_text(
                f"🎉 <b>اشتراک اختصاصی با موفقیت ساخته شد!</b>\n\n"
                f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n",
                parse_mode="HTML",
            )
        await callback.answer()

    except Exception as e:
        logger.error("Failed to create custom sub: %s", e)
        await callback.answer(f"❌ خطا در ساخت اشتراک: {e}", show_alert=True)
