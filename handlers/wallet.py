from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import INVOICE_EXPIRY_MINUTES
from db.models import create_invoice, get_card_config
from keyboards.inline_kb import card_payment_keyboard, deposit_amount_keyboard
from keyboards.reply_kb import BTN_INCREASE_WALLET, main_menu_keyboard
from utils.formatting import format_price, persian_to_english_digits

logger = logging.getLogger(__name__)
router = Router(name="wallet")


class WalletStates(StatesGroup):
    waiting_deposit_amount = State()


@router.message(Command("topup", "deposit", "wallet"))
@router.message(F.text == BTN_INCREASE_WALLET)
async def wallet_deposit_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()

    card_cfg = await get_card_config()
    if not card_cfg.get("enabled", True):
        await message.answer(
            "⚠️ <b>روش پرداخت کارت به کارت و شارژ کیف پول در حال حاضر غیرفعال می‌باشد.</b>\n\n"
            "لطفاً در زمانی دیگر مجدداً تلاش فرمایید.",
            parse_mode="HTML",
        )
        return

    text = (
        "💳 <b>افزایش موجودی کیف پول</b>\n\n"
        "لطفاً مبلغ مورد نظر برای افزایش موجودی را از گزینه‌های زیر انتخاب نموده یا مبلغ دلخواه خود را به تومان ارسال نمایید:\n\n"
        "💡 <i>حداقل مبلغ برای شارژ حساب ۵۰,۰۰۰ تومان می‌باشد.</i>"
    )
    await message.answer(
        text, reply_markup=deposit_amount_keyboard(), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("deposit_select_"))
