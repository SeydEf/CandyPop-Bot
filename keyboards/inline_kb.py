"""
All inline keyboards used throughout the bot.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import VOLUME_TIERS, DURATION_OPTIONS
from utils.formatting import format_price


# ──────────────────────────── Buy Flow ────────────────────────────


def duration_keyboard() -> InlineKeyboardMarkup:
    """Step 1: Duration selection (30/60/90 days)."""
    buttons = []
    for days in DURATION_OPTIONS:
        label = f"{days} روز"
        buttons.append(
            InlineKeyboardButton(text=label, callback_data=f"buy_dur_{days}")
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons,
            [InlineKeyboardButton(text="❌ انصراف", callback_data="buy_cancel")],
        ]
    )


def volume_keyboard(duration: int) -> InlineKeyboardMarkup:
    """Step 2: Data volume selection with prices."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

    for i, (gb, price) in enumerate(VOLUME_TIERS.items()):
        label = f"{gb} GB — {format_price(price)}"
        btn = InlineKeyboardButton(
            text=label,
            callback_data=f"buy_vol_{duration}_{gb}",
        )
        row.append(btn)
        if len(row) == 2 or i == len(VOLUME_TIERS) - 1:
            rows.append(row)
            row = []

    # Custom volume option
    rows.append(
        [
            InlineKeyboardButton(
                text="📝 حجم دلخواه",
                callback_data=f"buy_vol_{duration}_custom",
            )
        ]
    )
    # Back button
    rows.append(
        [
            InlineKeyboardButton(text="🔙 بازگشت", callback_data="buy_back_duration"),
            InlineKeyboardButton(text="❌ انصراف", callback_data="buy_cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_method_keyboard(duration: int, gb: int, price: int) -> InlineKeyboardMarkup:
    """Step 3: Payment method selection."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 کیف پول",
                    callback_data=f"buy_pay_wallet_{duration}_{gb}_{price}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💳 کارت به کارت",
                    callback_data=f"buy_pay_card_{duration}_{gb}_{price}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت",
                    callback_data=f"buy_back_volume_{duration}",
                ),
                InlineKeyboardButton(text="❌ انصراف", callback_data="buy_cancel"),
            ],
        ]
    )


def wallet_confirm_keyboard(duration: int, gb: int, price: int) -> InlineKeyboardMarkup:
    """Wallet payment confirmation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید و پرداخت",
                    callback_data=f"buy_wallet_confirm_{duration}_{gb}_{price}",
                ),
            ],
            [
                InlineKeyboardButton(text="❌ انصراف", callback_data="buy_cancel"),
            ],
        ]
    )


def card_payment_keyboard(
    invoice_id: str, card_number: str, amount: int
) -> InlineKeyboardMarkup:
    """Card-to-card payment interface with copy buttons."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 کپی شماره کارت",
                    callback_data=f"copy_card_{invoice_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 کپی مبلغ",
                    callback_data=f"copy_amount_{invoice_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ پرداخت کردم",
                    callback_data=f"paid_{invoice_id}",
                ),
            ],
            [
                InlineKeyboardButton(text="❌ انصراف", callback_data="buy_cancel"),
            ],
        ]
    )


# ──────────────────────────── Admin Payment Review ────────────────────────────


def admin_payment_review_keyboard(invoice_id: str) -> InlineKeyboardMarkup:
    """Admin buttons to approve or reject a payment."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید پرداخت",
                    callback_data=f"admin_approve_{invoice_id}",
                ),
                InlineKeyboardButton(
                    text="❌ رد پرداخت",
                    callback_data=f"admin_reject_{invoice_id}",
                ),
            ]
        ]
    )


# ──────────────────────────── My Subscriptions ────────────────────────────


def subscriptions_list_keyboard(
    subs: list[dict],
) -> InlineKeyboardMarkup:
    """List of subscriptions as inline buttons."""
    rows: list[list[InlineKeyboardButton]] = []
    for sub in subs:
        email = sub["email"]
        name = sub["service_name"]
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📦 {name}",
                    callback_data=f"sub_view_{email}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_manage_keyboard(email: str) -> InlineKeyboardMarkup:
    """Management actions for a single subscription."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ تغییر نام",
                    callback_data=f"sub_rename_{email}",
                ),
                InlineKeyboardButton(
                    text="🔄 تغییر لینک",
                    callback_data=f"sub_regen_{email}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📱 QR Code",
                    callback_data=f"sub_qr_{email}",
                ),
                InlineKeyboardButton(
                    text="🔗 لینک‌های کانفیگ",
                    callback_data=f"sub_links_{email}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 حذف سرویس",
                    callback_data=f"sub_delete_{email}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به لیست",
                    callback_data="sub_back_list",
                ),
            ],
        ]
    )


def confirm_delete_keyboard(email: str) -> InlineKeyboardMarkup:
    """Confirm subscription deletion."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ بله، حذف شود",
                    callback_data=f"sub_confirm_del_{email}",
                ),
                InlineKeyboardButton(
                    text="❌ خیر",
                    callback_data=f"sub_view_{email}",
                ),
            ]
        ]
    )


def confirm_regen_keyboard(email: str) -> InlineKeyboardMarkup:
    """Confirm subscription link regeneration."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ بله، لینک جدید بساز",
                    callback_data=f"sub_confirm_regen_{email}",
                ),
                InlineKeyboardButton(
                    text="❌ خیر",
                    callback_data=f"sub_view_{email}",
                ),
            ]
        ]
    )


def sub_config_links_keyboard(email: str) -> InlineKeyboardMarkup:
    """Glass button to fetch config links for a subscription."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 دریافت کانفیگ‌ها",
                    callback_data=f"sub_links_{email}",
                )
            ]
        ]
    )
