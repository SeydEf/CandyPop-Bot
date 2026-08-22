from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import CARD_HOLDER, CARD_NUMBER, INVOICE_EXPIRY_MINUTES
from db.models import create_invoice
from keyboards.inline_kb import card_payment_keyboard, deposit_amount_keyboard
from keyboards.reply_kb import BTN_INCREASE_WALLET, main_menu_keyboard
from utils.formatting import format_price, persian_to_english_digits

logger = logging.getLogger(__name__)
router = Router(name="wallet")


class WalletStates(StatesGroup):
    waiting_deposit_amount = State()


@router.message(F.text == BTN_INCREASE_WALLET)
async def wallet_increase_start(message: types.Message, state: FSMContext) -> None:
    await state.set_state(WalletStates.waiting_deposit_amount)
    text = (
        "💳 <b>افزایش موجودی کیف پول</b>\n\n"
        "با داشتن موجودی در کیف پول، می‌توانید تمام سفارش‌ها و تمدیدهای خود را <b>به‌صورت آنی و خودکار</b> تحویل بگیرید.\n\n"
        "🔹 لطفاً یکی از مبالغ آماده زیر را انتخاب کنید یا مبلغ دلخواه خود (به تومان) را ارسال نمایید:\n"
        "💡 <i>مثال: <code>100000</code></i>"
    )
    await message.answer(
        text,
        reply_markup=deposit_amount_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("deposit_amt_"))
async def wallet_deposit_preset(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not callback.from_user:
        return
    amount = int(callback.data.split("_")[-1])
    tg_id = callback.from_user.id

    await state.clear()

    invoice = await create_invoice(
        tg_id=tg_id,
        amount=amount,
        duration_days=0,
        data_gb=0,
        users_count=0,
        target_email="TOPUP",
    )
    invoice_id = invoice["id"]

    text = (
        f"💳 <b>فاکتور افزایش موجودی کیف پول</b>\n\n"
        f"🧾 <b>شماره فاکتور:</b> <code>{invoice_id}</code>\n"
        f"💰 <b>مبلغ قابل واریز:</b> <b>{format_price(amount)}</b>\n\n"
        f"💳 <b>شماره کارت مقصد:</b>\n"
        f"<code>{CARD_NUMBER}</code>\n"
        f"👤 <b>به نام:</b> {CARD_HOLDER}\n\n"
        f"⏳ <b>مهلت پرداخت:</b> {INVOICE_EXPIRY_MINUTES} دقیقه\n\n"
        f"📌 <i>لطفاً پس از واریز مبلغ، روی دکمه «✅ پرداخت کردم» کلیک نموده و تصویر فیش واریزی را ارسال فرمایید تا حسابتان شارژ شود.</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=card_payment_keyboard(invoice_id, CARD_NUMBER, amount),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "deposit_cancel")
async def wallet_deposit_cancel(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.clear()
    await callback.message.edit_text(
        "❌ <b>فرآیند افزایش موجودی کیف پول لغو شد.</b>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(WalletStates.waiting_deposit_amount, F.text)
async def wallet_deposit_custom_input(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or not message.from_user:
        return

    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(
            "❌ <b>فرآیند افزایش موجودی لغو گردید.</b>",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    raw_text = persian_to_english_digits(message.text.strip())
    clean_text = raw_text.replace(",", "").replace("،", "").replace(" ", "")

    try:
        amount = int(clean_text)
        if amount < 50000:
            await message.answer(
                "❌ <b>حداقل مبلغ برای افزایش موجودی ۵۰,۰۰۰ تومان می‌باشد.</b>",
                parse_mode="HTML",
            )
            return
        if amount > 50000000:
            await message.answer(
                "❌ <b>حداکثر مبلغ برای هر بار واریز ۵۰,۰۰۰,۰۰۰ تومان می‌باشد.</b>",
                parse_mode="HTML",
            )
            return
    except ValueError:
        await message.answer(
            "❌ <b>مبلغ وارد شده معتبر نیست!</b>\n\n"
            "لطفاً مبلغ مورد نظر را فقط به صورت عدد (به تومان) ارسال نمایید.\n"
            "💡 <i>مثال: <code>150000</code></i>",
            parse_mode="HTML",
        )
        return

    tg_id = message.from_user.id
    await state.clear()

    invoice = await create_invoice(
        tg_id=tg_id,
        amount=amount,
        duration_days=0,
        data_gb=0,
        users_count=0,
        target_email="TOPUP",
    )
    invoice_id = invoice["id"]

    text = (
        f"💳 <b>فاکتور افزایش موجودی کیف پول</b>\n\n"
        f"🧾 <b>شماره فاکتور:</b> <code>{invoice_id}</code>\n"
        f"💰 <b>مبلغ قابل واریز:</b> <b>{format_price(amount)}</b>\n\n"
        f"💳 <b>شماره کارت مقصد:</b>\n"
        f"<code>{CARD_NUMBER}</code>\n"
        f"👤 <b>به نام:</b> {CARD_HOLDER}\n\n"
        f"⏳ <b>مهلت پرداخت:</b> {INVOICE_EXPIRY_MINUTES} دقیقه\n\n"
        f"📌 <i>لطفاً پس از واریز مبلغ، روی دکمه «✅ پرداخت کردم» کلیک نموده و تصویر فیش واریزی را ارسال فرمایید تا حسابتان شارژ شود.</i>"
    )

    await message.answer(
        text,
        reply_markup=card_payment_keyboard(invoice_id, CARD_NUMBER, amount),
        parse_mode="HTML",
    )
