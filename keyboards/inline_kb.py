from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import DURATION_OPTIONS, VOLUME_TIERS
from utils.formatting import format_price, to_persian_digits


async def volume_keyboard() -> InlineKeyboardMarkup:
    from services.pricing import calculate_data_price

    rows: list[list[InlineKeyboardButton]] = []

    for gb, _ in VOLUME_TIERS.items():
        data_price = await calculate_data_price(gb)
        label = f"📊 {gb} گیگ ({format_price(data_price)})"
        btn = InlineKeyboardButton(
            text=label,
            callback_data=f"buy_vol_{gb}",
        )
        rows.append([btn])

    rows.append(
        [
            InlineKeyboardButton(
                text="📝 حجم دلخواه",
                callback_data="buy_vol_custom",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="❌ انصراف", callback_data="buy_cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def users_keyboard(gb: int, users: int) -> InlineKeyboardMarkup:
    dec_users = max(1, users - 1)
    inc_users = min(10, users + 1)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➖", callback_data=f"buy_users_step_{gb}_{dec_users}"
                ),
                InlineKeyboardButton(
                    text=f"👤 {to_persian_digits(users)} کاربر",
                    callback_data="buy_noop",
                ),
                InlineKeyboardButton(
                    text="➕", callback_data=f"buy_users_step_{gb}_{inc_users}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ ادامه",
                    callback_data=f"buy_users_confirm_{gb}_{users}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به انتخاب حجم", callback_data="buy_back_volume"
                ),
                InlineKeyboardButton(text="❌ انصراف", callback_data="buy_cancel"),
            ],
        ]
    )


def duration_keyboard(gb: int, users: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for days in DURATION_OPTIONS:
        months = days // 30
        label = f"⏱ {days} روز ({to_persian_digits(months)} ماهه)"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label, callback_data=f"buy_dur_{gb}_{users}_{days}"
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به انتخاب کاربر",
                callback_data=f"buy_back_users_{gb}_{users}",
            ),
            InlineKeyboardButton(text="❌ انصراف", callback_data="buy_cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_method_keyboard(
    duration: int, users: int, gb: int, price: int, has_discount: bool = False
) -> InlineKeyboardMarkup:
    discount_btn = (
        InlineKeyboardButton(
            text="❌ حذف کد تخفیف",
            callback_data=f"buy_discount_remove_{duration}_{users}_{gb}_{price}",
        )
        if has_discount
        else InlineKeyboardButton(
            text="🏷️ اعمال کد تخفیف",
            callback_data=f"buy_discount_apply_{duration}_{users}_{gb}_{price}",
        )
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [discount_btn],
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
                    callback_data=f"buy_back_duration_{gb}_{users}",
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
            # [
            #     InlineKeyboardButton(
            #         text="📋 کپی شماره کارت",
            #         callback_data=f"copy_card_{invoice_id}",
            #     ),
            # ],
            # [
            #     InlineKeyboardButton(
            #         text="📋 کپی مبلغ",
            #         callback_data=f"copy_amount_{invoice_id}",
            #     ),
            # ],
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
        is_enable = sub.get("enable", True)
        status_icon = "🟢" if is_enable else "🔴"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status_icon} {name}",
                    callback_data=f"sub_view_{email}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_manage_keyboard(
    email: str, show_renew: bool = True, is_test_sub: bool = False
) -> InlineKeyboardMarkup:
    rows = []
    if show_renew and not is_test_sub:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 تمدید اشتراک",
                    callback_data=f"sub_renew_{email}",
                ),
            ]
        )

    second_row = []
    if not is_test_sub:
        second_row.append(
            InlineKeyboardButton(
                text="✏️ تغییر نام",
                callback_data=f"sub_rename_{email}",
            )
        )
    second_row.append(
        InlineKeyboardButton(
            text="🔄 تغییر لینک",
            callback_data=f"sub_regen_{email}",
        )
    )
    rows.append(second_row)

    rows.append(
        [
            InlineKeyboardButton(
                text="📱 QR Code",
                callback_data=f"sub_qr_{email}",
            ),
            InlineKeyboardButton(
                text="🔗 لینک‌های کانفیگ",
                callback_data=f"sub_links_{email}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🗑 حذف سرویس",
                callback_data=f"sub_delete_{email}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به لیست",
                callback_data="sub_back_list",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
                    text="🔙 بازگشت", callback_data="renew_back_to_users"
                ),
                InlineKeyboardButton(
                    text="❌ انصراف", callback_data="sub_view_current"
                ),
            ],
        ]
    )


def renew_users_keyboard(gb: int, users: int) -> InlineKeyboardMarkup:
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


async def renew_volume_keyboard(
    duration: int = 30, users: int = 1
) -> InlineKeyboardMarkup:
    from services.pricing import calculate_data_price, get_pricing_config

    config = await get_pricing_config()
    volume_tiers = config["volume_tiers"]

    rows: list[list[InlineKeyboardButton]] = []

    for item in volume_tiers:
        gb = item[0] if isinstance(item, (list, tuple)) else item
        data_price = await calculate_data_price(gb)
        label = f"📊 {gb} گیگ ({format_price(data_price)})"
        btn = InlineKeyboardButton(
            text=label,
            callback_data=f"renew_vol_{gb}",
        )
        rows.append([btn])

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
                callback_data="sub_renew_current",
            ),
            InlineKeyboardButton(text="❌ انصراف", callback_data="sub_view_current"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def renew_payment_method_keyboard(
    is_change_plan: bool = False, has_discount: bool = False
) -> InlineKeyboardMarkup:
    discount_btn = (
        InlineKeyboardButton(
            text="❌ حذف کد تخفیف",
            callback_data="renew_discount_remove",
        )
        if has_discount
        else InlineKeyboardButton(
            text="🏷️ اعمال کد تخفیف",
            callback_data="renew_discount_apply",
        )
    )

    back_callback = "renew_back_to_duration" if is_change_plan else "sub_renew_current"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [discount_btn],
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
                    callback_data=back_callback,
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
                    text="50,000 تومان", callback_data="deposit_select_50000"
                ),
                InlineKeyboardButton(
                    text="100,000 تومان", callback_data="deposit_select_100000"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="200,000 تومان", callback_data="deposit_select_200000"
                ),
                InlineKeyboardButton(
                    text="500,000 تومان", callback_data="deposit_select_500000"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ مبلغ دلخواه", callback_data="deposit_select_custom"
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


def bulk_gift_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 هدیه حجم عمومی (+GB)",
                    callback_data="admin_bulk_gift_gb_start",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏱ هدیه تمدید زمان عمومی (+روز)",
                    callback_data="admin_bulk_gift_days_start",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
                )
            ],
        ]
    )


