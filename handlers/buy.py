"""
Buy Subscription handler.

Multi-step purchase flow:
  Step 1 — Duration (30/60/90 days)
  Step 2 — Data Volume (predefined tiers + custom)
  Step 3 — Payment Method (wallet / card-to-card)

Uses FSM (aiogram states) for the custom-volume text input and receipt upload.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import (
    ADMIN_CHAT_ID,
    CARD_HOLDER,
    CARD_NUMBER,
    INBOUND_IDS,
    INVOICE_EXPIRY_MINUTES,
    SUB_BASE_URL,
    VOLUME_TIERS,
)
from db.models import (
    create_invoice,
    create_subscription,
    debit_wallet,
    get_balance,
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
    volume_keyboard,
    wallet_confirm_keyboard,
)
from keyboards.reply_kb import BTN_BUY, main_menu_keyboard
from services import xui_api
from utils.formatting import format_price, format_size_gb, to_persian_digits
from utils.helpers import (
    calculate_custom_price,
    gb_to_bytes,
    generate_email,
    generate_service_name,
)

logger = logging.getLogger(__name__)
router = Router(name="buy")


# ──────────────────────────── FSM States ────────────────────────────


class BuyStates(StatesGroup):
    waiting_custom_gb = State()
    waiting_receipt = State()


# ──────────────────────────── Step 1: Duration ────────────────────────────


@router.message(F.text == BTN_BUY)
async def buy_start(message: types.Message, state: FSMContext) -> None:
    """Start the buy flow — show duration selection."""
    await state.clear()
    await message.answer(
        "⏱ <b>مدت زمان اشتراک را انتخاب کنید:</b>",
        reply_markup=duration_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "buy_back_duration")
async def buy_back_to_duration(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Go back to duration selection."""
    await state.clear()
    await callback.message.edit_text(  # type: ignore[union-attr]
        "⏱ <b>مدت زمان اشتراک را انتخاب کنید:</b>",
        reply_markup=duration_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


# ──────────────────────────── Step 2: Volume ────────────────────────────


@router.callback_query(F.data.startswith("buy_dur_"))
async def buy_select_duration(callback: types.CallbackQuery) -> None:
    """Duration selected — show volume selection."""
    duration = int(callback.data.split("_")[-1])  # type: ignore[union-attr]
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"📊 <b>حجم اشتراک {to_persian_digits(duration)} روزه را انتخاب کنید:</b>",
        reply_markup=volume_keyboard(duration),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_back_volume_\d+$"))
async def buy_back_to_volume(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Go back to volume selection from payment step."""
    await state.clear()
    parts = callback.data.split("_")  # type: ignore[union-attr]
    duration = int(parts[-1])
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"📊 <b>حجم اشتراک {to_persian_digits(duration)} روزه را انتخاب کنید:</b>",
        reply_markup=volume_keyboard(duration),
        parse_mode="HTML",
    )
    await callback.answer()


# ──────────────────────────── Custom Volume Input ────────────────────────────


@router.callback_query(F.data.regexp(r"^buy_vol_\d+_custom$"))
async def buy_custom_volume(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Prompt user to enter custom GB amount."""
    parts = callback.data.split("_")  # type: ignore[union-attr]
    duration = int(parts[2])
    await state.set_state(BuyStates.waiting_custom_gb)
    await state.update_data(duration=duration)
    await callback.message.edit_text(  # type: ignore[union-attr]
        "📝 <b>لطفاً حجم مورد نظر خود را به گیگابایت وارد کنید:</b>\n"
        "مثال: <code>25</code>\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BuyStates.waiting_custom_gb, F.text)
async def buy_custom_volume_input(message: types.Message, state: FSMContext) -> None:
    """Process custom GB input."""
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(
            "❌ عملیات خرید لغو شد.",
            reply_markup=main_menu_keyboard(),
        )
        return

    try:
        gb = int(message.text.strip())  # type: ignore[union-attr]
        if gb < 1:
            raise ValueError
        if gb > 500:
            await message.answer("❌ حداکثر حجم قابل سفارش ۵۰۰ گیگابایت است.")
            return
    except (ValueError, TypeError):
        await message.answer("❌ لطفاً یک عدد صحیح وارد کنید. مثال: <code>25</code>", parse_mode="HTML")
        return

    data = await state.get_data()
    duration = data["duration"]
    price = calculate_custom_price(gb)
    await state.clear()

    text = (
        f"📦 <b>خلاصه سفارش</b>\n\n"
        f"⏱ مدت: {to_persian_digits(duration)} روز\n"
        f"📊 حجم: {format_size_gb(gb)}\n"
        f"💰 قیمت: {format_price(price)}\n\n"
        f"💳 <b>روش پرداخت را انتخاب کنید:</b>"
    )
    await message.answer(
        text,
        reply_markup=payment_method_keyboard(duration, gb, price),
        parse_mode="HTML",
    )


# ──────────────────────────── Step 3: Volume Selected → Payment ────────────────────────────


@router.callback_query(F.data.regexp(r"^buy_vol_\d+_\d+$"))
async def buy_select_volume(callback: types.CallbackQuery) -> None:
    """Predefined volume selected — show payment options."""
    parts = callback.data.split("_")  # type: ignore[union-attr]
    duration = int(parts[2])
    gb = int(parts[3])
    price = VOLUME_TIERS.get(gb, 0)

    if price == 0:
        price = calculate_custom_price(gb)

    text = (
        f"📦 <b>خلاصه سفارش</b>\n\n"
        f"⏱ مدت: {to_persian_digits(duration)} روز\n"
        f"📊 حجم: {format_size_gb(gb)}\n"
        f"💰 قیمت: {format_price(price)}\n\n"
        f"💳 <b>روش پرداخت را انتخاب کنید:</b>"
    )
    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=payment_method_keyboard(duration, gb, price),
        parse_mode="HTML",
    )
    await callback.answer()


# ──────────────────────────── Wallet Payment ────────────────────────────


@router.callback_query(F.data.regexp(r"^buy_pay_wallet_\d+_\d+_\d+$"))
async def buy_wallet_payment(callback: types.CallbackQuery) -> None:
    """Show wallet balance and confirmation."""
    if not callback.from_user:
        return
    parts = callback.data.split("_")  # type: ignore[union-attr]
    duration = int(parts[3])
    gb = int(parts[4])
    price = int(parts[5])

    balance = await get_balance(callback.from_user.id)

    if balance < price:
        await callback.answer(
            f"❌ موجودی کیف پول شما کافی نیست.\n"
            f"موجودی: {format_price(balance)}\n"
            f"مبلغ مورد نیاز: {format_price(price)}",
            show_alert=True,
        )
        return

    after_balance = balance - price
    text = (
        f"💰 <b>پرداخت از کیف پول</b>\n\n"
        f"📦 سفارش: {format_size_gb(gb)} / {to_persian_digits(duration)} روز\n"
        f"💰 مبلغ: {format_price(price)}\n\n"
        f"👛 موجودی فعلی: {format_price(balance)}\n"
        f"👛 موجودی پس از خرید: {format_price(after_balance)}\n\n"
        f"آیا تأیید می‌کنید؟"
    )
    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=wallet_confirm_keyboard(duration, gb, price),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^buy_wallet_confirm_\d+_\d+_\d+$"))
async def buy_wallet_confirm(
    callback: types.CallbackQuery, bot: Bot
) -> None:
    """Confirm wallet purchase — debit, create client, deliver config."""
    if not callback.from_user:
        return
    parts = callback.data.split("_")  # type: ignore[union-attr]
    duration = int(parts[3])
    gb = int(parts[4])
    price = int(parts[5])
    tg_id = callback.from_user.id

    # Debit wallet
    try:
        new_balance = await debit_wallet(tg_id, price)
    except ValueError:
        await callback.answer("❌ موجودی کیف پول شما کافی نیست.", show_alert=True)
        return

    await callback.message.edit_text(  # type: ignore[union-attr]
        "⏳ در حال ساخت اشتراک...",
        parse_mode="HTML",
    )

    # Create client in X-UI
    try:
        email = generate_email(tg_id)
        service_name = generate_service_name(tg_id)
        total_bytes = gb_to_bytes(gb)
        expiry_ms = int((time.time() + duration * 86400) * 1000)

        result = await xui_api.add_client(
            email=email,
            total_gb=total_bytes,
            expiry_time=expiry_ms,
            tg_id=tg_id,
            inbound_ids=INBOUND_IDS,
        )

        # Get the created client to retrieve subId
        client = await xui_api.get_client(email)
        sub_id = client.get("subId", "") if client else ""

        # Save subscription locally
        await create_subscription(
            tg_id=tg_id,
            email=email,
            sub_id=sub_id,
            service_name=service_name,
            data_gb=gb,
            duration_days=duration,
        )

        # Build subscription link
        sub_link = f"{SUB_BASE_URL}/{sub_id}" if sub_id else "نامشخص"

        # Get individual config links
        config_links = await xui_api.get_client_links(email)
        links_text = ""
        if config_links:
            links_text = "\n\n🔗 <b>لینک‌های کانفیگ:</b>\n"
            for i, link in enumerate(config_links, 1):
                links_text += f"<code>{link}</code>\n\n"

        success_text = (
            f"✅ <b>اشتراک شما با موفقیت ایجاد شد!</b>\n\n"
            f"📦 نام سرویس: {service_name}\n"
            f"⏱ مدت: {to_persian_digits(duration)} روز\n"
            f"📊 حجم: {format_size_gb(gb)}\n"
            f"💰 روش پرداخت: کیف پول\n"
            f"👛 موجودی جدید: {format_price(new_balance)}\n\n"
            f"🔗 <b>لینک اشتراک:</b>\n<code>{sub_link}</code>"
            f"{links_text}"
        )

        await callback.message.edit_text(  # type: ignore[union-attr]
            success_text,
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception("Failed to create client for user %d", tg_id)
        # Refund on failure
        from db.models import credit_wallet
        await credit_wallet(tg_id, price)
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"❌ خطا در ساخت اشتراک. مبلغ به کیف پول شما بازگشت داده شد.\n"
            f"خطا: {e}",
            parse_mode="HTML",
        )

    await callback.answer()


# ──────────────────────────── Card-to-Card Payment ────────────────────────────


@router.callback_query(F.data.regexp(r"^buy_pay_card_\d+_\d+_\d+$"))
async def buy_card_payment(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Generate invoice and show card payment details."""
    if not callback.from_user:
        return
    parts = callback.data.split("_")  # type: ignore[union-attr]
    duration = int(parts[3])
    gb = int(parts[4])
    price = int(parts[5])
    tg_id = callback.from_user.id

    # Create invoice
    invoice = await create_invoice(tg_id, price, duration, gb)
    invoice_id = invoice["id"]

    card_display = " ".join(
        [CARD_NUMBER[i: i + 4] for i in range(0, len(CARD_NUMBER), 4)]
    )

    text = (
        f"💳 <b>پرداخت کارت به کارت</b>\n\n"
        f"🆔 شماره فاکتور: <code>{invoice_id}</code>\n\n"
        f"📦 سفارش: {format_size_gb(gb)} / {to_persian_digits(duration)} روز\n"
        f"💰 مبلغ: {format_price(price)}\n\n"
        f"💳 شماره کارت:\n<code>{CARD_NUMBER}</code>\n"
        f"👤 به نام: {CARD_HOLDER}\n\n"
        f"⏱ <b>مهلت پرداخت: {to_persian_digits(INVOICE_EXPIRY_MINUTES)} دقیقه</b>\n\n"
        f"پس از واریز، دکمه «✅ پرداخت کردم» را بزنید."
    )

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=card_payment_keyboard(invoice_id, CARD_NUMBER, price),
        parse_mode="HTML",
    )
    await callback.answer()

    # Schedule expiration
    asyncio.create_task(_expire_invoice_after(invoice_id, callback, INVOICE_EXPIRY_MINUTES * 60))


async def _expire_invoice_after(
    invoice_id: str,
    callback: types.CallbackQuery,
    seconds: int,
) -> None:
    """Background task to expire an invoice after the timeout."""
    await asyncio.sleep(seconds)
    invoice = await get_invoice(invoice_id)
    if invoice and invoice["status"] == "pending":
        await update_invoice_status(invoice_id, "expired")
        try:
            if callback.message:
                await callback.message.edit_text(  # type: ignore[union-attr]
                    f"⏰ <b>فاکتور {invoice_id} منقضی شد.</b>\n\n"
                    "مهلت پرداخت به پایان رسیده است. لطفاً دوباره اقدام کنید.",
                    parse_mode="HTML",
                )
        except Exception:
            pass


@router.callback_query(F.data.regexp(r"^copy_card_"))
async def copy_card_number(callback: types.CallbackQuery) -> None:
    """Show card number as a copyable alert."""
    await callback.answer(f"شماره کارت: {CARD_NUMBER}", show_alert=True)


@router.callback_query(F.data.regexp(r"^copy_amount_"))
async def copy_amount(callback: types.CallbackQuery) -> None:
    """Show amount as a copyable alert."""
    invoice_id = callback.data.split("_")[-1]  # type: ignore[union-attr]
    invoice = await get_invoice(invoice_id)
    if invoice:
        await callback.answer(f"مبلغ: {invoice['amount']} تومان", show_alert=True)
    else:
        await callback.answer("فاکتور یافت نشد.", show_alert=True)


# ──────────────────────────── "I Have Paid" → Receipt Upload ────────────────────────────


@router.callback_query(F.data.regexp(r"^paid_"))
async def paid_button(callback: types.CallbackQuery, state: FSMContext) -> None:
    """User claims they paid — ask for receipt."""
    invoice_id = callback.data.split("_")[-1]  # type: ignore[union-attr]
    invoice = await get_invoice(invoice_id)

    if not invoice:
        await callback.answer("❌ فاکتور یافت نشد.", show_alert=True)
        return

    if invoice["status"] != "pending":
        await callback.answer("❌ این فاکتور دیگر فعال نیست.", show_alert=True)
        return

    await state.set_state(BuyStates.waiting_receipt)
    await state.update_data(invoice_id=invoice_id)

    await callback.message.edit_text(  # type: ignore[union-attr]
        "📸 <b>لطفاً رسید پرداخت خود را ارسال کنید.</b>\n\n"
        "می‌توانید عکس رسید یا متن شماره پیگیری را بفرستید.\n\n"
        "برای انصراف /cancel را بزنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BuyStates.waiting_receipt, F.photo)
async def receive_receipt_photo(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    """Receive receipt as photo."""
    if not message.from_user or not message.photo:
        return

    data = await state.get_data()
    invoice_id = data.get("invoice_id")
    if not invoice_id:
        await state.clear()
        return

    invoice = await get_invoice(invoice_id)
    if not invoice or invoice["status"] != "pending":
        await message.answer("❌ فاکتور منقضی شده یا قبلاً پردازش شده است.")
        await state.clear()
        return

    file_id = message.photo[-1].file_id
    await set_invoice_receipt(invoice_id, file_id=file_id)
    await update_invoice_status(invoice_id, "paid")
    await state.clear()

    await message.answer(
        "✅ <b>رسید شما دریافت شد.</b>\n\n"
        "پرداخت شما در حال بررسی توسط ادمین است. "
        "پس از تأیید، اشتراک شما فعال خواهد شد.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )

    # Send to admin
    admin_text = (
        f"🔔 <b>درخواست تأیید پرداخت</b>\n\n"
        f"🆔 فاکتور: <code>{invoice_id}</code>\n"
        f"👤 کاربر: <code>{message.from_user.id}</code>"
    )
    if message.from_user.username:
        admin_text += f" (@{message.from_user.username})"
    admin_text += (
        f"\n\n📦 سفارش: {format_size_gb(invoice['data_gb'])} / "
        f"{to_persian_digits(invoice['duration_days'])} روز\n"
        f"💰 مبلغ: {format_price(invoice['amount'])}\n"
    )

    admin_msg = await bot.send_photo(
        chat_id=ADMIN_CHAT_ID,
        photo=file_id,
        caption=admin_text,
        reply_markup=admin_payment_review_keyboard(invoice_id),
        parse_mode="HTML",
    )
    await set_invoice_message_id(invoice_id, admin_msg.message_id)


@router.message(BuyStates.waiting_receipt, F.text)
async def receive_receipt_text(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    """Receive receipt as text (tracking number)."""
    if not message.from_user or not message.text:
        return

    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(
            "❌ عملیات لغو شد.",
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
        await message.answer("❌ فاکتور منقضی شده یا قبلاً پردازش شده است.")
        await state.clear()
        return

    receipt_text = message.text.strip()
    await set_invoice_receipt(invoice_id, text=receipt_text)
    await update_invoice_status(invoice_id, "paid")
    await state.clear()

    await message.answer(
        "✅ <b>رسید شما دریافت شد.</b>\n\n"
        "پرداخت شما در حال بررسی توسط ادمین است. "
        "پس از تأیید، اشتراک شما فعال خواهد شد.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )

    # Send to admin
    admin_text = (
        f"🔔 <b>درخواست تأیید پرداخت</b>\n\n"
        f"🆔 فاکتور: <code>{invoice_id}</code>\n"
        f"👤 کاربر: <code>{message.from_user.id}</code>"
    )
    if message.from_user.username:
        admin_text += f" (@{message.from_user.username})"
    admin_text += (
        f"\n\n📦 سفارش: {format_size_gb(invoice['data_gb'])} / "
        f"{to_persian_digits(invoice['duration_days'])} روز\n"
        f"💰 مبلغ: {format_price(invoice['amount'])}\n\n"
        f"📝 متن رسید:\n<code>{receipt_text}</code>"
    )

    admin_msg = await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=admin_text,
        reply_markup=admin_payment_review_keyboard(invoice_id),
        parse_mode="HTML",
    )
    await set_invoice_message_id(invoice_id, admin_msg.message_id)


# ──────────────────────────── Cancel ────────────────────────────


@router.callback_query(F.data == "buy_cancel")
async def buy_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Cancel the buy flow."""
    await state.clear()
    await callback.message.edit_text(  # type: ignore[union-attr]
        "❌ عملیات خرید لغو شد.",
        parse_mode="HTML",
    )
    await callback.answer()
