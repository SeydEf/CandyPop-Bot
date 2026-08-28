from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import (
    ADMIN_CHAT_ID,
    INVOICE_EXPIRY_MINUTES,
    SUB_BASE_URL,
)
from db.discounts import validate_discount_code
from db.models import (
    create_invoice,
    debit_wallet,
    get_balance,
    get_card_config,
    get_invoice,
    set_invoice_message_id,
    set_invoice_receipt,
    update_invoice_status,
)
from keyboards.inline_kb import (
    admin_payment_review_keyboard,
    card_payment_keyboard,
    duration_keyboard,
    payment_method_keyboard,
    sub_config_links_keyboard,
    users_keyboard,
    volume_keyboard,
    wallet_confirm_keyboard,
)
from keyboards.reply_kb import BTN_BUY, main_menu_keyboard
from services import xui_api
from services.pricing import (
    get_price_breakdown,
    get_pricing_config,
)
from utils.formatting import (
    format_price,
    format_size_gb,
    persian_to_english_digits,
    to_persian_digits,
)
from utils.helpers import (
    gb_to_bytes,
    generate_email,
)

logger = logging.getLogger(__name__)
router = Router(name="buy")


class BuyStates(StatesGroup):
    waiting_custom_gb = State()
    waiting_discount_code = State()
    waiting_receipt = State()


async def _get_volume_step_text() -> str:
    config = await get_pricing_config()
    volume_tiers = config["volume_tiers"]
    fallback_rate = config["fallback_gb_rate"]

    tiers_info = ""
    for max_gb, rate in sorted(volume_tiers, key=lambda x: x[0]):
        tiers_info += f"  ▫️ تا {to_persian_digits(max_gb)} گیگ: {format_price(rate)} به ازای هر گیگ\n"
    last_max = volume_tiers[-1][0] if volume_tiers else 100
    tiers_info += f"  ▫️ بالای {to_persian_digits(last_max)} گیگ: {format_price(fallback_rate)} به ازای هر گیگ\n"

    return (
        "🚀 <b>گام ۱ از ۳: انتخاب حجم ترافیک اشتراک</b>\n\n"
        "لطفاً میزان حجم مورد نیاز خود را انتخاب کنید:\n\n"
        f"🎁 <b>تعرفه‌ها و تخفیف‌های پلکانی:</b>\n{tiers_info}\n"
        "حجم مورد نظرت رو از دکمه‌های زیر انتخاب کن یا حجم دلخواهت رو بنویس 👇"
    )


@router.message(Command("buy"))
@router.message(F.text == BTN_BUY)
async def buy_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()

    from db.models import get_shop_status

    shop_status = await get_shop_status()
    if not shop_status["purchases_enabled"]:
        await message.answer(
            "⛔️ <b>فروش اشتراک جدید موقتاً غیرفعال می‌باشد.</b>\n\n"
            "امکان خرید اشتراک جدید در حال حاضر توسط مدیریت متوقف شده است. لطفاً بعداً مراجعه فرمایید.",
            parse_mode="HTML",
        )
        return

    text = await _get_volume_step_text()
    await message.answer(
        text,
        reply_markup=await volume_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "buy_back_volume")