def bulk_gift_gb_stepper_keyboard(gb: int) -> InlineKeyboardMarkup:
    dec_gb = max(1, gb - 1)
    inc_gb = gb + 1
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕", callback_data=f"admin_bulk_gift_gb_step_{inc_gb}"
                ),
                InlineKeyboardButton(
                    text=f"📊 هدیه: +{to_persian_digits(gb)} گیگ",
                    callback_data="buy_noop",
                ),
                InlineKeyboardButton(
                    text="➖", callback_data=f"admin_bulk_gift_gb_step_{dec_gb}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚀 اعمال هدیه به همه کاربران",
                    callback_data=f"admin_bulk_gift_gb_confirm_{gb}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به منوی هدیه",
                    callback_data="admin_bulk_gift_menu",
                )
            ],
        ]
    )


def bulk_gift_days_stepper_keyboard(days: int) -> InlineKeyboardMarkup:
    dec_days = max(1, days - 1)
    inc_days = days + 1
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕", callback_data=f"admin_bulk_gift_days_step_{inc_days}"
                ),
                InlineKeyboardButton(
                    text=f"⏱ تمدید: +{to_persian_digits(days)} روز",
                    callback_data="buy_noop",
                ),
                InlineKeyboardButton(
                    text="➖", callback_data=f"admin_bulk_gift_days_step_{dec_days}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚀 اعمال هدیه به همه کاربران",
                    callback_data=f"admin_bulk_gift_days_confirm_{days}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به منوی هدیه",
                    callback_data="admin_bulk_gift_menu",
                )
            ],
        ]
    )


def admin_manage_admins_keyboard(admins: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for adm in admins:
        tg_id = adm["tg_id"]
        username = adm.get("username", "")
        name_str = f"@{username}" if username else f"ID: {tg_id}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"⚙️ دسترسی‌های {name_str}",
                    callback_data=f"admin_perm_panel_{tg_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ افزودن ادمین جدید",
                callback_data="admin_add_admin_start",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🗑 عزل / حذف ادمین",
                callback_data="admin_remove_admin_menu",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به پنل اصلی", callback_data="admin_price_main"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_permissions_keyboard(
    tg_id: int, perms: dict[str, bool]
) -> InlineKeyboardMarkup:
    from db.models import PERMISSION_TITLES

    rows: list[list[InlineKeyboardButton]] = []

    for perm_key, title in PERMISSION_TITLES.items():
        is_active = perms.get(perm_key, False)
        status_icon = "🟢" if is_active else "🔴"
        btn_text = f"{status_icon} {title}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"admin_toggle_perm_{tg_id}_{perm_key}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🗑 عزل و حذف دسترسی این ادمین",
                callback_data=f"admin_remove_admin_confirm_{tg_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به لیست ادمین‌ها",
                callback_data="admin_manage_admins_menu",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_remove_admins_keyboard(admins: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for adm in admins:
        tg_id = adm["tg_id"]
        username = adm.get("username", "")
        name_str = f"@{username}" if username else f"ID: {tg_id}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 عزل {name_str}",
                    callback_data=f"admin_remove_admin_confirm_{tg_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به مدیریت ادمین‌ها",
                callback_data="admin_manage_admins_menu",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 بروزرسانی آمار",
                    callback_data="admin_stats_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل اصلی",
                    callback_data="admin_price_main",
                )
            ],
        ]
    )


def admin_users_list_keyboard(
    users: list[dict[str, Any]],
    current_page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for u in users:
        tg_id = u["tg_id"]
        username = u.get("username")
        full_name = u.get("full_name") or "بدون نام"
        display_name = f"@{username}" if username else full_name
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👤 مدیریت کاربر {display_name} ({tg_id})",
                    callback_data=f"admin_manage_user_{tg_id}_p{current_page}",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if current_page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ قبلی",
                callback_data=f"admin_users_list_{current_page - 1}",
            )
        )
    else:
        nav_row.append(
            InlineKeyboardButton(
                text="⛔️",
                callback_data="admin_users_list_noop",
            )
        )

    nav_row.append(
        InlineKeyboardButton(
            text=f"📄 {current_page + 1} / {total_pages}",
            callback_data="admin_users_list_noop",
        )
    )

    if current_page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="بعدی ➡️",
                callback_data=f"admin_users_list_{current_page + 1}",
            )
        )
    else:
        nav_row.append(
            InlineKeyboardButton(
                text="⛔️",
                callback_data="admin_users_list_noop",
            )
        )
    rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton(
                text="🔍 جستجوی کاربر",
                callback_data="admin_search_start",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به پنل اصلی",
                callback_data="admin_price_main",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)
