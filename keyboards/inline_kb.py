from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import DURATION_OPTIONS, VOLUME_TIERS
from utils.formatting import format_price, to_persian_digits


def duration_keyboard() -> InlineKeyboardMarkup:
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


def users_keyboard(duration: int, users: int) -> InlineKeyboardMarkup:
    dec_users = max(1, users - 1)
    inc_users = min(10, users + 1)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➖", callback_data=f"buy_users_step_{duration}_{dec_users}"
                ),
                InlineKeyboardButton(
                    text=f"👤 {to_persian_digits(users)} کاربر",
                    callback_data="buy_noop",
                ),
                InlineKeyboardButton(
                    text="➕", callback_data=f"buy_users_step_{duration}_{inc_users}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ ادامه",
                    callback_data=f"buy_users_confirm_{duration}_{users}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت", callback_data="buy_back_duration"
                ),
                InlineKeyboardButton(text="❌ انصراف", callback_data="buy_cancel"),
            ],
        ]
    )


async def volume_keyboard(duration: int, users: int) -> InlineKeyboardMarkup:
    from services.pricing import calculate_total_price

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

    for i, (gb, _) in enumerate(VOLUME_TIERS.items()):
        total_price = await calculate_total_price(gb, duration, users)
        label = f"{gb}GB — {format_price(total_price)}"
        btn = InlineKeyboardButton(
            text=label,
            callback_data=f"buy_vol_{duration}_{users}_{gb}",
        )
        row.append(btn)
        if len(row) == 2 or i == len(VOLUME_TIERS) - 1:
            rows.append(row)
            row = []

    rows.append(
        [
            InlineKeyboardButton(
                text="📝 حجم دلخواه",
                callback_data=f"buy_vol_{duration}_{users}_custom",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت", callback_data=f"buy_back_users_{duration}_{users}"
            ),
            InlineKeyboardButton(text="❌ انصراف", callback_data="buy_cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_method_keyboard(
    duration: int, users: int, gb: int, price: int
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 کیف پول",
                    callback_data=f"buy_pay_wallet_{duration}_{users}_{gb}_{price}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💳 کارت به کارت",
                    callback_data=f"buy_pay_card_{duration}_{users}_{gb}_{price}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت",
                    callback_data=f"buy_back_volume_{duration}_{users}",
                ),
                InlineKeyboardButton(text="❌ انصراف", callback_data="buy_cancel"),
            ],
        ]
    )


def wallet_confirm_keyboard(
    duration: int, users: int, gb: int, price: int
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید و پرداخت",
                    callback_data=f"buy_wallet_confirm_{duration}_{users}_{gb}_{price}",
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


def admin_payment_review_keyboard(invoice_id: str) -> InlineKeyboardMarkup:
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


def subscriptions_list_keyboard(
    subs: list[dict],
) -> InlineKeyboardMarkup:
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 تمدید اشتراک",
                    callback_data=f"sub_renew_{email}",
                ),
            ],
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


def renew_options_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 تمدید پلن فعلی",
                    callback_data="renew_same",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ تغییر پلن و تمدید",
                    callback_data="renew_change",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت",
                    callback_data="sub_view_current",
                ),
            ],
        ]
    )


def renew_duration_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for days in DURATION_OPTIONS:
        label = f"{days} روز"
        buttons.append(
            InlineKeyboardButton(text=label, callback_data=f"renew_dur_{days}")
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons,
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت", callback_data="sub_renew_current"
                ),
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data="sub_view_current"
                ),
            ],
        ]
    )


def renew_users_keyboard(duration: int, users: int) -> InlineKeyboardMarkup:
    dec_users = max(1, users - 1)
    inc_users = min(10, users + 1)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➖",
                    callback_data=f"renew_users_step_{dec_users}",
                ),
                InlineKeyboardButton(
                    text=f"👤 {to_persian_digits(users)} کاربر",
                    callback_data="buy_noop",
                ),
                InlineKeyboardButton(
                    text="➕",
                    callback_data=f"renew_users_step_{inc_users}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ ادامه",
                    callback_data=f"renew_users_confirm_{users}",
                ),
            ],
            [
                InlineKeyboardButton(text="🔙 بازگشت", callback_data="renew_change"),
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data="sub_view_current"
                ),
            ],
        ]
    )


async def renew_volume_keyboard(duration: int, users: int) -> InlineKeyboardMarkup:
    from services.pricing import calculate_total_price

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

    for i, (gb, _) in enumerate(VOLUME_TIERS.items()):
        total_price = await calculate_total_price(gb, duration, users)
        label = f"{gb}GB — {format_price(total_price)}"
        btn = InlineKeyboardButton(
            text=label,
            callback_data=f"renew_vol_{gb}",
        )
        row.append(btn)
        if len(row) == 2 or i == len(VOLUME_TIERS) - 1:
            rows.append(row)
            row = []

    rows.append(
        [
            InlineKeyboardButton(
                text="📝 حجم دلخواه",
                callback_data="renew_vol_custom",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت",
                callback_data=f"renew_back_users_{users}",
            ),
            InlineKeyboardButton(text="❌ انصراف", callback_data="sub_view_current"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def renew_payment_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 کیف پول",
                    callback_data="renew_pay_wallet",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💳 کارت به کارت",
                    callback_data="renew_pay_card",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت",
                    callback_data="renew_back_volume",
                ),
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data="sub_view_current"
                ),
            ],
        ]
    )


def renew_wallet_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید و پرداخت",
                    callback_data="renew_wallet_confirm",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data="sub_view_current"
                ),
            ],
        ]
    )


def confirm_delete_keyboard(email: str) -> InlineKeyboardMarkup:
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


def deposit_amount_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="50,000 تومان", callback_data="deposit_amt_50000"
                ),
                InlineKeyboardButton(
                    text="100,000 تومان", callback_data="deposit_amt_100000"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="200,000 تومان", callback_data="deposit_amt_200000"
                ),
                InlineKeyboardButton(
                    text="500,000 تومان", callback_data="deposit_amt_500000"
                ),
            ],
            [
                InlineKeyboardButton(text="❌ انصراف", callback_data="deposit_cancel"),
            ],
        ]
    )


def profile_dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 افزایش موجودی", callback_data="profile_topup"
                ),
                InlineKeyboardButton(
                    text="🧾 تاریخچه سفارشات", callback_data="profile_orders_0"
                ),
            ],
        ]
    )


def orders_pagination_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if total_pages > 1:
        prev_btn = (
            InlineKeyboardButton(
                text="◀️ قبلی", callback_data=f"profile_orders_{page - 1}"
            )
            if page > 0
            else InlineKeyboardButton(text=" ⬛️ ", callback_data="buy_noop")
        )

        page_indicator = InlineKeyboardButton(
            text=f"صفحه {to_persian_digits(page + 1)} از {to_persian_digits(total_pages)}",
            callback_data="buy_noop",
        )

        next_btn = (
            InlineKeyboardButton(
                text="بعدی ▶️", callback_data=f"profile_orders_{page + 1}"
            )
            if page < total_pages - 1
            else InlineKeyboardButton(text=" ⬛️ ", callback_data="buy_noop")
        )

        rows.append([prev_btn, page_indicator, next_btn])

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به پروفایل", callback_data="profile_main"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
