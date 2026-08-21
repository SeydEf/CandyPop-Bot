from __future__ import annotations

import logging
import uuid

from aiogram import Bot, F, Router, types
from aiogram.types import InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import CARD_HOLDER, CARD_NUMBER, INVOICE_EXPIRY_MINUTES, SUB_BASE_URL
from db.models import (
    create_invoice,
    debit_wallet,
    get_balance,
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
from keyboards.reply_kb import BTN_MY_SUBS
from services import xui_api
from services.pricing import get_price_breakdown
from utils.formatting import (
    format_price,
    format_remaining_days,
    format_size,
    format_size_gb,
    to_persian_digits,
)
from utils.helpers import generate_qr

logger = logging.getLogger(__name__)
router = Router(name="subscriptions")


class SubStates(StatesGroup):
    waiting_rename = State()


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


def _build_sub_link(sub_id: str) -> str:
    if sub_id:
        return f"{SUB_BASE_URL}/{sub_id}"
    return "نامشخص"


@router.message(F.text == BTN_MY_SUBS)
async def my_subscriptions(message: types.Message) -> None:
    if not message.from_user:
        return

    subs = await _fetch_subs_from_xui(message.from_user.id)
    if not subs:
        await message.answer(
            "📭 <b>شما درحال حاضر اشتراک فعالی ندارید.</b>\n\n"
            "برای خرید اشتراک از منوی اصلی گزینه «🛒 خرید اشتراک» را انتخاب کنید.",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"📋 <b>اشتراک‌های شما ({to_persian_digits(len(subs))}):</b>\n\n"
        "یکی را انتخاب کنید:",
        reply_markup=subscriptions_list_keyboard(subs),
        parse_mode="HTML",
    )


async def _render_subscriptions_list(callback: types.CallbackQuery) -> None:
    if not callback.from_user:
        return
    subs = await _fetch_subs_from_xui(callback.from_user.id)
    if not subs:
        await callback.message.edit_text(
            "📭 <b>شما درحال حاضر اشتراک فعالی ندارید.</b>\n\n"
            "برای خرید اشتراک از منوی اصلی گزینه «🛒 خرید اشتراک» را انتخاب کنید.",
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            f"📋 <b>اشتراک‌های شما ({to_persian_digits(len(subs))}):</b>\n\n"
            "یکی را انتخاب کنید:",
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

    total_bytes = client.get("totalGB", 0)
    expiry_ms = client.get("expiryTime", 0)
    used_traffic = client_full.get("usedTraffic", 0) if client_full else 0
    remaining = max(0, total_bytes - used_traffic) if total_bytes > 0 else 0
    sub_id = client.get("subId", "")
    sub_link = _build_sub_link(sub_id)

    if total_bytes > 0:
        usage_text = f"{format_size(used_traffic)} / {format_size(total_bytes)}"
        remaining_text = format_size(remaining)
    else:
        usage_text = f"{format_size(used_traffic)} / نامحدود"
        remaining_text = "نامحدود"

    limit_ip = client.get("limitIp", 0)
    if limit_ip > 0:
        users_text = f"{to_persian_digits(limit_ip)} کاربر"
    else:
        users_text = "نامحدود"

    days_text = format_remaining_days(expiry_ms)

    text = (
        f"📦 <b>داشبورد اشتراک</b>\n\n"
        f"📛 نام سرویس: {client.get('email', email)}\n"
        f"👤 تعداد کاربر: {users_text}\n"
        f"📊 مصرف ترافیک: {usage_text}\n"
        f"📉 ترافیک باقیمانده: {remaining_text}\n"
        f"⏱ روزهای باقیمانده: {days_text}\n\n"
        f"🔗 لینک اشتراک:\n<code>{sub_link}</code>"
    )

    return text, subscription_manage_keyboard(email)


@router.callback_query(F.data.startswith("sub_view_"))
async def view_subscription(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_view_") :]

    info = await _build_dashboard_info(email)
    if not info:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
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
    await state.set_state(SubStates.waiting_rename)
    await state.update_data(rename_email=email)

    await callback.message.edit_text(
        "✏️ <b>نام جدید سرویس را وارد کنید:</b>\n\nبرای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SubStates.waiting_rename, F.text)
async def rename_process(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        return

    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات لغو شد.")
        return

    data = await state.get_data()
    old_email = data.get("rename_email")
    if not old_email:
        await state.clear()
        return

    new_name = message.text.strip()[:50]

    client = await xui_api.get_client(old_email)
    if not client:
        await state.clear()
        await message.answer("❌ کلاینت در پنل یافت نشد.")
        return

    update_data = dict(client)
    update_data["email"] = new_name

    try:
        await xui_api.update_client(old_email, update_data)
        await state.clear()

        info = await _build_dashboard_info(new_name)
        if info:
            text, keyboard = info
            await message.answer(
                f"✅ <b>نام سرویس با موفقیت به «{new_name}» تغییر کرد!</b>\n\n{text}",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            await message.answer(
                f"✅ <b>نام سرویس با موفقیت به «{new_name}» تغییر کرد.</b>",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.exception("Failed to rename client %s", old_email)
        await state.clear()
        await message.answer(f"❌ خطا در تغییر نام: {e}")


@router.callback_query(F.data.startswith("sub_regen_"))
async def regen_confirm(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_regen_") :]
    await callback.message.edit_text(
        "⚠️ <b>آیا مطمئن هستید؟</b>\n\n"
        "با تغییر لینک اشتراک، لینک‌ قبلی و UUID های قبلی غیرفعال شده "
        "و دسترسی افراد غیرمجاز قطع می‌شود.\n\n"
        "تمام کانفیگ‌های متصل به این اشتراک باید با لینک جدید جایگزین شوند.",
        reply_markup=confirm_regen_keyboard(email),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sub_confirm_regen_"))
async def regen_execute(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_confirm_regen_") :]

    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ کلاینت در پنل یافت نشد.", show_alert=True)
        return

    new_uuid = str(uuid.uuid4())
    new_sub_id = str(uuid.uuid4())

    update_data = dict(client)
    update_data["uuid"] = new_uuid
    update_data["subId"] = new_sub_id

    try:
        await xui_api.update_client(email, update_data)

        info = await _build_dashboard_info(email)
        if info:
            text, keyboard = info
            await callback.message.edit_text(
                f"✅ <b>لینک اشتراک با موفقیت تغییر کرد!</b>\n"
                f"⚠️ لینک قبلی دیگر کار نمی‌کند.\n\n{text}",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            new_link = _build_sub_link(new_sub_id)
            await callback.message.edit_text(
                f"✅ <b>لینک اشتراک با موفقیت تغییر کرد!</b>\n\n"
                f"🔗 لینک جدید:\n<code>{new_link}</code>\n\n"
                "⚠️ لینک قبلی دیگر کار نمی‌کند.",
                reply_markup=subscription_manage_keyboard(email),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.exception("Failed to regenerate link for %s", email)
        await callback.answer(f"❌ خطا: {e}", show_alert=True)

    await callback.answer()


@router.callback_query(F.data.startswith("sub_delete_"))
async def delete_confirm(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_delete_") :]
    await callback.message.edit_text(
        "⚠️ <b>آیا مطمئن هستید که می‌خواهید این سرویس را حذف کنید؟</b>\n\n"
        "این عملیات قابل بازگشت نیست!",
        reply_markup=confirm_delete_keyboard(email),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sub_confirm_del_"))
async def delete_execute(callback: types.CallbackQuery) -> None:
    email = callback.data[len("sub_confirm_del_") :]

    try:
        await xui_api.delete_client(email)
        await callback.answer("✅ سرویس با موفقیت حذف شد.", show_alert=True)
    except Exception:
        logger.exception("Failed to delete client %s from X-UI", email)
        await callback.answer("❌ خطا در حذف سرویس.", show_alert=True)
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
        caption=f"📱 QR Code اشتراک\n\n🔗 <code>{sub_link}</code>",
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
        await callback.answer("❌ لینکی یافت نشد.", show_alert=True)
        return

    text = "🔗 <b>لینک‌های کانفیگ:</b>\n\n"
    for i, link in enumerate(links, 1):
        name = _extract_link_name(link, i)
        text += f"📌 <b>{name}:</b>\n<code>{link}</code>\n\n"

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
    if callback.data.startswith("sub_renew_") and callback.data != "sub_renew_current":
        email = callback.data[len("sub_renew_") :]
        await state.update_data(renew_email=email)
    else:
        data = await state.get_data()
        email = data.get("renew_email")

    if not email:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ اشتراک در پنل یافت نشد.", show_alert=True)
        return

    current_gb = max(1, client.get("totalGB", 0) // (1024**3))
    current_users = max(1, client.get("limitIp", 1))

    text = (
        f"🔄 <b>تمدید اشتراک</b>\n\n"
        f"📦 نام سرویس: {client.get('email', email)}\n"
        f"📊 حجم فعلی: {format_size_gb(current_gb)}\n"
        f"👤 تعداد کاربر فعلی: {to_persian_digits(current_users)} کاربر\n\n"
        f"لطفاً یکی از گزینه‌های زیر را انتخاب کنید:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=renew_options_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "renew_same")
async def renew_same_plan(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("renew_email")
    if not email:
        await callback.answer("❌ اشتراک یافت نشد.", show_alert=True)
        return

    client = await xui_api.get_client(email)
    if not client:
        await callback.answer("❌ اشتراک در پنل یافت نشد.", show_alert=True)
        return

    current_gb = max(1, client.get("totalGB", 0) // (1024**3))
    current_users = max(1, client.get("limitIp", 1))
    duration = 30

    bd = await get_price_breakdown(current_gb, duration, current_users)
    price = bd["total_price"]

    await state.update_data(
        duration=duration, users=current_users, gb=current_gb, price=price
    )

    dur_str = (
        f" (+{format_price(bd['duration_surcharge'])})"
        if bd["duration_surcharge"] > 0
        else ""
    )
    user_str = (
        f" (+{format_price(bd['user_surcharge'])})" if bd["user_surcharge"] > 0 else ""
    )

    text = (
        f"🔄 <b>پیش‌نمایش تمدید پلن فعلی</b>\n\n"
        f"📦 نام سرویس: {email}\n"
        f"⏱ مدت: {duration} روز{dur_str}\n"
        f"👤 تعداد کاربر: {to_persian_digits(current_users)} کاربر{user_str}\n"
        f"📊 حجم: {format_size_gb(current_gb)} ({format_price(bd['data_price'])})\n\n"
        f"💰 <b>مبلغ کل قابل پرداخت:</b> {format_price(price)}\n\n"
        f"💳 <b>روش پرداخت را انتخاب کنید:</b>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=renew_payment_method_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "renew_change")
async def renew_change_plan(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("renew_email", "")

    from handlers.buy import _get_duration_step_text

    text = await _get_duration_step_text()
    await callback.message.edit_text(
        f"🔄 <b>تغییر پلن و تمدید سرویس {email}</b>\n\n{text}",
        reply_markup=renew_duration_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_dur_"))
async def renew_select_duration(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    duration = int(callback.data.split("_")[-1])
    await state.update_data(duration=duration)

    data = await state.get_data()
    email = data.get("renew_email", "")

    from handlers.buy import _get_users_step_text

    text = await _get_users_step_text()
    await callback.message.edit_text(
        f"🔄 <b>تغییر پلن سرویس {email}</b>\n\n{text}",
        reply_markup=renew_users_keyboard(duration, users=1),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_users_step_"))
async def renew_users_step(callback: types.CallbackQuery, state: FSMContext) -> None:
    users = int(callback.data.split("_")[-1])
    data = await state.get_data()
    duration = data.get("duration", 30)

    await callback.message.edit_reply_markup(
        reply_markup=renew_users_keyboard(duration, users)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_users_confirm_"))
async def renew_users_confirm(callback: types.CallbackQuery, state: FSMContext) -> None:
    users = int(callback.data.split("_")[-1])
    await state.update_data(users=users)

    data = await state.get_data()
    email = data.get("renew_email", "")
    duration = data.get("duration", 30)

    from handlers.buy import _get_volume_step_text

    text = await _get_volume_step_text(duration, users)
    await callback.message.edit_text(
        f"🔄 <b>تغییر پلن سرویس {email}</b>\n\n{text}",
        reply_markup=await renew_volume_keyboard(duration, users),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_back_users_"))
async def renew_back_to_users(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("renew_email", "")
    duration = data.get("duration", 30)
    users = data.get("users", 1)

    from handlers.buy import _get_users_step_text

    text = await _get_users_step_text()
    await callback.message.edit_text(
        f"🔄 <b>تغییر پلن سرویس {email}</b>\n\n{text}",
        reply_markup=renew_users_keyboard(duration, users),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "renew_back_volume")
async def renew_back_to_volume(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    data = await state.get_data()
    email = data.get("renew_email", "")
    duration = data.get("duration", 30)
    users = data.get("users", 1)

    from handlers.buy import _get_volume_step_text

    text = await _get_volume_step_text(duration, users)
    await callback.message.edit_text(
        f"🔄 <b>تغییر پلن سرویس {email}</b>\n\n{text}",
        reply_markup=await renew_volume_keyboard(duration, users),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "renew_vol_custom")
async def renew_custom_volume(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    email = data.get("renew_email", "")

    from handlers.buy import BuyStates

    await state.set_state(BuyStates.waiting_custom_gb)
    await callback.message.edit_text(
        f"📝 <b>لطفاً حجم جدید مورد نظر برای تمدید سرویس {email} را به گیگابایت وارد کنید:</b>\n"
        "مثال: <code>25</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_vol_"))
async def renew_select_volume(callback: types.CallbackQuery, state: FSMContext) -> None:
    gb = int(callback.data.split("_")[-1])
    data = await state.get_data()
    email = data.get("renew_email", "")
    duration = data.get("duration", 30)
    users = data.get("users", 1)

    bd = await get_price_breakdown(gb, duration, users)
    price = bd["total_price"]

    await state.update_data(gb=gb, price=price)

    dur_str = (
        f" (+{format_price(bd['duration_surcharge'])})"
        if bd["duration_surcharge"] > 0
        else ""
    )
    user_str = (
        f" (+{format_price(bd['user_surcharge'])})" if bd["user_surcharge"] > 0 else ""
    )

    text = (
        f"📦 <b>خلاصه سفارش تمدید</b>\n\n"
        f"📦 نام سرویس: {email}\n"
        f"⏱ مدت جدید: {duration} روز{dur_str}\n"
        f"👤 تعداد کاربر جدید: {to_persian_digits(users)} کاربر{user_str}\n"
        f"📊 حجم جدید: {format_size_gb(gb)} ({format_price(bd['data_price'])})\n\n"
        f"💰 <b>مبلغ کل قابل پرداخت:</b> {format_price(price)}\n\n"
        f"💳 <b>روش پرداخت را انتخاب کنید:</b>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=renew_payment_method_keyboard(),
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
            f"❌ موجودی کیف پول شما کافی نیست.\n"
            f"موجودی: {format_price(balance)}\n"
            f"مبلغ مورد نیاز: {format_price(price)}",
            show_alert=True,
        )
        return

    bd = await get_price_breakdown(gb, duration, users)
    after_balance = balance - price

    dur_str = (
        f" (+{format_price(bd['duration_surcharge'])})"
        if bd["duration_surcharge"] > 0
        else ""
    )
    user_str = (
        f" (+{format_price(bd['user_surcharge'])})" if bd["user_surcharge"] > 0 else ""
    )

    text = (
        f"💰 <b>پرداخت از کیف پول برای تمدید</b>\n\n"
        f"📦 نام سرویس: {email}\n"
        f"⏱ مدت: {duration} روز{dur_str}\n"
        f"👤 تعداد کاربر: {to_persian_digits(users)} کاربر{user_str}\n"
        f"📊 حجم: {format_size_gb(gb)} ({format_price(bd['data_price'])})\n"
        f"💰 مبلغ کل: {format_price(price)}\n\n"
        f"👛 موجودی فعلی: {format_price(balance)}\n"
        f"👛 موجودی پس از خرید: {format_price(after_balance)}\n\n"
        f"آیا تأیید می‌کنید؟"
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
        await callback.answer("❌ موجودی کیف پول شما کافی نیست.", show_alert=True)
        return

    invoice = await create_invoice(
        tg_id=tg_id,
        amount=price,
        duration_days=duration,
        data_gb=gb,
        users_count=users,
        target_email=email,
        payment_method="wallet",
    )
    await update_invoice_status(invoice["id"], "approved")

    await callback.message.edit_text(
        "⏳ در حال تمدید اشتراک...",
        parse_mode="HTML",
    )

    try:
        await xui_api.renew_client(
            email=email,
            duration_days=duration,
            data_gb=gb,
            users_count=users,
        )
        await state.clear()

        client = await xui_api.get_client(email)
        sub_id = client.get("subId", "") if client else ""
        sub_link = _build_sub_link(sub_id)

        success_text = (
            f"✅ <b>اشتراک شما با موفقیت تمدید شد!</b>\n\n"
            f"📦 نام سرویس: {email}\n"
            f"⏱ مدت جدید: {duration} روز\n"
            f"👤 تعداد کاربر: {to_persian_digits(users)} کاربر\n"
            f"📊 حجم جدید: {format_size_gb(gb)}\n"
            f"💰 روش پرداخت: کیف پول\n"
            f"👛 موجودی جدید: {format_price(new_balance)}\n\n"
            f"🔗 <b>لینک اشتراک:</b>\n<code>{sub_link}</code>"
        )

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
            f"❌ خطا در تمدید اشتراک. مبلغ به کیف پول شما بازگشت داده شد.\nخطا: {e}",
            parse_mode="HTML",
        )

    await callback.answer()


@router.callback_query(F.data == "renew_pay_card")
async def renew_card_payment(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    data = await state.get_data()
    email = data.get("renew_email", "")
    duration = data.get("duration", 30)
    users = data.get("users", 1)
    gb = data.get("gb", 10)
    price = data.get("price", 0)
    tg_id = callback.from_user.id

    invoice = await create_invoice(
        tg_id=tg_id,
        amount=price,
        duration_days=duration,
        data_gb=gb,
        users_count=users,
        target_email=email,
    )
    invoice_id = invoice["id"]
    await state.clear()

    bd = await get_price_breakdown(gb, duration, users)
    dur_str = (
        f" (+{format_price(bd['duration_surcharge'])})"
        if bd["duration_surcharge"] > 0
        else ""
    )
    user_str = (
        f" (+{format_price(bd['user_surcharge'])})" if bd["user_surcharge"] > 0 else ""
    )

    text = (
        f"💳 <b>پرداخت کارت به کارت جهت تمدید</b>\n\n"
        f"🆔 شماره فاکتور: <code>{invoice_id}</code>\n\n"
        f"📦 سفارش تمدید:\n"
        f"📦 نام سرویس: {email}\n"
        f"⏱ مدت: {duration} روز{dur_str}\n"
        f"👤 تعداد کاربر: {to_persian_digits(users)} کاربر{user_str}\n"
        f"📊 حجم: {format_size_gb(gb)} ({format_price(bd['data_price'])})\n"
        f"💰 مبلغ کل: {format_price(price)}\n\n"
        f"💳 شماره کارت:\n<code>{CARD_NUMBER}</code>\n"
        f"👤 به نام: {CARD_HOLDER}\n\n"
        f"⏱ <b>مهلت پرداخت: {INVOICE_EXPIRY_MINUTES} دقیقه</b>\n\n"
        f"پس از واریز، دکمه «✅ پرداخت کردم» را بزنید."
    )

    await callback.message.edit_text(
        text,
        reply_markup=card_payment_keyboard(invoice_id, CARD_NUMBER, price),
        parse_mode="HTML",
    )
    await callback.answer()