async def buy_back_to_volume(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = await _get_volume_step_text()
    await callback.message.edit_text(
        text,
        reply_markup=await volume_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


async def _get_users_step_text(gb: int, users: int = 1) -> str:
    from services.pricing import calculate_data_price

    config = await get_pricing_config()
    user_surcharge_unit = config["user_surcharge"]
    data_price = await calculate_data_price(gb)
    extra_users = max(0, users - 1)
    users_surcharge_total = extra_users * user_surcharge_unit
    running_total = data_price + users_surcharge_total

    surcharge_text = (
        f" (+{format_price(users_surcharge_total)})"
        if users_surcharge_total > 0
        else " (بدون هزینه اضافه)"
    )

    return (
        f"📋 <b>مشخصات و قیمت مراحل قبلی:</b>\n"
        f"📊 <b>حجم ترافیک:</b> {format_size_gb(gb)} <i>({format_price(data_price)})</i>\n\n"
        f"👤 <b>گام ۲ از ۳: انتخاب تعداد کاربر همزمان (دستگاه)</b>\n\n"
        f"چند نفر یا دستگاه قراره به صورت همزمان از این سرویس استفاده کنن؟\n"
        f"💡 <i>به ازای هر کاربر اضافه، مبلغ +{format_price(user_surcharge_unit)} به اشتراک افزوده می‌شود.</i>\n\n"
        f"👥 <b>تعداد کاربر انتخابی:</b> {to_persian_digits(users)} کاربر{surcharge_text}\n"
        f"💵 <b>مجموع قیمت تا این مرحله:</b> <b>{format_price(running_total)}</b>"
    )


@router.callback_query(F.data.regexp(r"^buy_vol_\d+$"))
async def buy_select_volume(callback: types.CallbackQuery) -> None:
    gb = int(callback.data.split("_")[-1])
    users = 1
    text = await _get_users_step_text(gb, users)
    await callback.message.edit_text(
        text,
        reply_markup=users_keyboard(gb, users),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "buy_vol_custom")
async def buy_custom_volume(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BuyStates.waiting_custom_gb)
    await callback.message.edit_text(
        "✍️ <b>حجم دلخواهت رو وارد کن:</b>\n\n"
        "میزان حجم رو به گیگابایت بصورت عددی ارسال کن.\n"
        "🔸 <b>حداقل حجم:</b> <code>10</code> گیگابایت\n"
        "🔸 <b>مثال:</b> <code>25</code>\n\n"
        "<i>برای انصراف /cancel رو بفرست.</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BuyStates.waiting_custom_gb, F.text)
async def buy_custom_volume_input(message: types.Message, state: FSMContext) -> None:
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(
            "🚫 فرایند خرید لغو شد.",
            reply_markup=main_menu_keyboard(),
        )
        return

    try:
        raw_text = (
            persian_to_english_digits(message.text.strip()) if message.text else ""
        )
        clean_text = raw_text.replace(",", "").replace("،", "").replace(" ", "")
        gb = int(clean_text)
        if gb < 10:
            await message.answer(
                "⚠️ <b>حداقل حجم قابل سفارش ۱۰ گیگابایت می‌باشد.</b>\n"
                "لطفاً عددی معادل ۱۰ گیگابایت یا بیشتر وارد کنید.",
                parse_mode="HTML",
            )
            return
        if gb > 150:
            await message.answer("⚠️ حداکثر حجم قابل سفارش 150 گیگابایت هست.")
            return
    except (ValueError, TypeError):
        await message.answer(
            "⚠️ لطفاً فقط یک عدد انگلیسی یا فارسی معتبر وارد کنید (حداقل ۱۰ گیگابایت).\n🔸 مثال: <code>25</code>",
            parse_mode="HTML",
        )
        return

    await state.update_data(gb=gb)
    data = await state.get_data()
    renew_email = data.get("renew_email")

    if renew_email:
        email = renew_email
        users = data.get("users", 1)
        text = await _get_users_step_text(gb, users)
        from keyboards.inline_kb import renew_users_keyboard

        await message.answer(
            f"🔄 <b>تغییر پلن سرویس «{email}»</b>\n\n{text}",
            reply_markup=renew_users_keyboard(gb, users),
            parse_mode="HTML",
        )
    else:
        users = 1
        text = await _get_users_step_text(gb, users)
        await message.answer(
            text,
            reply_markup=users_keyboard(gb, users),
            parse_mode="HTML",
        )


@router.callback_query(F.data.regexp(r"^buy_users_step_\d+_\d+$"))
async def buy_users_step(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("_")
    gb = int(parts[3])
    users = int(parts[4])
    text = await _get_users_step_text(gb, users)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=users_keyboard(gb, users),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.edit_reply_markup(reply_markup=users_keyboard(gb, users))

    await callback.answer()


@router.callback_query(F.data == "buy_noop")
async def buy_noop(callback: types.CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_back_users_\d+_\d+$"))
async def buy_back_to_users(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parts = callback.data.split("_")
    gb = int(parts[3])
    users = int(parts[4])
    text = await _get_users_step_text(gb, users)
    await callback.message.edit_text(
        text,
        reply_markup=users_keyboard(gb, users),
        parse_mode="HTML",
    )
    await callback.answer()


async def _get_duration_step_text(gb: int, users: int) -> str:
    from services.pricing import calculate_data_price

    config = await get_pricing_config()
    durs = config["duration_surcharges"]
    dur60 = durs.get(60, 0)
    dur90 = durs.get(90, 0)

    data_price = await calculate_data_price(gb)
    extra_users = max(0, users - 1)
    users_surcharge_total = extra_users * config["user_surcharge"]
    base_sum = data_price + users_surcharge_total

    price_30 = base_sum + durs.get(30, 0)
    price_60 = base_sum + dur60
    price_90 = base_sum + dur90

    user_surcharge_str = (
        f" (+{format_price(users_surcharge_total)})"
        if users_surcharge_total > 0
        else " (بدون هزینه اضافه)"
    )

    return (
        f"📋 <b>مشخصات و قیمت مراحل قبلی:</b>\n"
        f"📊 <b>حجم ترافیک:</b> {format_size_gb(gb)} <i>({format_price(data_price)})</i>\n"
        f"👥 <b>ظرفیت کاربر:</b> {to_persian_digits(users)} کاربر<i>{user_surcharge_str}</i>\n"
        f"💵 <b>مجموع قیمت پایه (حجم + کاربر):</b> <b>{format_price(base_sum)}</b>\n\n"
        f"⏱ <b>گام ۳ از ۳: انتخاب مدت زمان اعتبار</b>\n\n"
        f"لطفاً مدت اعتبار سرویس خود را انتخاب کنید:\n\n"
        f"🔹 <b>۱ ماهه (۳۰ روز):</b> بدون هزینه اضافه — <b>قیمت نهایی: {format_price(price_30)}</b>\n"
        f"🔹 <b>۲ ماهه (۶۰ روز):</b> +{format_price(dur60)} — <b>قیمت نهایی: {format_price(price_60)}</b>\n"
        f"🔹 <b>۳ ماهه (۹۰ روز):</b> +{format_price(dur90)} — <b>قیمت نهایی: {format_price(price_90)}</b>"
    )


@router.callback_query(F.data.regexp(r"^buy_users_confirm_\d+_\d+$"))
async def buy_users_confirm(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("_")
    gb = int(parts[3])
    users = int(parts[4])
    text = await _get_duration_step_text(gb, users)
    await callback.message.edit_text(
        text,
        reply_markup=duration_keyboard(gb, users),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_back_duration_\d+_\d+$"))
async def buy_back_to_duration(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.clear()
    parts = callback.data.split("_")
    gb = int(parts[3])
    users = int(parts[4])
    text = await _get_duration_step_text(gb, users)
    await callback.message.edit_text(
        text,
        reply_markup=duration_keyboard(gb, users),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_dur_\d+_\d+_\d+$"))
async def buy_select_duration(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("_")
    gb = int(parts[2])
    users = int(parts[3])
    duration = int(parts[4])

    bd = await get_price_breakdown(gb, duration, users)
    price = bd["total_price"]

    dur_str = (
        f" (+{format_price(bd['duration_surcharge'])})"
        if bd["duration_surcharge"] > 0
        else ""
    )
    user_str = (
        f" (+{format_price(bd['user_surcharge'])})" if bd["user_surcharge"] > 0 else ""
    )

    text = (
        f"📋 <b>پیش‌فاکتور خرید اشتراک جدید</b>\n\n"
        f"📊 <b>حجم ترافیک:</b> {format_size_gb(gb)} ({format_price(bd['data_price'])})\n"
        f"👥 <b>ظرفیت کاربر:</b> {to_persian_digits(users)} کاربر{user_str}\n"
        f"⏱ <b>مدت زمان:</b> {duration} روز{dur_str}\n\n"
        f"💎 <b>مبلغ کل قابل پرداخت:</b> {format_price(price)}\n\n"
        f"💳 لطفاً روش پرداخت مورد نظر خود را انتخاب کنید:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=payment_method_keyboard(duration, users, gb, price),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_pay_wallet_\d+_\d+_\d+_\d+$"))
async def buy_wallet_payment(callback: types.CallbackQuery) -> None:
    if not callback.from_user:
        return
    parts = callback.data.split("_")
    duration = int(parts[3])
    users = int(parts[4])
    gb = int(parts[5])
    price = int(parts[6])

    balance = await get_balance(callback.from_user.id)

    if balance < price:
        await callback.answer(
            f"❌ موجودی کیف پول شما برای این سفارش کافی نیست!\n\n"
            f"💰 موجودی فعلی: {format_price(balance)}\n"
            f"💵 مبلغ مورد نیاز: {format_price(price)}",
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
        f"👛 <b>پرداخت آنی از کیف پول</b>\n\n"
        f"🛍 <b>جزئیات سفارش:</b>\n"
        f"⏱ مدت زمان: {duration} روز{dur_str}\n"
        f"👥 تعداد کاربر: {to_persian_digits(users)} کاربر{user_str}\n"
        f"📊 حجم ترافیک: {format_size_gb(gb)} ({format_price(bd['data_price'])})\n"
        f"💎 مبلغ کل: <b>{format_price(price)}</b>\n\n"
        f"💳 موجودی فعلی حساب: {format_price(balance)}\n"
        f"📉 موجودی پس از پرداخت: {format_price(after_balance)}\n\n"
        f"آیا برای ثبت و دریافت کانفیگ مطمئن هستید؟"
    )
    await callback.message.edit_text(
        text,
        reply_markup=wallet_confirm_keyboard(duration, users, gb, price),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_wallet_confirm_\d+_\d+_\d+_\d+$"))
async def buy_wallet_confirm(
    callback: types.CallbackQuery, bot: Bot, state: FSMContext
) -> None:
    if not callback.from_user:
        return
    parts = callback.data.split("_")
    duration = int(parts[3])
    users = int(parts[4])
    gb = int(parts[5])
    price = int(parts[6])
    tg_id = callback.from_user.id

    data = await state.get_data()
    discount_code = data.get("discount_code")
    original_price = data.get("original_price", price)

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
        payment_method="wallet",
        discount_code=discount_code,
        original_amount=original_price,
    )
    await update_invoice_status(invoice["id"], "approved")

    if discount_code:
        from db.discounts import increment_discount_usage

        await increment_discount_usage(discount_code, tg_id)

    from db.models import process_referral_commission

    await process_referral_commission(tg_id, price, bot)

    await callback.message.edit_text(
        "🚀 <b>در حال ایجاد کانفیگ اختصاصی شما... لطفاً چند ثانیه صبر کنید.</b>",
        parse_mode="HTML",
    )

    try:
        username = callback.from_user.username
        email = generate_email(tg_id, username)
        total_bytes = gb_to_bytes(gb)

        from db.models import (
            get_active_client_group,
            get_active_inbound_ids,
            get_start_first_use_config,
        )

        start_first_use = await get_start_first_use_config()
        if start_first_use and duration > 0:
            expiry_ms = -int(duration * 86400 * 1000)
        else:
            expiry_ms = (
                int((time.time() + duration * 86400) * 1000) if duration > 0 else 0
            )

        active_inbound_ids = await get_active_inbound_ids()
        active_group = await get_active_client_group()

        await xui_api.add_client(
            email=email,
            total_gb=total_bytes,
            expiry_time=expiry_ms,
            tg_id=tg_id,
            inbound_ids=active_inbound_ids,
            limit_ip=users,
            group=active_group,
        )

        client = await xui_api.get_client(email)
        sub_id = client.get("subId", "") if client else ""

        sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

        success_text = (
            f"🎉 <b>تبریک! اشتراک شما با موفقیت فعال شد</b>\n\n"
            f"🔹 <b>شناسه سرویس:</b> <code>{email}</code>\n"
            f"⏱ <b>مدت اعتبار:</b> {duration} روز\n"
            f"👥 <b>تعداد کاربر همزمان:</b> {to_persian_digits(users)} کاربر\n"
            f"📊 <b>حجم اشتراک:</b> {format_size_gb(gb)}\n"
            f"💳 <b>روش پرداخت:</b> کیف پول حساب\n"
            f"👛 <b>موجودی باقیمانده:</b> {format_price(new_balance)}\n\n"
            f"🔗 <b>لینک اتصال ساب‌اسکریپشن:</b>\n<code>{sub_link}</code>\n\n"
            f"💡 <i>کافیه لینک بالا یا بارکد رو توی برنامه مورد نظرتون کپی و وارد کنید.</i>"
        )

        await state.clear()
        if sub_id:
            from aiogram.types import BufferedInputFile
            from utils.helpers import generate_qr

            qr_buf = generate_qr(sub_link)
            photo = BufferedInputFile(qr_buf.getvalue(), filename="qrcode.png")
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer_photo(
                photo=photo,
                caption=success_text,
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
        logger.exception("Failed to create client for user %d", tg_id)
        from db.models import credit_wallet

        await credit_wallet(tg_id, price)
        await callback.message.edit_text(
            f"⚠️ <b>خطا در راه‌اندازی اشتراک:</b> مبلغ پرداختی فوراً به کیف پول شما برگشت داده شد.\nعلت خطا: {e}",
            parse_mode="HTML",
        )

    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_discount_apply_\d+_\d+_\d+_\d+$"))
async def buy_discount_apply_prompt(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    parts = callback.data.split("_")
    duration = int(parts[3])
    users = int(parts[4])
    gb = int(parts[5])
    price = int(parts[6])

    await state.set_state(BuyStates.waiting_discount_code)
    await state.update_data(
        duration=duration,
        users=users,
        gb=gb,
        original_price=price,
    )
    await callback.message.edit_text(
        "🏷️ <b>کد تخفیف دارید؟</b>\n\n"
        "کد تخفیف خود را ارسال کنید تا روی مبلغ سفارش اعمال شود:\n\n"
        "<i>جهت انصراف /cancel را ارسال کنید.</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BuyStates.waiting_discount_code, F.text)
async def buy_discount_process(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        return

    data = await state.get_data()
    duration = data.get("duration", 30)
    users = data.get("users", 1)
    gb = data.get("gb", 10)
    original_price = data.get("original_price", 0)

    if message.text.strip() == "/cancel":
        await state.clear()
        bd = await get_price_breakdown(gb, duration, users)
        dur_str = (
            f" (+{format_price(bd['duration_surcharge'])})"
            if bd["duration_surcharge"] > 0
            else ""
        )
        user_str = (
            f" (+{format_price(bd['user_surcharge'])})"
            if bd["user_surcharge"] > 0
            else ""
        )
        text = (
            f"📋 <b>پیش‌فاکتور سفارش شما</b>\n\n"
            f"⏱ <b>مدت اعتبار:</b> {duration} روز{dur_str}\n"
            f"👥 <b>ظرفیت کاربر:</b> {to_persian_digits(users)} کاربر{user_str}\n"
            f"📊 <b>حجم ترافیک:</b> {format_size_gb(gb)} ({format_price(bd['data_price'])})\n\n"
            f"💎 <b>مبلغ کل قابل پرداخت:</b> {format_price(original_price)}\n\n"
            f"💳 لطفاً روش پرداخت مورد نظرتون رو انتخاب کنید:"
        )
        await message.answer(
            text,
            reply_markup=payment_method_keyboard(duration, users, gb, original_price),
            parse_mode="HTML",
        )
        return

    code = message.text.strip()
    is_valid, err_msg, dc = await validate_discount_code(code, message.from_user.id)

    if not is_valid or not dc:
        await message.answer(
            f"⚠️ <b>{err_msg}</b>\n\n"
            "لطفاً کد تخفیف را مجدداً و با دقت وارد کنید، یا در صورت تمایل دستور /cancel را ارسال نمایید.",
            parse_mode="HTML",
        )
        return

    percent = dc["discount_percent"]
    discount_amount = int(round(original_price * percent / 100))
    final_price = max(0, original_price - discount_amount)
    clean_code = dc["code"]

    await state.update_data(
        discount_code=clean_code,
        discount_percent=percent,
        final_price=final_price,
        price=final_price,
    )

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
        f"🎉 <b>کد تخفیف با موفقیت اعمال شد!</b>\n\n"
        f"⏱ <b>مدت اعتبار:</b> {duration} روز{dur_str}\n"
        f"👥 <b>ظرفیت کاربر:</b> {to_persian_digits(users)} کاربر{user_str}\n"
        f"📊 <b>حجم ترافیک:</b> {format_size_gb(gb)} ({format_price(bd['data_price'])})\n\n"
        f"💵 مبلغ اولیه: <s>{format_price(original_price)}</s>\n"
        f"🏷️ کد تخفیف: <code>{clean_code}</code> (<b>{to_persian_digits(percent)}٪ تخفیف</b>)\n"
        f"🎁 سود شما از این خرید: -{format_price(discount_amount)}\n\n"
        f"💎 <b>مبلغ نهایی و قابل پرداخت:</b> {format_price(final_price)}\n\n"
        f"💳 روش پرداخت مورد نظرتون رو انتخاب کنید:"
    )
    await message.answer(
        text,
        reply_markup=payment_method_keyboard(
            duration, users, gb, final_price, has_discount=True
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.regexp(r"^buy_discount_remove_\d+_\d+_\d+_\d+$"))
async def buy_discount_remove(callback: types.CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split("_")
    duration = int(parts[3])
    users = int(parts[4])
    gb = int(parts[5])
    _ = int(parts[6])

    await state.update_data(discount_code=None, discount_percent=None, final_price=None)
    bd = await get_price_breakdown(gb, duration, users)
    original_price = bd["total_price"]

    dur_str = (
        f" (+{format_price(bd['duration_surcharge'])})"
        if bd["duration_surcharge"] > 0
        else ""
    )
    user_str = (
        f" (+{format_price(bd['user_surcharge'])})" if bd["user_surcharge"] > 0 else ""
    )

    text = (
        f"📋 <b>پیش‌فاکتور سفارش شما</b>\n\n"
        f"⏱ <b>مدت اعتبار:</b> {duration} روز{dur_str}\n"
        f"👥 <b>ظرفیت کاربر:</b> {to_persian_digits(users)} کاربر{user_str}\n"
        f"📊 <b>حجم ترافیک:</b> {format_size_gb(gb)} ({format_price(bd['data_price'])})\n\n"
        f"💎 <b>مبلغ کل قابل پرداخت:</b> {format_price(original_price)}\n\n"
        f"💳 لطفاً روش پرداخت مورد نظرتون رو انتخاب کنید:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=payment_method_keyboard(duration, users, gb, original_price),
        parse_mode="HTML",
    )
    await callback.answer("✅ کد تخفیف با موفقیت حذف شد.")


@router.callback_query(F.data.regexp(r"^buy_pay_card_\d+_\d+_\d+_\d+$"))
async def buy_card_payment(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    parts = callback.data.split("_")
    duration = int(parts[3])
    users = int(parts[4])
    gb = int(parts[5])
    price = int(parts[6])
    tg_id = callback.from_user.id

    data = await state.get_data()
    discount_code = data.get("discount_code")
    original_price = data.get("original_price", price)
    final_price = data.get("final_price")

    payable_amount = (
        final_price if (discount_code and final_price is not None) else price
    )

    invoice = await create_invoice(
        tg_id=tg_id,
        amount=payable_amount,
        duration_days=duration,
        data_gb=gb,
        users_count=users,
        payment_method="card",
        discount_code=discount_code,
        original_amount=original_price,
    )
    invoice_id = invoice["id"]

    bd = await get_price_breakdown(gb, duration, users)
    dur_str = (
        f" (+{format_price(bd['duration_surcharge'])})"
        if bd["duration_surcharge"] > 0
        else ""
    )
    user_str = (
        f" (+{format_price(bd['user_surcharge'])})" if bd["user_surcharge"] > 0 else ""
    )

    card_config = await get_card_config()
    card_number = card_config["card_number"]
    card_holder = card_config["card_holder"]

    disc_info = ""
    if discount_code:
        disc_info = (
            f"💵 <b>مبلغ اولیه:</b> <s>{format_price(original_price)}</s>\n"
            f"🏷️ <b>کد تخفیف:</b> <code>{discount_code}</code>\n"
        )

    text = (
        f"💳 <b>سفارش شماره {invoice_id} ثبت شد!</b>\n\n"
        f"📋 <b>جزئیات سفارش شما:</b>\n"
        f"⏱ <b>مدت اعتبار:</b> {duration} روز{dur_str}\n"
        f"👥 <b>ظرفیت کاربر:</b> {to_persian_digits(users)} کاربر{user_str}\n"
        f"📊 <b>حجم ترافیک:</b> {format_size_gb(gb)} ({format_price(bd['data_price'])})\n"
        f"{disc_info}"
        f"💎 <b>مبلغ قابل پرداخت:</b> <b>{format_price(payable_amount)}</b>\n\n"
        f"💳 <b>شماره کارت جهت واریز:</b>\n<code>{card_number}</code>\n"
        f"👤 <b>به نام:</b> {card_holder}\n\n"
        f"⏳ <b>مهلت پرداخت: {to_persian_digits(INVOICE_EXPIRY_MINUTES)} دقیقه</b>\n\n"
        f"✨ <i>نکته: پس از انتقال وجه، حتماً روی دکمه «✅ پرداخت کردم» کلیک کنید و رسید خود را ارسال نمایید تا اشتراک فوراً بررسی و فعال گردد.</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=card_payment_keyboard(invoice_id, card_number, payable_amount),
        parse_mode="HTML",
    )
    await callback.answer()

    asyncio.create_task(
        _expire_invoice_after(invoice_id, callback, INVOICE_EXPIRY_MINUTES * 60)
    )


async def _expire_invoice_after(
    invoice_id: str,
    callback: types.CallbackQuery,
    seconds: int,
) -> None:
    await asyncio.sleep(seconds)
    invoice = await get_invoice(invoice_id)
    if invoice and invoice["status"] == "pending":
        await update_invoice_status(invoice_id, "expired")
        try:
            if callback.message:
                await callback.message.edit_text(
                    f"⌛️ <b>فاکتور شماره {invoice_id} منقضی گردید.</b>\n\n"
                    "مهلت زمان پرداخت به پایان رسیده است. در صورت تمایل می‌توانید سفارش جدیدی ثبت بفرمایید.",
                    parse_mode="HTML",
                )
        except Exception:
            pass


@router.callback_query(F.data.regexp(r"^copy_card_"))
async def copy_card_number(callback: types.CallbackQuery) -> None:
    card_config = await get_card_config()
    card_number = card_config["card_number"]
    await callback.answer(f"شماره کارت کپی شد: {card_number}", show_alert=True)


@router.callback_query(F.data.regexp(r"^copy_amount_"))
async def copy_amount(callback: types.CallbackQuery) -> None:
    invoice_id = callback.data.split("_")[-1]
    invoice = await get_invoice(invoice_id)
    if invoice:
        await callback.answer(
            f"مبلغ قابل پرداخت: {format_price(invoice['amount'])}", show_alert=True
        )
    else:
        await callback.answer("⚠️ فاکتور مورد نظر یافت نشد.", show_alert=True)


@router.callback_query(F.data.regexp(r"^paid_"))
async def paid_button(callback: types.CallbackQuery, state: FSMContext) -> None:
    invoice_id = callback.data.split("_")[-1]
    invoice = await get_invoice(invoice_id)

    if not invoice:
        await callback.answer("❌ متأسفانه فاکتور پیدا نشد.", show_alert=True)
        return

    if invoice["status"] != "pending":
        await callback.answer(
            "⚠️ این فاکتور قبلاً پردازش شده یا منقضی گردیده است.", show_alert=True
        )
        return

    await state.set_state(BuyStates.waiting_receipt)
    await state.update_data(invoice_id=invoice_id)

    await callback.message.edit_text(
        "📸 <b>ارسال رسید یا شماره پیگیری واریز</b>\n\n"
        "لطفاً تصویر رسید پرداخت بانکی یا شماره پیگیری تراکنش خود را در همین بخش ارسال کنید.\n\n"
        "💡 <i>در صورت انصراف، می‌توانید دستور /cancel را بفرستید.</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BuyStates.waiting_receipt, F.photo)
async def receive_receipt_photo(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not message.from_user or not message.photo:
        return

    data = await state.get_data()
    invoice_id = data.get("invoice_id")
    if not invoice_id:
        await state.clear()
        return

    invoice = await get_invoice(invoice_id)
    if not invoice or invoice["status"] != "pending":
        await message.answer(
            "⚠️ این فاکتور منقضی شده یا قبلاً مورد پردازش قرار گرفته است."
        )
        await state.clear()
        return

    file_id = message.photo[-1].file_id
    await set_invoice_receipt(invoice_id, file_id=file_id)
    await update_invoice_status(invoice_id, "paid")
    await state.clear()

    is_topup = invoice.get("target_email") == "TOPUP" or (
        invoice.get("duration_days") == 0 and invoice.get("data_gb") == 0
    )

    if is_topup:
        user_msg = (
            "🎉 <b>رسید پرداخت شما با موفقیت دریافت شد!</b>\n\n"
            "درخواست افزایش موجودی کیف پول شما در صف بررسی توسط تیم پشتیبانی قرار گرفت. "
            "به‌محض تأیید، موجودی کیف پول شما شارژ خواهد شد. 👛"
        )
    else:
        user_msg = (
            "🎉 <b>رسید پرداخت شما با موفقیت دریافت شد!</b>\n\n"
            "سفارش شما در صف بررسی توسط تیم پشتیبانی قرار گرفت. "
            "به‌محض تأیید، کانفیگ اشتراک به همراه راهنمای اتصال برای شما ارسال خواهد شد. 🚀"
        )

    await message.answer(
        user_msg,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )

    users_count = invoice.get("users_count", 1)
    if is_topup:
        admin_text = (
            f"👛 <b>درخواست افزایش موجودی کیف پول (کارت به کارت)</b>\n\n"
            f"🆔 فاکتور: <code>{invoice_id}</code>\n"
            f"👤 کاربر: <code>{message.from_user.id}</code>"
        )
        if message.from_user.username:
            admin_text += f" (@{message.from_user.username})"
        admin_text += (
            f"\n\n💰 <b>مبلغ افزایش موجودی:</b> {format_price(invoice['amount'])}\n"
        )
    else:
        disc_code = invoice.get("discount_code")
        orig_amount = invoice.get("original_amount") or invoice["amount"]
        disc_info = ""
        if disc_code and orig_amount > invoice["amount"]:
            disc_info = (
                f"🏷️ <b>کد تخفیف:</b> <code>{disc_code}</code>\n"
                f"💵 <b>مبلغ اولیه:</b> <s>{format_price(orig_amount)}</s>\n"
            )

        admin_text = (
            f"🔔 <b>درخواست تأیید پرداخت اشتراک (کارت به کارت)</b>\n\n"
            f"🆔 فاکتور: <code>{invoice_id}</code>\n"
            f"👤 کاربر: <code>{message.from_user.id}</code>"
        )
        if message.from_user.username:
            admin_text += f" (@{message.from_user.username})"
        admin_text += (
            f"\n\n📦 <b>جزئیات سفارش:</b>\n"
            f"⏱ مدت: {invoice['duration_days']} روز\n"
            f"👤 تعداد کاربر: {to_persian_digits(users_count)} کاربر\n"
            f"📊 حجم: {format_size_gb(invoice['data_gb'])}\n"
            f"{disc_info}"
            f"💰 <b>مبلغ واریزی نهایی (تخفیف‌خورده):</b> <b>{format_price(invoice['amount'])}</b>\n"
        )

    from db.models import get_all_admins, has_admin_permission

    admin_ids = [ADMIN_CHAT_ID]
    for adm in await get_all_admins():
        a_id = adm["tg_id"]
        if a_id > 0 and a_id not in admin_ids:
            if await has_admin_permission(a_id, "approve_invoices"):
                admin_ids.append(a_id)

    primary_msg_id = 0
    for a_id in admin_ids:
        try:
            sent_msg = await bot.send_photo(
                chat_id=a_id,
                photo=file_id,
                caption=admin_text,
                reply_markup=admin_payment_review_keyboard(invoice_id),
                parse_mode="HTML",
            )
            if primary_msg_id == 0:
                primary_msg_id = sent_msg.message_id
        except Exception as e:
            logger.warning("Failed to send invoice review photo to %d: %s", a_id, e)

    if primary_msg_id > 0:
        await set_invoice_message_id(invoice_id, primary_msg_id)


@router.message(BuyStates.waiting_receipt, F.text)
async def receive_receipt_text(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not message.from_user or not message.text:
        return

    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(
            "❌ فرآیند ارسال رسید لغو شد.",
            reply_markup=main_menu_keyboard(),
        )
        return

    data = await state.get_data()
    invoice_id = data.get("invoice_id")
    if not invoice_id:
        await state.clear()
        return

    invoice = await get_invoice(invoice_id)
    if not invoice or invoice["status"] != "pending":
        await message.answer(
            "⚠️ این فاکتور منقضی شده یا قبلاً مورد پردازش قرار گرفته است."
        )
        await state.clear()
        return

    receipt_text = message.text.strip()
    await set_invoice_receipt(invoice_id, text=receipt_text)
    await update_invoice_status(invoice_id, "paid")
    await state.clear()

    is_topup = invoice.get("target_email") == "TOPUP" or (
        invoice.get("duration_days") == 0 and invoice.get("data_gb") == 0
    )

    if is_topup:
        user_msg = (
            "🎉 <b>اطلاعات پرداخت شما با موفقیت دریافت شد!</b>\n\n"
            "درخواست افزایش موجودی کیف پول شما در صف بررسی توسط تیم پشتیبانی قرار گرفت. "
            "به‌محض تأیید، موجودی کیف پول شما شارژ خواهد شد. 👛"
        )
    else:
        user_msg = (
            "🎉 <b>اطلاعات پرداخت شما با موفقیت دریافت شد!</b>\n\n"
            "سفارش شما در صف بررسی توسط تیم پشتیبانی قرار گرفت. "
            "به‌محض تأیید، کانفیگ اشتراک به همراه راهنمای اتصال برای شما ارسال خواهد شد. 🚀"
        )

    await message.answer(
        user_msg,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )

    users_count = invoice.get("users_count", 1)
    if is_topup:
        admin_text = (
            f"👛 <b>درخواست افزایش موجودی کیف پول (متنی)</b>\n\n"
            f"🆔 فاکتور: <code>{invoice_id}</code>\n"
            f"👤 کاربر: <code>{message.from_user.id}</code>"
        )
        if message.from_user.username:
            admin_text += f" (@{message.from_user.username})"
        admin_text += (
            f"\n\n💰 <b>مبلغ افزایش موجودی:</b> {format_price(invoice['amount'])}\n\n"
            f"📝 متن رسید:\n<code>{receipt_text}</code>"
        )
    else:
        admin_text = (
            f"🔔 <b>درخواست تأیید پرداخت اشتراک (متنی)</b>\n\n"
            f"🆔 فاکتور: <code>{invoice_id}</code>\n"
            f"👤 کاربر: <code>{message.from_user.id}</code>"
        )
        if message.from_user.username:
            admin_text += f" (@{message.from_user.username})"
        admin_text += (
            f"\n\n📦 <b>جزئیات سفارش:</b>\n"
            f"⏱ مدت: {invoice['duration_days']} روز\n"
            f"👤 تعداد کاربر: {to_persian_digits(users_count)} کاربر\n"
            f"📊 حجم: {format_size_gb(invoice['data_gb'])}\n"
            f"💰 مبلغ: {format_price(invoice['amount'])}\n\n"
            f"📝 متن رسید:\n<code>{receipt_text}</code>"
        )

    from db.models import get_all_admins, has_admin_permission

    admin_ids = [ADMIN_CHAT_ID]
    for adm in await get_all_admins():
        a_id = adm["tg_id"]
        if a_id > 0 and a_id not in admin_ids:
            if await has_admin_permission(a_id, "approve_invoices"):
                admin_ids.append(a_id)

    primary_msg_id = 0
    for a_id in admin_ids:
        try:
            sent_msg = await bot.send_message(
                chat_id=a_id,
                text=admin_text,
                reply_markup=admin_payment_review_keyboard(invoice_id),
                parse_mode="HTML",
            )
            if primary_msg_id == 0:
                primary_msg_id = sent_msg.message_id
        except Exception as e:
            logger.warning("Failed to send invoice review text to %d: %s", a_id, e)

    if primary_msg_id > 0:
        await set_invoice_message_id(invoice_id, primary_msg_id)


@router.callback_query(F.data == "buy_cancel")
async def buy_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "❌ فرآیند خرید لغو گردید. در هر زمان می‌توانید مجدداً اقدام فرمایید.",
        parse_mode="HTML",
    )
    await callback.answer()
