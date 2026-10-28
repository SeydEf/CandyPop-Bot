from __future__ import annotations

import logging
from typing import Any
import uuid

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import INVOICE_EXPIRY_MINUTES
from db.discounts import validate_discount_code
from db.models import (
    create_invoice,
    debit_wallet,
    get_active_client_group,
    get_balance,
    get_card_config,
    get_sub_final_attachment,
    is_admin,
    update_invoice_status,
)
from keyboards.inline_kb import (
    card_payment_keyboard,
    confirm_delete_keyboard,
    confirm_regen_keyboard,
    renew_duration_keyboard,
    renew_options_keyboard,
    renew_payment_method_keyboard,
    renew_users_keyboard,
    renew_volume_keyboard,
    renew_wallet_confirm_keyboard,
    sub_config_links_keyboard,
    subscription_manage_keyboard,
    subscriptions_list_keyboard,
)
from services.renewal_service import (
    activate_reserved_renewal,
    is_client_expired,
)
from keyboards.reply_kb import BTN_MY_SUBS
from services import xui_api
from services.pricing import (
    format_volume_label,
    get_price_breakdown,
    is_custom_duration_allowed_for_volume,
    is_duration_allowed_for_volume,
    is_unlimited_volume,
)
from utils.formatting import (
    format_detailed_traffic,
    format_price,
    format_remaining_days,
    format_size_persian_gb,
    persian_to_english_digits,
    to_persian_digits,
)
from utils.helpers import generate_qr, _build_sub_link

logger = logging.getLogger(__name__)
router = Router(name="subscriptions")


class SubStates(StatesGroup):
    waiting_rename = State()
    waiting_renew_discount = State()
    waiting_custom_gb = State()
    waiting_custom_days = State()


async def _fetch_subs_from_xui(tg_id: int) -> list[dict]:
    raw = await xui_api.get_clients_by_tg_id(tg_id)
    subs: list[dict] = []
    for entry in raw:
        client = entry.get("client", {})
        subs.append(
            {
                "email": client.get("email", ""),
                "service_name": client.get("email", ""),
                "sub_id": client.get("subId", ""),
                "totalGB": client.get("totalGB", 0),
                "expiryTime": client.get("expiryTime", 0),
                "usedTraffic": entry.get("usedTraffic", 0),
                "enable": client.get("enable", True),
                "inboundIds": entry.get("inboundIds", []),
            }
        )
    return subs


@router.message(Command("subs"))
@router.message(F.text == BTN_MY_SUBS)
async def my_subscriptions(message: types.Message) -> None:
    if not message.from_user:
        return

    subs = await _fetch_subs_from_xui(message.from_user.id)
    if not subs:
        await message.answer(
            "📭 <b>شما در حال حاضر هیچ اشتراک فعالی ندارید.</b>\n\n"
            "🚀 برای خرید اشتراک، از منوی اصلی دکمه «🛒 خرید اشتراک» را انتخاب کنید.",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"📋 <b>فهرست اشتراک‌های فعال شما ({to_persian_digits(len(subs))} سرویس):</b>\n\n"
        "👇 برای مشاهده وضعیت و مدیریت هر سرویس، آن را انتخاب کنید:",
        reply_markup=subscriptions_list_keyboard(subs),
        parse_mode="HTML",
    )


async def _render_subscriptions_list(callback: types.CallbackQuery) -> None:
    if not callback.from_user:
        return
    subs = await _fetch_subs_from_xui(callback.from_user.id)
    if not subs:
        await callback.message.edit_text(
            "📭 <b>شما در حال حاضر هیچ اشتراک فعالی ندارید.</b>\n\n"
            "🚀 برای خرید اشتراک، از منوی اصلی دکمه «🛒 خرید اشتراک» را انتخاب کنید.",
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            f"📋 <b>فهرست اشتراک‌های فعال شما ({to_persian_digits(len(subs))} سرویس):</b>\n\n"
            "👇 برای مشاهده وضعیت و مدیریت هر سرویس، آن را انتخاب کنید:",
            reply_markup=subscriptions_list_keyboard(subs),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "sub_back_list")
async def back_to_list(callback: types.CallbackQuery) -> None:
    await _render_subscriptions_list(callback)
    await callback.answer()


async def _build_dashboard_info(
    email: str,
) -> tuple[str, InlineKeyboardMarkup] | None:
    client = await xui_api.get_client(email)
    client_full = await xui_api.get_client_full(email)

    if not client:
        return None

    from db.models import get_reserve_renewal_config, get_reserved_renewal

    if is_client_expired(client):
        res_rec = await get_reserved_renewal(email)
        if res_rec:
            await activate_reserved_renewal(email, force=True, bot=None)
            client = await xui_api.get_client(email)
            client_full = await xui_api.get_client_full(email)
            if not client:
                return None

    total_bytes = client.get("totalGB", 0)
    expiry_ms = client.get("expiryTime", 0)
    used_traffic = client_full.get("usedTraffic", 0) if client_full else 0
    remaining = max(0, total_bytes - used_traffic) if total_bytes > 0 else 0
    sub_id = client.get("subId", "")
    sub_link = _build_sub_link(sub_id)

    total_gb_int = int(round(total_bytes / (1024**3))) if total_bytes > 0 else 0
    is_unlimited = (total_bytes == 0) or (await is_unlimited_volume(total_gb_int))

    if is_unlimited:
        if total_bytes > 0:
            usage_text = f"{format_detailed_traffic(used_traffic)} / {format_size_persian_gb(total_bytes)}"
            remaining_text = f"{format_detailed_traffic(remaining)}"
        else:
            usage_text = f"{format_detailed_traffic(used_traffic)} / ♾️ نامحدود"
            remaining_text = "♾️ نامحدود"
    elif total_bytes > 0:
        usage_text = f"{format_detailed_traffic(used_traffic)} / {format_size_persian_gb(total_bytes)}"
        remaining_text = format_detailed_traffic(remaining)
    else:
        usage_text = f"{format_detailed_traffic(used_traffic)} / نامحدود"
        remaining_text = "نامحدود"

    limit_ip = client.get("limitIp", 0)
    if limit_ip > 0:
        users_text = f"{to_persian_digits(limit_ip)} کاربر"
    else:
        users_text = "نامحدود"

    days_text = format_remaining_days(expiry_ms)

    from db.models import get_ip_violation

    is_enabled = bool(client.get("enable", True))
    ip_rec = await get_ip_violation(email)
    is_ip_suspended = bool(ip_rec.get("suspended", 0)) if ip_rec else False

    if is_ip_suspended:
        status_text = "⛔️ مسدودشده (تخطی از سقف اتصال IP)"
    elif is_enabled:
        status_text = "🟢 فعال"
    else:
        status_text = "🔴 غیرفعال"

    plan_type_line = (
        f"📦 <b>نوع اشتراک:</b> ♾️ نامحدود (سقف منصفانه {format_size_persian_gb(total_bytes)})\n"
        if is_unlimited
        else ""
    )

    text = (
        f"<b>داشبورد مدیریت اشتراک</b>\n\n"
        f"🏷 <b>نام سرویس:</b> <code>{client.get('email', email)}</code>\n"
        f"⚡️ <b>وضعیت اشتراک:</b> {status_text}\n"
        f"{plan_type_line}"
        f"👥 <b>ظرفیت کاربر:</b> {users_text}\n"
        f"📊 <b>میزان مصرف:</b> {usage_text}\n"
        f"🔋 <b>ترافیک باقیمانده:</b> {remaining_text}\n"
        f"⏳ <b>اعتبار باقیمانده:</b> {days_text}\n\n"
        f"🔗 <b>لینک هوشمند اشتراک (ساب‌اسکریپشن):</b>\n<code>{sub_link}</code>\n\n"
        f"💡 <i>از گزینه‌های زیر می‌توانید برای تمدید، تغییر نام، دریافت QR و لینک‌های اتصال استفاده کنید.</i>"
    )

    res_rec = await get_reserved_renewal(email)
    has_reserved = bool(res_rec)
    reserve_cfg = await get_reserve_renewal_config()
    reserve_enabled = bool(reserve_cfg.get("enabled", True))
    can_early_activate = bool(reserve_cfg.get("allow_early_activation", True))

    if res_rec:
        res_dur = res_rec.get("duration_days", 0)
        res_gb = res_rec.get("data_gb", 0)
        res_users = res_rec.get("users_count", 1)
        res_vol_label = await format_volume_label(res_gb, with_fair_policy=True)
        text += (
            f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>بسته تمدید رزروشده:</b>\n"
            f"⏱ <b>مدت اعتبار:</b> {res_dur} روز\n"
            f"📊 <b>حجم ترافیک:</b> {res_vol_label}\n"
            f"👥 <b>ظرفیت کاربر:</b> {to_persian_digits(res_users)} کاربر\n"
            f"💡 <i>این بسته پس از پایان حجم یا زمان سرویس فعلی، به‌صورت خودکار فعال می‌شود.</i>"
        )

    is_test_sub = email.endswith("_test") or "_test" in email

    if is_test_sub:
        show_renew = False
    elif reserve_enabled:
        show_renew = True
    else:
        import time

        from db.models import get_alert_config

        alert_config = await get_alert_config()
        min_gb = float(alert_config.get("min_gb", 2.0))
        min_days = int(alert_config.get("min_days", 3))

        now_ms = int(time.time() * 1000)

        is_low_gb = False
        if total_bytes > 0:
            rem_gb = (
                max(0, total_bytes - used_traffic) / (1024**3)
                if total_bytes > 0
                else 999.0
            )
            if rem_gb <= min_gb:
                is_low_gb = True

        is_low_days = False
        if expiry_ms > 0:
            rem_days = (expiry_ms - now_ms) / (86400 * 1000)
            if rem_days <= min_days:
                is_low_days = True

        show_renew = is_low_gb or is_low_days

    return text, subscription_manage_keyboard(
        email,
        show_renew=show_renew,
        is_test_sub=is_test_sub,
        has_reserved=has_reserved,
        can_early_activate=can_early_activate,
        is_reserve_mode=reserve_enabled,
    )