@router.callback_query(F.data.startswith("deposit_amt_"))
async def wallet_deposit_preset(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not callback.from_user:
        return

    card_cfg = await get_card_config()
    if not card_cfg.get("enabled", True):
        await callback.answer(
            "⚠️ شارژ کیف پول از طریق کارت به کارت در حال حاضر غیرفعال است.",
            show_alert=True,
        )
        return

    amount_str = callback.data.split("_")[-1]
    if amount_str == "custom":
        await state.set_state(WalletStates.waiting_deposit_amount)
        cancel_kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="❌ انصراف و بازگشت", callback_data="deposit_back"
                    )
                ]
            ]
        )
        await callback.message.edit_text(
            "💰 <b>لطفاً مبلغ مورد نظر خود را (به تومان) وارد کنید:</b>\n\n"
            "مثال: <code>150000</code>\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=cancel_kb,
            parse_mode="HTML",
        )
        await callback.answer()
        return

    try:
        amount = int(amount_str)
    except ValueError:
        await callback.answer("مبلغ نامعتبر است.", show_alert=True)
        return

    tg_id = callback.from_user.id
    invoice = await create_invoice(
        tg_id=tg_id,
        amount=amount,
        duration_days=0,
        data_gb=0,
        users_count=0,
        target_email="TOPUP",
    )
    invoice_id = invoice["id"]

    card_config = await get_card_config()
    card_number = card_config["card_number"]
    card_holder = card_config["card_holder"]

    text = (
        f"💳 <b>فاکتور افزایش موجودی کیف پول</b>\n\n"
        f"🧾 <b>شماره فاکتور:</b> <code>{invoice_id}</code>\n"
        f"💰 <b>مبلغ قابل واریز:</b> <b>{format_price(amount)}</b>\n\n"
        f"💳 <b>شماره کارت مقصد:</b>\n"
        f"<code>{card_number}</code>\n"
        f"👤 <b>به نام:</b> {card_holder}\n\n"
        f"⏳ <b>مهلت پرداخت:</b> {INVOICE_EXPIRY_MINUTES} دقیقه\n\n"
        f"📌 <i>لطفاً پس از واریز مبلغ، روی دکمه «✅ پرداخت کردم» کلیک نموده و تصویر فیش واریزی را ارسال فرمایید تا حسابتان شارژ شود.</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=card_payment_keyboard(invoice_id, card_number, amount),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "deposit_back")
async def wallet_deposit_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    card_cfg = await get_card_config()
    if not card_cfg.get("enabled", True):
        await callback.answer(
            "⚠️ شارژ کیف پول از طریق کارت به کارت در حال حاضر غیرفعال است.",
            show_alert=True,
        )
        return
    text = (
        "💳 <b>افزایش موجودی کیف پول</b>\n\n"
        "لطفاً مبلغ مورد نظر برای افزایش موجودی را از گزینه‌های زیر انتخاب نموده یا مبلغ دلخواه خود را به تومان ارسال نمایید:\n\n"
        "💡 <i>حداقل مبلغ برای شارژ حساب ۵۰,۰۰۰ تومان می‌باشد.</i>"
    )
    await callback.message.edit_text(
        text, reply_markup=deposit_amount_keyboard(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "deposit_cancel")
async def wallet_deposit_cancel(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.clear()
    from db.models import get_user
    from handlers.profile import _build_profile_text
    from keyboards.inline_kb import profile_dashboard_keyboard

    if callback.from_user:
        user_info = await get_user(callback.from_user.id)
        text = await _build_profile_text(callback.from_user.id, user_info)
        await callback.message.edit_text(
            text,
            reply_markup=profile_dashboard_keyboard(),
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            "❌ <b>فرآیند افزایش موجودی کیف پول لغو شد.</b>",
            parse_mode="HTML",
        )
    await callback.answer("عملیات افزایش موجودی لغو شد.")


@router.message(WalletStates.waiting_deposit_amount, F.text)
async def wallet_deposit_custom_input(
    message: types.Message, state: FSMContext
) -> None:
    if not message.text or not message.from_user:
        return

    card_cfg = await get_card_config()
    if not card_cfg.get("enabled", True):
        await state.clear()
        await message.answer(
            "⚠️ <b>روش پرداخت کارت به کارت و شارژ کیف پول در حال حاضر غیرفعال می‌باشد.</b>",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
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

    cancel_kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="❌ انصراف و بازگشت", callback_data="deposit_back"
                )
            ]
        ]
    )

    try:
        amount = int(clean_text)
        if amount < 50000:
            await message.answer(
                "❌ <b>حداقل مبلغ برای افزایش موجودی ۵۰,۰۰۰ تومان می‌باشد.</b>\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )
            return
        if amount > 50000000:
            await message.answer(
                "❌ <b>حداکثر مبلغ برای هر بار واریز ۵۰,۰۰۰,۰۰۰ تومان می‌باشد.</b>\n\n"
                "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
                reply_markup=cancel_kb,
                parse_mode="HTML",
            )
            return
    except ValueError:
        await message.answer(
            "❌ <b>مبلغ وارد شده معتبر نیست!</b>\n\n"
            "لطفاً مبلغ مورد نظر را فقط به صورت عدد (به تومان) ارسال نمایید.\n"
            "💡 <i>مثال: <code>150000</code></i>\n\n"
            "💡 <i>برای انصراف از دکمه زیر استفاده کنید.</i>",
            reply_markup=cancel_kb,
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
    card_config = await get_card_config()
    card_number = card_config["card_number"]
    card_holder = card_config["card_holder"]

    text = (
        f"💳 <b>فاکتور افزایش موجودی کیف پول</b>\n\n"
        f"🧾 <b>شماره فاکتور:</b> <code>{invoice_id}</code>\n"
        f"💰 <b>مبلغ قابل واریز:</b> <b>{format_price(amount)}</b>\n\n"
        f"💳 <b>شماره کارت مقصد:</b>\n"
        f"<code>{card_number}</code>\n"
        f"👤 <b>به نام:</b> {card_holder}\n\n"
        f"⏳ <b>مهلت پرداخت:</b> {INVOICE_EXPIRY_MINUTES} دقیقه\n\n"
        f"📌 <i>لطفاً پس از واریز مبلغ، روی دکمه «✅ پرداخت کردم» کلیک نموده و تصویر فیش واریزی را ارسال فرمایید تا حسابتان شارژ شود.</i>"
    )

    await message.answer(
        text,
        reply_markup=card_payment_keyboard(invoice_id, card_number, amount),
        parse_mode="HTML",
    )