@router.callback_query(F.data == "sub_view_current")
@router.callback_query(F.data.startswith("sub_view_"))
async def view_subscription(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.data == "sub_view_current":
        await sub_view_current(callback, state)
        return

    email = callback.data[len("sub_view_") :]
    if email == "current":
        await sub_view_current(callback, state)
        return

    await state.update_data(renew_email=email)

    info = await _build_dashboard_info(email)
    if not info:
        await callback.answer("❌ متأسفانه اطلاعات اشتراک یافت نشد.", show_alert=True)
        return

    text, keyboard = info
    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sub_rename_"))
async def rename_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    email = callback.data[len("sub_rename_") :]
    if email.endswith("_test") or "_test" in email:
        await callback.answer(
            "❌ امکان تغییر نام اشتراک‌های تست وجود ندارد.", show_alert=True
        )
        return

    await state.set_state(SubStates.waiting_rename)
    await state.update_data(rename_email=email)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data=f"sub_view_{email}",
                )
            ]
        ]
    )

    user_is_admin = await is_admin(callback.from_user.id)
    if not user_is_admin:
        tg_id = callback.from_user.id
        raw_username = callback.from_user.username
        if raw_username:
            prefix = "".join(c for c in raw_username if c.isalnum() or c == "_")
            if not prefix:
                prefix = str(tg_id)
        else:
            prefix = str(tg_id)
        hint_text = f"💡 <i>پیشوند <code>{prefix}_</code> به‌صورت خودکار به ابتدای نام انتخابی اضافه خواهد شد.</i>\n\n"
    else:
        hint_text = ""

    await callback.message.edit_text(
        f"✏️ <b>تغییر نام سرویس «{email}»</b>\n\n"
        "لطفاً نام جدید و دلخواه خود را ارسال کنید (حداکثر ۵۰ کاراکتر):\n\n"
        f"{hint_text}"
        "❌ <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SubStates.waiting_rename, F.text)
async def rename_process(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        return

    data = await state.get_data()
    old_email = data.get("rename_email")

    if message.text.strip() == "/cancel":
        await state.clear()
        if old_email:
            info = await _build_dashboard_info(old_email)
            if info:
                text, keyboard = info
                await message.answer(
                    f"❌ <b>عملیات تغییر نام لغو شد.</b>\n\n{text}",
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
                return
        await message.answer("❌ عملیات تغییر نام لغو شد.")
        return
    if not old_email:
        await state.clear()
        return

    raw_input = message.text.strip()
    user_is_admin = await is_admin(message.from_user.id)

    if not user_is_admin:
        tg_id = message.from_user.id
        raw_username = message.from_user.username
        if raw_username:
            prefix = "".join(c for c in raw_username if c.isalnum() or c == "_")
            if not prefix:
                prefix = str(tg_id)
        else:
            prefix = str(tg_id)

        clean_input = raw_input.lstrip("@_")
        if not clean_input:
            cancel_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ انصراف و بازگشت",
                            callback_data=f"sub_view_{old_email}",
                        )
                    ]
                ]
            )
            await message.answer(
                "⚠️ <b>نام وارد شده نامعتبر است.</b>\n\n"
                "لطفاً نام دیگری را ارسال فرمایید:\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )
            return

        if clean_input.startswith(f"{prefix}_"):
            new_name = clean_input[:50]
        else:
            new_name = f"{prefix}_{clean_input}"[:50]
    else:
        new_name = raw_input[:50]

    client = await xui_api.get_client(old_email)
    if not client:
        await state.clear()
        await message.answer("❌ سرویس مورد نظر یافت نشد.")
        return

    update_data = dict(client)
    update_data["email"] = new_name

    try:
        await xui_api.update_client(old_email, update_data)
        from db.models import rename_client_references

        await rename_client_references(old_email, new_name)
        await state.clear()

        info = await _build_dashboard_info(new_name)
        if info:
            text, keyboard = info
            await message.answer(
                f"🎉 <b>نام سرویس با موفقیت به «{new_name}» تغییر یافت!</b>\n\n{text}",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            await message.answer(
                f"🎉 <b>نام سرویس با موفقیت به «{new_name}» تغییر یافت!</b>",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.warning("Failed to rename client %s to %s: %s", old_email, new_name, e)
        err_str = str(e).lower()
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ انصراف و بازگشت",
                        callback_data=f"sub_view_{old_email}",
                    )
                ]
            ]
        )
        if (
            "unique" in err_str
            or "constraint" in err_str
            or "exist" in err_str
            or "already" in err_str
        ):
            await message.answer(
                "⚠️ <b>این نام قبلاً توسط سرویس دیگری استفاده شده است!</b>\n\n"
                "لطفاً یک نام جدید و متفاوت وارد کنید:\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "⚠️ <b>امکان استفاده از این نام وجود ندارد.</b>\n\n"
                "لطفاً نام دیگری را امتحان کنید:\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )


@router.callback_query(F.data.startswith("sub_regen_"))
async def regen_confirm(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_regen_") :]
    await callback.message.edit_text(
        f"🔐 <b>تغییر و بازنشانی لینک اشتراک ({email})</b>\n\n"
        "⚠️ <b>توجه مهم:</b>\n"
        "با تغییر لینک اشتراک، تمامی کانفیگ‌های قبلی باطل می‌شوند و اتصال دستگاه‌ها قطع خواهد شد.\n\n"
        "آیا از بازنشانی و صدور لینک جدید اطمینان دارید؟",
        reply_markup=confirm_regen_keyboard(email),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sub_confirm_regen_"))
async def regen_execute(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_confirm_regen_") :]

    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ سرویس مورد نظر یافت نشد.", show_alert=True)
        return

    new_uuid = str(uuid.uuid4())
    new_sub_id = str(uuid.uuid4().hex[:16])

    update_data = dict(client)
    update_data["uuid"] = new_uuid
    update_data["subId"] = new_sub_id

    try:
        await xui_api.update_client(email, update_data)

        info = await _build_dashboard_info(email)
        if info:
            text, keyboard = info
            await callback.message.edit_text(
                f"✅ <b>لینک اشتراک جدید با موفقیت صادر شد!</b>\n"
                f"⚠️ لینک قبلی باطل شده است؛ لطفاً لینک جدید را در نرم‌افزار خود وارد کنید.\n\n{text}",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            new_link = _build_sub_link(new_sub_id)
            is_test_sub = email.endswith("_test") or "_test" in email
            await callback.message.edit_text(
                f"✅ <b>لینک اشتراک جدید با موفقیت صادر شد!</b>\n\n"
                f"🔗 لینک هوشمند جدید:\n<code>{new_link}</code>\n\n"
                "⚠️ لینک قبلی باطل شد.",
                reply_markup=subscription_manage_keyboard(
                    email, show_renew=False, is_test_sub=is_test_sub
                ),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.exception("Failed to regenerate link for %s", email)
        await callback.answer(f"❌ بروز خطا: {e}", show_alert=True)

    await callback.answer()


@router.callback_query(F.data.startswith("sub_delete_"))
async def delete_confirm(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_delete_") :]
    await callback.message.edit_text(
        f"🗑 <b>حذف سرویس «{email}»</b>\n\n"
        "⚠️ <b>هشدار جدی:</b> با حذف این سرویس، دسترسی کانفیگ‌ها فوراً قطع شده و امکان بازگردانی آن وجود نخواهد داشت!\n\n"
        "آیا از حذف این سرویس مطمئن هستید؟",
        reply_markup=confirm_delete_keyboard(email),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sub_confirm_del_"))
async def delete_execute(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_confirm_del_") :]

    try:
        await xui_api.delete_client(email)
        await callback.answer("🗑 سرویس با موفقیت حذف شد.", show_alert=True)
    except Exception:
        logger.exception("Failed to delete client %s from X-UI", email)
        await callback.answer("❌ متأسفانه در حذف سرویس خطایی رخ داد.", show_alert=True)
        return

    await _render_subscriptions_list(callback)


@router.callback_query(F.data.startswith("sub_qr_"))
async def show_qr(callback: types.CallbackQuery, bot: Bot) -> None:
    email = callback.data[len("sub_qr_") :]
    client = await xui_api.get_client(email)

    sub_id = client.get("subId", "") if client else ""
    if not sub_id:
        await callback.answer("❌ لینک اشتراک یافت نشد.", show_alert=True)
        return

    sub_link = _build_sub_link(sub_id)
    qr_image = generate_qr(sub_link)

    await bot.send_photo(
        chat_id=callback.message.chat.id,
        photo=types.BufferedInputFile(qr_image.read(), filename="qrcode.png"),
        caption=(
            f"📱 <b>بارکد اختصاصی (QR Code) اشتراک:</b>\n\n"
            f"🔗 <code>{sub_link}</code>\n\n"
            f"💡 <i>کافی است در نرم‌افزار مورد نظر (مانند v2rayN یا V2Box)، گزینه اسکن بارکد (Scan QR) را انتخاب کنید.</i>"
        ),
        parse_mode="HTML",
    )
    await callback.answer()


def _extract_link_name(link: str, index: int) -> str:
    if "#" in link:
        from urllib.parse import unquote

        name = unquote(link.split("#", 1)[1]).strip()
        if name:
            return name
    return f"کانفیگ {to_persian_digits(index)}"


@router.callback_query(F.data.startswith("sub_links_"))
async def show_links(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_links_") :]

    links = await xui_api.get_client_links(email)

    if not links:
        await callback.answer("❌ هیچ کانفیگ فعالی یافت نشد.", show_alert=True)
        return

    text = "⚡️ <b>کانفیگ‌های اختصاصی سرویس شما:</b>\n\n"
    for i, link in enumerate(links, 1):
        name = _extract_link_name(link, i)
        text += f"🔹 <b>{name}:</b>\n<code>{link}</code>\n\n"

    text += "💡 <i>روی هر کانفیگ بزنید تا کپی شود، سپس آن را در نرم‌افزار خود وارد (Import) کنید.</i>"

    await callback.message.answer(
        text,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "sub_view_current")
async def sub_view_current(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("renew_email")
    if not email:
        await _render_subscriptions_list(callback)
        await callback.answer()
        return

    info = await _build_dashboard_info(email)
    if not info:
        await _render_subscriptions_list(callback)
        await callback.answer()
        return

    text, keyboard = info
    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "sub_renew_current")
@router.callback_query(F.data.startswith("sub_renew_"))
async def sub_renew_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    from db.models import get_shop_status

    shop_status = await get_shop_status()
    if not shop_status["renewals_enabled"]:
        await callback.answer("⛔️ تمدید اشتراک‌ها موقتاً غیرفعال است.", show_alert=True)
        await callback.message.answer(
            "⛔️ <b>تمدید اشتراک‌ها موقتاً غیرفعال است.</b>\n\n"
            "امکان تمدید سرویس در حال حاضر موقتاً متوقف شده است. لطفاً بعداً مراجعه کنید.",
            parse_mode="HTML",
        )
        return

    if callback.data.startswith("sub_renew_") and callback.data != "sub_renew_current":
        email = callback.data[len("sub_renew_") :]
        await state.update_data(renew_email=email)
    else:
        data = await state.get_data()
        email = data.get("renew_email")

    if not email:
        await callback.answer("❌ متأسفانه اشتراک پیدا نشد.", show_alert=True)
        return

    from db.models import get_reserved_renewal

    res_rec = await get_reserved_renewal(email)
    if res_rec:
        await callback.answer(
            "⚠️ شما در حال حاضر یک بسته تمدید رزروشده برای این سرویس دارید.\n"
            "امکان رزرو بیش از یک بسته وجود ندارد.",
            show_alert=True,
        )
        return

    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ اشتراک در پنل سرور یافت نشد.", show_alert=True)
        return

    current_gb = max(1, client.get("totalGB", 0) // (1024**3))
    current_users = max(1, client.get("limitIp", 1))

    from db.models import get_reserve_renewal_config

    reserve_cfg = await get_reserve_renewal_config()
    reserve_enabled = bool(reserve_cfg.get("enabled", True))

    action_title = (
        "📦 <b>رزرو اشتراک</b>" if reserve_enabled else "🔄 <b>تمدید اشتراک</b>"
    )
    prompt_desc = "💡 مایلید با مشخصات قبلی تمدید شود یا مشخصات (مدت، حجم، تعداد کاربر) را تغییر می‌دهید؟"

    vol_label = await format_volume_label(current_gb)

    text = (
        f"{action_title}\n\n"
        f"🏷 <b>نام سرویس:</b> <code>{client.get('email', email)}</code>\n"
        f"📊 <b>حجم فعلی:</b> <b>{vol_label}</b>\n"
        f"👥 <b>ظرفیت اتصال فعلی:</b> <b>{to_persian_digits(current_users)} کاربر</b>\n\n"
        f"{prompt_desc}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=renew_options_keyboard(is_reserve_mode=reserve_enabled),
        parse_mode="HTML",
    )
    await callback.answer()


async def _build_renewal_invoice(
    data: dict[str, Any],
    coupon_dc: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    from db.discounts import calculate_discount_amount
    from db.models import (
        get_renewal_discount_config,
        get_reserve_renewal_config,
    )

    email = data.get("renew_email", "")
    duration = data.get("duration", 30)
    users = data.get("users", 1)
    gb = data.get("gb", 10)
    original_price = data.get("original_price") or data.get("price", 0)

    bd = await get_price_breakdown(gb, duration, users)
    if not original_price:
        original_price = bd["total_price"]

    reserve_cfg = await get_reserve_renewal_config()
    reserve_enabled = bool(reserve_cfg.get("enabled", True))
    is_change_plan = data.get("is_change_plan", False)
    if not is_change_plan:
        title = (
            "📋 <b>پیش‌فاکتور رزرو با مشخصات فعلی</b>"
            if reserve_enabled
            else "📋 <b>پیش‌فاکتور تمدید پلن فعلی</b>"
        )
    else:
        title = (
            "📋 <b>پیش‌فاکتور رزرو اشتراک</b>"
            if reserve_enabled
            else "📋 <b>پیش‌فاکتور تمدید اشتراک</b>"
        )

    renew_cfg = await get_renewal_discount_config()
    default_enabled = renew_cfg.get("enabled", False)
    default_percent = renew_cfg.get("percent", 10)
    allow_coupon = renew_cfg.get("allow_coupon", True)
    stack_discounts = renew_cfg.get("stack_discounts", False)

    default_discount_amount = 0
    coupon_discount_amount = 0
    coupon_code_str = None

    if coupon_dc and allow_coupon:
        coupon_code_str = coupon_dc.get("code")
        c_amt, _ = calculate_discount_amount(coupon_dc, original_price)
        coupon_discount_amount = c_amt

    if default_enabled:
        default_discount_amount = int(original_price * (default_percent / 100.0))

    if coupon_dc and allow_coupon:
        if default_enabled and stack_discounts:
            rules = coupon_dc.get("rules") or {}
            if rules.get("discount_type") == "fixed":
                total_discount = min(
                    original_price, default_discount_amount + coupon_discount_amount
                )
            else:
                c_pct = rules.get("amount") or coupon_dc.get("discount_percent", 0)
                tot_pct = min(100, default_percent + c_pct)
                total_discount = int(original_price * (tot_pct / 100.0))
        elif default_enabled and not stack_discounts:
            total_discount = min(original_price, coupon_discount_amount)
            default_discount_amount = 0
        else:
            total_discount = min(original_price, coupon_discount_amount)
    else:
        total_discount = min(original_price, default_discount_amount)

    final_price = max(0, original_price - total_discount)

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
    if default_discount_amount > 0:
        breakdown_lines.append(
            f"• تخفیف تمدید ({to_persian_digits(default_percent)}٪): -{format_price(default_discount_amount)}"
        )
    if coupon_code_str and coupon_discount_amount > 0:
        breakdown_lines.append(
            f"• کد تخفیف ({coupon_code_str}): -{format_price(coupon_discount_amount)}"
        )

    breakdown_str = "\n".join(breakdown_lines)

    text = (
        f"{title}\n\n"
        f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
        f"📦 <b>مشخصات تمدید:</b>\n"
        f"• حجم ترافیک: <b>{vol_label}</b>\n"
        f"• ظرفیت اتصال: <b>{to_persian_digits(users)} کاربر</b>\n"
        f"• مدت اعتبار: <b>{to_persian_digits(duration)} روز</b>\n\n"
        f"💳 <b>ریز محاسبات:</b>\n"
        f"{breakdown_str}\n"
        f"──────────────\n"
        f"💎 <b>مبلغ قابل پرداخت:</b> <b>{format_price(final_price)}</b>\n\n"
        f"💳 روش پرداخت را انتخاب کنید:"
    )

    pricing_info = {
        "original_price": original_price,
        "final_price": final_price,
        "total_discount": total_discount,
        "coupon_code": coupon_code_str,
        "coupon_discount_amount": coupon_discount_amount,
        "allow_coupon": allow_coupon,
        "has_coupon": bool(coupon_code_str),
        "default_enabled": default_enabled,
        "default_discount_amount": default_discount_amount,
    }
    return text, pricing_info


@router.callback_query(F.data == "renew_same")
async def renew_same_plan(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("renew_email")
    if not email:
        await callback.answer("❌ متأسفانه اشتراک پیدا نشد.", show_alert=True)
        return

    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    current_gb = max(1, client.get("totalGB", 0) // (1024**3))
    current_users = max(1, client.get("limitIp", 1))
    duration = 30

    bd = await get_price_breakdown(current_gb, duration, current_users)
    price = bd["total_price"]

    await state.update_data(
        duration=duration,
        users=current_users,
        gb=current_gb,
        original_price=price,
        price=price,
        is_change_plan=False,
    )

    data = await state.get_data()
    text, p_info = await _build_renewal_invoice(data)
    await state.update_data(
        price=p_info["final_price"],
        final_price=p_info["final_price"],
        original_price=p_info["original_price"],
        discount_code=None,
        discount_amount=p_info["total_discount"],
    )

    card_cfg = await get_card_config()
    await callback.message.edit_text(
        text,
        reply_markup=renew_payment_method_keyboard(
            is_change_plan=False,
            has_discount=False,
            card_enabled=card_cfg.get("enabled", True),
            allow_coupon=p_info["allow_coupon"],
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "renew_change")
async def renew_change_plan(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(is_change_plan=True)
    data = await state.get_data()
    email = data.get("renew_email", "")

    from db.models import get_reserve_renewal_config

    reserve_cfg = await get_reserve_renewal_config()
    reserve_enabled = bool(reserve_cfg.get("enabled", True))
    change_plan_title = (
        f"📦 <b>تغییر مشخصات و رزرو سرویس «{email}»</b>"
        if reserve_enabled
        else f"🔄 <b>تغییر پلن و تمدید سرویس «{email}»</b>"
    )

    await callback.message.edit_text(
        f"{change_plan_title}\n\n"
        f"<b>گام ۱ از ۳: انتخاب حجم ترافیک جدید</b>\n\n"
        f"لطفاً میزان ترافیک مورد نظر خود را برای این سرویس انتخاب کنید:",
        reply_markup=await renew_volume_keyboard(duration=30, users=1),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_users_step_"))
async def renew_users_step(callback: types.CallbackQuery, state: FSMContext) -> None:
    users = int(callback.data.split("_")[-1])
    data = await state.get_data()
    gb = data.get("gb", 30)

    await callback.message.edit_reply_markup(
        reply_markup=renew_users_keyboard(gb, users)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_users_confirm_"))
async def renew_users_confirm(callback: types.CallbackQuery, state: FSMContext) -> None:
    users = int(callback.data.split("_")[-1])
    await state.update_data(users=users)

    data = await state.get_data()
    email = data.get("renew_email", "")
    gb = data.get("gb", 30)

    from handlers.buy import _get_duration_step_text

    text = await _get_duration_step_text(gb, users)
    await callback.message.edit_text(
        f"🔄 <b>تغییر پلن سرویس «{email}»</b>\n\n{text}",
        reply_markup=await renew_duration_keyboard(gb, users),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "renew_dur_custom")
async def renew_custom_duration_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    data = await state.get_data()
    gb = data.get("gb", 30)
    if not await is_custom_duration_allowed_for_volume(gb, for_renew=True):
        await callback.answer(
            "⚠️ مدت زمان دلخواه برای این پلن در تمدید فعال نیست.", show_alert=True
        )
        return
    await state.set_state(SubStates.waiting_custom_days)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data="renew_back_to_duration",
                )
            ]
        ]
    )
    await callback.message.edit_text(
        "✍️ <b>مدت‌زمان دلخواه تمدید را وارد کنید:</b>\n\n"
        "تعداد روز اشتراک را به صورت عددی ارسال کنید.\n"
        "🔸 <b>حداقل مدت:</b> <code>30</code> روز\n"
        "🔸 <b>حداکثر مدت:</b> <code>365</code> روز (۱ سال)\n"
        "🔸 <b>مثال:</b> <code>45</code>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SubStates.waiting_custom_days, F.text)
async def renew_custom_duration_input(
    message: types.Message, state: FSMContext
) -> None:
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 فرایند تمدید لغو شد.")
        return

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data="renew_back_to_duration",
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
                "⚠️ <b>حداقل مدت زمان قابل تمدید ۳۰ روز است.</b>\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )
            return
        if days > 365:
            await message.answer(
                "⚠️ <b>حداکثر مدت زمان قابل تمدید ۳۶۵ روز است.</b>\n\n"
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

    duration = days
    await state.update_data(duration=duration)
    data = await state.get_data()
    gb = data.get("gb", 30)
    users = data.get("users", 1)

    bd = await get_price_breakdown(gb, duration, users)
    price = bd["total_price"]
    await state.update_data(
        original_price=price,
        price=price,
        is_change_plan=True,
    )

    data = await state.get_data()
    text, p_info = await _build_renewal_invoice(data)
    await state.update_data(
        price=p_info["final_price"],
        final_price=p_info["final_price"],
        original_price=p_info["original_price"],
        discount_code=None,
        discount_amount=p_info["total_discount"],
    )

    from keyboards.inline_kb import renew_payment_method_keyboard

    card_cfg = await get_card_config()
    await message.answer(
        text,
        reply_markup=renew_payment_method_keyboard(
            is_change_plan=True,
            has_discount=False,
            card_enabled=card_cfg.get("enabled", True),
            allow_coupon=p_info["allow_coupon"],
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.regexp(r"^renew_dur_\d+$"))
async def renew_select_duration(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    duration = int(callback.data.split("_")[-1])
    data = await state.get_data()
    gb = data.get("gb", 30)
    users = data.get("users", 1)

    if not await is_duration_allowed_for_volume(gb, duration, for_renew=True):
        await callback.answer(
            "⚠️ این مدت زمان برای این پلن در تمدید در دسترس نیست.", show_alert=True
        )
        return

    await state.update_data(duration=duration)
    bd = await get_price_breakdown(gb, duration, users)
    price = bd["total_price"]

    await state.update_data(
        original_price=price,
        price=price,
        is_change_plan=True,
    )

    data = await state.get_data()
    text, p_info = await _build_renewal_invoice(data)
    await state.update_data(
        price=p_info["final_price"],
        final_price=p_info["final_price"],
        original_price=p_info["original_price"],
        discount_code=None,
        discount_amount=p_info["total_discount"],
    )

    from keyboards.inline_kb import renew_payment_method_keyboard

    card_cfg = await get_card_config()
    await callback.message.edit_text(
        text,
        reply_markup=renew_payment_method_keyboard(
            is_change_plan=True,
            has_discount=False,
            card_enabled=card_cfg.get("enabled", True),
            allow_coupon=p_info["allow_coupon"],
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "renew_back_to_duration")
async def renew_back_to_duration(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    data = await state.get_data()
    email = data.get("renew_email", "")
    gb = data.get("gb", 30)
    users = data.get("users", 1)

    from services.pricing import resolve_plan_user_config

    user_cfg = await resolve_plan_user_config(gb)
    skip = not user_cfg["allow_selection"]

    from handlers.buy import _get_duration_step_text
    from keyboards.inline_kb import renew_duration_keyboard

    text = await _get_duration_step_text(gb, users)
    await callback.message.edit_text(
        f"🔄 <b>تغییر پلن سرویس «{email}»</b>\n\n{text}",
        reply_markup=await renew_duration_keyboard(gb, users, skip_users_step=skip),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "renew_back_to_users")
async def renew_back_to_users(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("renew_email", "")
    gb = data.get("gb", 30)

    from services.pricing import resolve_plan_user_config

    user_cfg = await resolve_plan_user_config(gb)
    if not user_cfg["allow_selection"]:
        from keyboards.inline_kb import renew_volume_keyboard
        from db.models import get_reserve_renewal_config

        reserve_cfg = await get_reserve_renewal_config()
        reserve_enabled = bool(reserve_cfg.get("enabled", True))
        change_plan_title = (
            f"📦 <b>تغییر مشخصات و رزرو سرویس «{email}»</b>"
            if reserve_enabled
            else f"🔄 <b>تغییر پلن و تمدید سرویس «{email}»</b>"
        )
        await callback.message.edit_text(
            f"{change_plan_title}\n\n"
            f"<b>گام ۱ از ۳: انتخاب حجم ترافیک جدید</b>\n\n"
            f"لطفاً میزان ترافیک مورد نظر خود را برای این سرویس انتخاب کنید:",
            reply_markup=await renew_volume_keyboard(duration=30, users=1),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    users = data.get("users", 1)
    from handlers.buy import _get_users_step_text

    text = await _get_users_step_text(gb, users)
    await callback.message.edit_text(
        f"🔄 <b>تغییر پلن سرویس «{email}»</b>\n\n{text}",
        reply_markup=renew_users_keyboard(gb, users),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "renew_vol_custom")
async def renew_custom_volume(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubStates.waiting_custom_gb)
    data = await state.get_data()
    email = data.get("renew_email", "")

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data="renew_change",
                )
            ]
        ]
    )
    from services.pricing import get_custom_volume_range

    min_gb, max_gb = await get_custom_volume_range()

    await callback.message.edit_text(
        f"✍️ <b>ورود حجم دلخواه برای تمدید سرویس «{email}»</b>\n\n"
        "حجم ترافیک مورد نظر را به <b>گیگابایت</b> ارسال کنید:\n"
        f"🔸 <b>حداقل حجم:</b> <code>{to_persian_digits(min_gb)}</code> گیگابایت\n"
        f"🔸 <b>حداکثر حجم:</b> <code>{to_persian_digits(max_gb)}</code> گیگابایت\n"
        "<i>(مثال: برای ۲۵ گیگابایت عدد <code>25</code> را ارسال کنید)</i>\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SubStates.waiting_custom_gb, F.text)
async def renew_custom_volume_input(message: types.Message, state: FSMContext) -> None:
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("🚫 فرایند تمدید لغو شد.")
        return

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data="renew_change",
                )
            ]
        ]
    )

    try:
        raw_text = (
            persian_to_english_digits(message.text.strip()) if message.text else ""
        )
        clean_text = raw_text.replace(",", "").replace("،", "").replace(" ", "")
        gb = int(clean_text)
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
        await message.answer(
            f"❌ لطفاً فقط یک عدد معتبر ارسال کنید (حداقل {to_persian_digits(min_gb)} گیگابایت).\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=cancel_kb,
            parse_mode="HTML",
        )
        return

    await state.update_data(gb=gb)
    data = await state.get_data()
    email = data.get("renew_email", "")

    from services.pricing import resolve_plan_user_config

    user_cfg = await resolve_plan_user_config(gb)

    if not user_cfg["allow_selection"]:
        users = user_cfg["users"]
        await state.update_data(users=users)
        from handlers.buy import _get_duration_step_text
        from keyboards.inline_kb import renew_duration_keyboard

        text = await _get_duration_step_text(gb, users)
        await message.answer(
            f"🔄 <b>تغییر پلن سرویس «{email}»</b>\n\n{text}",
            reply_markup=await renew_duration_keyboard(gb, users, skip_users_step=True),
            parse_mode="HTML",
        )
    else:
        users = data.get("users", 1)
        from handlers.buy import _get_users_step_text

        text = await _get_users_step_text(gb, users)
        await message.answer(
            f"🔄 <b>تغییر پلن سرویس «{email}»</b>\n\n{text}",
            reply_markup=renew_users_keyboard(gb, users),
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("renew_vol_"))
async def renew_select_volume(callback: types.CallbackQuery, state: FSMContext) -> None:
    gb = int(callback.data.split("_")[-1])
    await state.update_data(gb=gb)
    data = await state.get_data()
    email = data.get("renew_email", "")

    from services.pricing import resolve_plan_user_config

    user_cfg = await resolve_plan_user_config(gb)

    if not user_cfg["allow_selection"]:
        users = user_cfg["users"]
        await state.update_data(users=users)
        from handlers.buy import _get_duration_step_text
        from keyboards.inline_kb import renew_duration_keyboard

        text = await _get_duration_step_text(gb, users)
        await callback.message.edit_text(
            f"🔄 <b>تغییر پلن سرویس «{email}»</b>\n\n{text}",
            reply_markup=await renew_duration_keyboard(gb, users, skip_users_step=True),
            parse_mode="HTML",
        )
    else:
        users = data.get("users", 1)
        from handlers.buy import _get_users_step_text

        text = await _get_users_step_text(gb, users)
        await callback.message.edit_text(
            f"🔄 <b>تغییر پلن سرویس «{email}»</b>\n\n{text}",
            reply_markup=renew_users_keyboard(gb, users),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "renew_pay_wallet")
async def renew_wallet_payment(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not callback.from_user:
        return
    data = await state.get_data()
    email = data.get("renew_email", "")
    duration = data.get("duration", 30)
    users = data.get("users", 1)
    gb = data.get("gb", 10)
    price = data.get("price", 0)

    balance = await get_balance(callback.from_user.id)

    if balance < price:
        await callback.answer(
            f"❌ موجودی کیف پول شما کافی نیست!\n\n"
            f"💰 موجودی فعلی: {format_price(balance)}\n"
            f"💳 مبلغ مورد نیاز: {format_price(price)}\n\n"
            f"💡 لطفاً از منوی کیف پول موجودی حساب خود را افزایش دهید.",
            show_alert=True,
        )
        return

    bd = await get_price_breakdown(gb, duration, users)
    extra_users = max(0, users - 1)
    vol_label = await format_volume_label(gb, with_fair_policy=True)
    after_balance = balance - price

    original_price = data.get("original_price", price)
    discount_code = data.get("discount_code")

    breakdown_lines = [f"• هزینه حجم: {format_price(bd['data_price'])}"]
    if bd["user_surcharge"] > 0:
        breakdown_lines.append(
            f"• کاربر اضافه ({to_persian_digits(extra_users)} کاربر): +{format_price(bd['user_surcharge'])}"
        )
    if bd["duration_surcharge"] > 0:
        breakdown_lines.append(
            f"• افزایش مدت ({to_persian_digits(duration)} روز): +{format_price(bd['duration_surcharge'])}"
        )
    if original_price > price:
        discount_amount = original_price - price
        disc_label = f"کد {discount_code}" if discount_code else "تخفیف تمدید"
        breakdown_lines.append(
            f"• تخفیف ({disc_label}): -{format_price(discount_amount)}"
        )

    breakdown_str = "\n".join(breakdown_lines)

    from db.models import get_reserve_renewal_config

    reserve_cfg = await get_reserve_renewal_config()
    reserve_enabled = bool(reserve_cfg.get("enabled", True))
    wallet_header = (
        "👛 <b>تأیید پرداخت رزرو اشتراک از کیف پول</b>"
        if reserve_enabled
        else "👛 <b>تأیید پرداخت تمدید از کیف پول</b>"
    )
    wallet_question = "آیا از کسر از کیف پول و ثبت سفارش اطمینان دارید؟"

    text = (
        f"{wallet_header}\n\n"
        f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
        f"📦 <b>مشخصات تمدید:</b>\n"
        f"• حجم ترافیک: <b>{vol_label}</b>\n"
        f"• ظرفیت اتصال: <b>{to_persian_digits(users)} کاربر</b>\n"
        f"• مدت اعتبار: <b>{to_persian_digits(duration)} روز</b>\n\n"
        f"💳 <b>ریز محاسبات:</b>\n"
        f"{breakdown_str}\n"
        f"──────────────\n"
        f"💎 <b>مبلغ کسر از کیف پول:</b> <b>{format_price(price)}</b>\n"
        f"💰 <b>موجودی فعلی حساب:</b> {format_price(balance)}\n"
        f"📉 <b>موجودی پس از پرداخت:</b> {format_price(after_balance)}\n\n"
        f"{wallet_question}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=renew_wallet_confirm_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "renew_wallet_confirm")
async def renew_wallet_confirm(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not callback.from_user:
        return
    data = await state.get_data()
    email = data.get("renew_email", "")
    duration = data.get("duration", 30)
    users = data.get("users", 1)
    gb = data.get("gb", 10)
    price = data.get("price", 0)
    tg_id = callback.from_user.id

    try:
        new_balance = await debit_wallet(tg_id, price)
    except ValueError:
        await callback.answer(
            "❌ موجودی کیف پول شما برای این عملیات کافی نیست.", show_alert=True
        )
        return

    discount_code = data.get("discount_code")
    original_price = data.get("original_price", price)

    invoice = await create_invoice(
        tg_id=tg_id,
        amount=price,
        duration_days=duration,
        data_gb=gb,
        users_count=users,
        target_email=email,
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

    await process_referral_commission(tg_id, price, callback.bot)

    await callback.message.edit_text(
        "⏳ <b>در حال اعمال تغییرات و تمدید آنی سرویس شما...</b>",
        parse_mode="HTML",
    )

    try:
        from services.renewal_service import process_renewal_purchase

        renewal_ok, renewal_status, is_reserved = await process_renewal_purchase(
            email=email,
            tg_id=tg_id,
            duration_days=duration,
            data_gb=gb,
            users_count=users,
            invoice_id=invoice["id"],
            bot=callback.bot,
        )
        await state.clear()

        if renewal_ok and is_reserved:
            success_text = (
                f"📦 <b>بسته تمدید شما با موفقیت رزرو شد!</b>\n\n"
                f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                f"⏱ <b>مدت اعتبار بسته:</b> {duration} روز\n"
                f"👥 <b>ظرفیت کاربر:</b> {to_persian_digits(users)} کاربر\n"
                f"📊 <b>حجم ترافیک بسته:</b> {await format_volume_label(gb)}\n"
                f"💳 <b>روش پرداخت:</b> کیف پول (آنی)\n"
                f"👛 <b>موجودی باقیمانده کیف پول:</b> {format_price(new_balance)}\n\n"
                f"💡 <i>این بسته رزرو شد و پس از پایان اعتبار زمانی یا حجمی اشتراک فعلی، به‌صورت کاملاً خودکار فعال می‌شود. در صورت نیاز می‌توانید آن را در بخش مدیریت اشتراک زودتر فعال کنید.</i>"
            )

            await callback.message.edit_text(
                success_text,
                reply_markup=sub_config_links_keyboard(email),
                parse_mode="HTML",
            )
        else:
            client = await xui_api.get_client(email)
            sub_id = client.get("subId", "") if client else ""
            sub_link = _build_sub_link(sub_id)

            success_text = (
                f"🎉 <b>اشتراک شما با موفقیت تمدید شد!</b>\n\n"
                f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
                f"⏱ <b>مدت اعتبار جدید:</b> {duration} روز\n"
                f"👥 <b>ظرفیت کاربر جدید:</b> {to_persian_digits(users)} کاربر\n"
                f"📊 <b>حجم ترافیک جدید:</b> {await format_volume_label(gb)}\n"
                f"💳 <b>روش پرداخت:</b> کیف پول (آنی)\n"
                f"👛 <b>موجودی باقیمانده کیف پول:</b> {format_price(new_balance)}\n\n"
                f"🔗 <b>لینک هوشمند اشتراک:</b>\n<code>{sub_link}</code>\n\n"
                f"🚀 <i>ترافیک و زمان جدید به سرویس شما افزوده شد. نیازی به تغییر کانفیگ‌ها نیست و اتصال دستگاه‌ها برقرار خواهد ماند.</i>"
            )

            final_attachment = await get_sub_final_attachment()
            if final_attachment:
                success_text += final_attachment

            if sub_id:
                from aiogram.types import BufferedInputFile

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
        logger.exception("Failed to renew client %s for user %d", email, tg_id)
        from db.models import credit_wallet

        await credit_wallet(tg_id, price)
        await callback.message.edit_text(
            f"❌ <b>خطا در فرآیند تمدید سرویس!</b>\n\n"
            f"مبلغ کسرشده به کیف پول شما بازگشت.\n"
            f"علت خطا: <code>{e}</code>\n\n"
            f"لطفاً با پشتیبانی در ارتباط باشید.",
            parse_mode="HTML",
        )

    await callback.answer()


@router.callback_query(F.data == "renew_discount_apply")
async def renew_discount_apply_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    from db.models import get_renewal_discount_config

    renew_cfg = await get_renewal_discount_config()
    if not renew_cfg.get("allow_coupon", True):
        await callback.answer(
            "❌ امکان استفاده از کد تخفیف برای تمدید اشتراک توسط مدیریت غیرفعال شده است.",
            show_alert=True,
        )
        return

    data = await state.get_data()
    price = data.get("price", 0)
    await state.update_data(original_price=data.get("original_price", price))
    await state.set_state(SubStates.waiting_renew_discount)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف و بازگشت",
                    callback_data="renew_discount_cancel",
                )
            ]
        ]
    )
    from db.models import get_reserve_renewal_config

    reserve_cfg = await get_reserve_renewal_config()
    reserve_enabled = bool(reserve_cfg.get("enabled", True))
    disc_title = (
        "🏷️ <b>ورود کد تخفیف رزرو اشتراک</b>"
        if reserve_enabled
        else "🏷️ <b>ورود کد تخفیف تمدید اشتراک</b>"
    )

    await callback.message.edit_text(
        f"{disc_title}\n\n"
        "لطفاً کد تخفیف خود را به صورت لاتین ارسال کنید:\n\n"
        "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SubStates.waiting_renew_discount, F.text)
async def renew_discount_process(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        return

    data = await state.get_data()
    email = data.get("renew_email", "")
    duration = data.get("duration", 30)
    users = data.get("users", 1)
    gb = data.get("gb", 10)
    original_price = data.get("original_price") or data.get("price", 0)

    if message.text.strip() == "/cancel":
        await state.set_state(None)
        text, p_info = await _build_renewal_invoice(data, coupon_dc=None)
        await state.update_data(
            price=p_info["final_price"],
            final_price=p_info["final_price"],
            original_price=p_info["original_price"],
            discount_code=None,
            discount_amount=p_info["total_discount"],
        )
        is_change_plan = data.get("is_change_plan", False)
        card_cfg = await get_card_config()
        await message.answer(
            text,
            reply_markup=renew_payment_method_keyboard(
                is_change_plan=is_change_plan,
                has_discount=False,
                card_enabled=card_cfg.get("enabled", True),
                allow_coupon=p_info["allow_coupon"],
            ),
            parse_mode="HTML",
        )
        return

    code = message.text.strip()
    active_group = await get_active_client_group()
    order_context = {
        "data_gb": gb,
        "duration_days": duration,
        "users_count": users,
        "original_price": original_price,
        "is_renewal": True,
        "client_email": email,
        "client_group": active_group,
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
                        callback_data="renew_discount_cancel",
                    )
                ]
            ]
        )
        await message.answer(
            f"❌ <b>{err_msg}</b>\n\n"
            "لطفاً کد تخفیف را مجدداً وارد کنید:\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=cancel_kb,
            parse_mode="HTML",
        )
        return

    text, p_info = await _build_renewal_invoice(data, coupon_dc=dc)

    await state.set_state(None)
    await state.update_data(
        discount_code=p_info["coupon_code"],
        discount_percent=dc.get("discount_percent", 0),
        discount_amount=p_info["total_discount"],
        price=p_info["final_price"],
        final_price=p_info["final_price"],
        original_price=p_info["original_price"],
    )

    is_change_plan = data.get("is_change_plan", False)
    card_cfg = await get_card_config()
    await message.answer(
        "🎉 <b>کد تخفیف با موفقیت اعمال شد!</b>\n\n" + text,
        reply_markup=renew_payment_method_keyboard(
            is_change_plan=is_change_plan,
            has_discount=True,
            card_enabled=card_cfg.get("enabled", True),
            allow_coupon=p_info["allow_coupon"],
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "renew_discount_cancel")
async def renew_discount_cancel_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(None)
    data = await state.get_data()
    text, p_info = await _build_renewal_invoice(data)
    await state.update_data(
        price=p_info["final_price"],
        final_price=p_info["final_price"],
        original_price=p_info["original_price"],
        discount_amount=p_info["total_discount"],
    )
    is_change_plan = data.get("is_change_plan", False)
    card_cfg = await get_card_config()
    await callback.message.edit_text(
        text,
        reply_markup=renew_payment_method_keyboard(
            is_change_plan=is_change_plan,
            has_discount=bool(data.get("discount_code")),
            card_enabled=card_cfg.get("enabled", True),
            allow_coupon=p_info["allow_coupon"],
        ),
        parse_mode="HTML",
    )
    await callback.answer("عملیات لغو شد.")


@router.callback_query(F.data == "renew_discount_remove")
async def renew_discount_remove(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.update_data(
        discount_code=None,
        discount_percent=None,
    )
    data = await state.get_data()
    text, p_info = await _build_renewal_invoice(data, coupon_dc=None)
    await state.update_data(
        price=p_info["final_price"],
        final_price=p_info["final_price"],
        original_price=p_info["original_price"],
        discount_code=None,
        discount_percent=None,
        discount_amount=p_info["total_discount"],
    )
    is_change_plan = data.get("is_change_plan", False)
    card_cfg = await get_card_config()
    await callback.message.edit_text(
        text,
        reply_markup=renew_payment_method_keyboard(
            is_change_plan=is_change_plan,
            has_discount=False,
            card_enabled=card_cfg.get("enabled", True),
            allow_coupon=p_info["allow_coupon"],
        ),
        parse_mode="HTML",
    )
    await callback.answer("✅ کد تخفیف حذف شد.")


@router.callback_query(F.data == "renew_pay_card")
async def renew_card_payment(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return

    card_cfg = await get_card_config()
    if not card_cfg.get("enabled", True):
        await callback.answer(
            "⚠️ روش پرداخت کارت به کارت در حال حاضر غیرفعال است.",
            show_alert=True,
        )
        return

    data = await state.get_data()
    email = data.get("renew_email", "")
    duration = data.get("duration", 30)
    users = data.get("users", 1)
    gb = data.get("gb", 10)
    price = data.get("price", 0)
    discount_code = data.get("discount_code")
    original_price = data.get("original_price", price)
    final_price = data.get("final_price", price)
    payable_amount = final_price if final_price is not None else price
    tg_id = callback.from_user.id

    from db.models import generate_unique_card_amount

    card_payable_amount = await generate_unique_card_amount(payable_amount)
    card_payable_rial = card_payable_amount * 10
    unique_delta = card_payable_amount - payable_amount

    invoice = await create_invoice(
        tg_id=tg_id,
        amount=card_payable_amount,
        duration_days=duration,
        data_gb=gb,
        users_count=users,
        target_email=email,
        payment_method="card",
        discount_code=discount_code,
        original_amount=original_price,
    )
    invoice_id = invoice["id"]
    await state.clear()

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
        notice_line = f"⚠️ <i>لطفا دقیقا مبلغ <code>{card_payable_amount}</code> را انتقال دهید و آن را به هیچ‌وجه رُند نکنید تا سفارش شما فورا و خودکار تایید شود.</i>\n\n"

    breakdown_str = "\n".join(breakdown_lines)

    card_config = await get_card_config()
    card_number = card_config["card_number"]
    card_holder = card_config["card_holder"]

    from db.models import get_receipt_config, get_reserve_renewal_config

    reserve_cfg = await get_reserve_renewal_config()
    reserve_enabled = bool(reserve_cfg.get("enabled", True))
    inv_title = "سفارش رزرو اشتراک" if reserve_enabled else "سفارش تمدید اشتراک"

    receipt_cfg = await get_receipt_config()
    expiry_minutes = receipt_cfg.get("expiry_minutes", INVOICE_EXPIRY_MINUTES)

    text = (
        f"💳 <b>{inv_title} (شماره {invoice_id}) ثبت شد</b>\n\n"
        f"🏷 <b>نام سرویس:</b> <code>{email}</code>\n"
        f"📦 <b>مشخصات تمدید:</b>\n"
        f"• حجم ترافیک: <b>{vol_label}</b>\n"
        f"• ظرفیت اتصال: <b>{to_persian_digits(users)} کاربر</b>\n"
        f"• مدت اعتبار: <b>{to_persian_digits(duration)} روز</b>\n\n"
        f"💳 <b>ریز محاسبات:</b>\n"
        f"{breakdown_str}\n"
        f"──────────────\n"
        f"💎 <b>مبلغ قابل پرداخت:</b> <code>{card_payable_amount}</code> تومان (معادل <code>{card_payable_rial}</code> ریال)\n\n"
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

    from handlers.buy import _expire_invoice_after

    import asyncio

    asyncio.create_task(
        _expire_invoice_after(invoice_id, callback, expiry_minutes * 60)
    )


@router.callback_query(F.data.startswith("sub_act_res_confirm_"))
async def sub_act_res_confirm_handler(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_act_res_confirm_") :]

    res = await activate_reserved_renewal(email, force=True, bot=callback.bot)
    if res.get("status") == "activated":
        await callback.answer("✅ بسته رزرو با موفقیت فعال شد!", show_alert=True)
    elif res.get("status") == "no_reserved":
        await callback.answer(
            "❌ هیچ بسته رزروی برای این سرویس یافت نشد.", show_alert=True
        )
    else:
        await callback.answer(
            f"❌ خطا در فعال‌سازی: {res.get('msg', 'ناشناخته')}", show_alert=True
        )

    info = await _build_dashboard_info(email)
    if info:
        text, keyboard = info
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("sub_act_res_"))
async def sub_act_res_prompt_handler(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_act_res_") :]
    from db.models import get_reserved_renewal

    res_rec = await get_reserved_renewal(email)
    if not res_rec:
        await callback.answer(
            "❌ هیچ بسته رزروی برای این سرویس یافت نشد.", show_alert=True
        )
        return

    text = (
        f"⚡️ <b>فعال‌سازی پیش از موعد بسته تمدید</b>\n\n"
        f"🏷 <b>سرویس:</b> <code>{email}</code>\n"
        f"⏱ <b>مدت اعتبار بسته:</b> {res_rec.get('duration_days', 0)} روز\n"
        f"📊 <b>حجم ترافیک بسته:</b> {await format_volume_label(res_rec.get('data_gb', 0), with_fair_policy=True)}\n"
        f"👥 <b>ظرفیت کاربر:</b> {to_persian_digits(res_rec.get('users_count', 1))} کاربر\n\n"
        f"⚠️ <b>توجه مهم:</b>\n"
        f"با فعال‌سازی پیش از موعد، مصرف دوره جاری صفر شده و بسته رزرو از همین لحظه فعال می‌شود.\n\n"
        f"آیا از فعال‌سازی فوری این بسته اطمینان دارید؟"
    )
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡️ بله، فعال شود",
                    callback_data=f"sub_act_res_confirm_{email}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 انصراف و بازگشت",
                    callback_data=f"sub_view_{email}",
                )
            ],
        ]
    )
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()
