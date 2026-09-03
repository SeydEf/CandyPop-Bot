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
                text="🗑 حذف سرویس",
                callback_data=f"sub_delete_{email}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔗 دریافت لینک تکی کانفیگ‌ها",
                callback_data=f"sub_links_{email}",
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
                    text="🔗 دریافت لینک تکی کانفیگ‌ها",
                    callback_data=f"sub_links_{email}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📖 راهنمای اتصال",
                    callback_data="guide_back_os",
                )
            ],
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


def admin_invoices_list_keyboard(
    invoices: list[dict[str, Any]],
    status_filter: str,
    current_page: int,
    total_pages: int,
    search_query: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    all_badge = "🔘 همه" if status_filter == "all" else "همه"
    approved_badge = (
        "🔘 🟢 پرداخت‌شده" if status_filter == "approved" else "🟢 پرداخت‌شده"
    )
    pending_badge = "🔘 🟡 در انتظار" if status_filter == "pending" else "🟡 در انتظار"
    rejected_badge = "🔘 🔴 رد شده" if status_filter == "rejected" else "🔴 رد شده"
    expired_badge = "🔘 ⌛️ منقضی" if status_filter == "expired" else "⌛️ منقضی"

    rows.append(
        [
            InlineKeyboardButton(text=all_badge, callback_data="admin_invoices_all_0"),
            InlineKeyboardButton(
                text=approved_badge, callback_data="admin_invoices_approved_0"
            ),
            InlineKeyboardButton(
                text=pending_badge, callback_data="admin_invoices_pending_0"
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=rejected_badge, callback_data="admin_invoices_rejected_0"
            ),
            InlineKeyboardButton(
                text=expired_badge, callback_data="admin_invoices_expired_0"
            ),
        ]
    )

    digit_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    detail_buttons: list[InlineKeyboardButton] = []
    for idx, inv in enumerate(invoices):
        emoji = digit_emojis[idx] if idx < len(digit_emojis) else f"{idx + 1}"
        detail_buttons.append(
            InlineKeyboardButton(
                text=f"{emoji}",
                callback_data=f"admin_inv_view_{inv['id']}_{status_filter}_{current_page}",
            )
        )
    if detail_buttons:
        rows.append(detail_buttons)

    nav_row: list[InlineKeyboardButton] = []
    if current_page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ قبلی",
                callback_data=f"admin_invoices_{status_filter}_{current_page - 1}",
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
                callback_data=f"admin_invoices_{status_filter}_{current_page + 1}",
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

    if search_query:
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ لغو جستجو و نمایش همه فاکتورها",
                    callback_data="admin_inv_search_clear",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔍 جستجوی مجدد در فاکتورها",
                    callback_data="admin_inv_search_start",
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔍 جستجو در فاکتورها",
                    callback_data="admin_inv_search_start",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 بروزرسانی لیست",
                callback_data=f"admin_invoices_{status_filter}_{current_page}",
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


def admin_invoice_search_prompt_keyboard(
    status_filter: str = "all", page: int = 0
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 انصراف و بازگشت به لیست فاکتورها",
                    callback_data=f"admin_invoices_{status_filter}_{page}",
                )
            ]
        ]
    )


def admin_invoice_detail_keyboard(
    invoice: dict[str, Any],
    status_filter: str,
    page: int,
    can_approve: bool = False,
    can_delete: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    status = invoice.get("status")

    if can_approve and status == "pending":
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ تأیید پرداخت",
                    callback_data=f"admin_approve_{invoice['id']}",
                ),
                InlineKeyboardButton(
                    text="❌ رد پرداخت",
                    callback_data=f"admin_reject_{invoice['id']}",
                ),
            ]
        )

    if invoice.get("receipt_file_id"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="🖼 مشاهده تصویر رسید واریز",
                    callback_data=f"admin_inv_receipt_{invoice['id']}_{status_filter}_{page}",
                )
            ]
        )

    tg_id = invoice.get("tg_id")
    if tg_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="👤 مدیریت این کاربر",
                    callback_data=f"admin_manage_user_{tg_id}_inv_{invoice['id']}_{status_filter}_{page}",
                )
            ]
        )

    if can_delete:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 حذف فاکتور از دیتابیس",
                    callback_data=f"admin_inv_del_ask_{invoice['id']}_{status_filter}_{page}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به لیست فاکتورها",
                callback_data=f"admin_invoices_{status_filter}_{page}",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_invoice_delete_confirm_keyboard(
    invoice_id: str,
    status_filter: str,
    page: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚠️ بله، برای همیشه حذف شود",
                    callback_data=f"admin_inv_del_confirm_{invoice_id}_{status_filter}_{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 انصراف و بازگشت",
                    callback_data=f"admin_inv_view_{invoice_id}_{status_filter}_{page}",
                )
            ],
        ]
    )


def admin_user_ban_scope_keyboard(tg_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    if not is_banned:
        btn1 = InlineKeyboardButton(
            text="🤖 فقط مسدودسازی دسترسی به ربات",
            callback_data=f"admin_user_banscope_{tg_id}_bot",
        )
        btn2 = InlineKeyboardButton(
            text="🚫 مسدودسازی ربات + غیرفعال‌سازی سرویس‌ها",
            callback_data=f"admin_user_banscope_{tg_id}_both",
        )
    else:
        btn1 = InlineKeyboardButton(
            text="🤖 فقط رفع مسدودی دسترسی به ربات",
            callback_data=f"admin_user_banscope_{tg_id}_bot",
        )
        btn2 = InlineKeyboardButton(
            text="✅ رفع مسدودی ربات + فعال‌سازی مجدد سرویس‌ها",
            callback_data=f"admin_user_banscope_{tg_id}_both",
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn1],
            [btn2],
            [
                InlineKeyboardButton(
                    text="🔙 انصراف و بازگشت",
                    callback_data=f"admin_manage_user_{tg_id}_back",
                )
            ],
        ]
    )


def admin_user_ban_notify_keyboard(
    tg_id: int, is_banned: bool, scope: str
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡️ بدون ارسال پیام به کاربر",
                    callback_data=f"admin_user_banaction_{tg_id}_{scope}_none",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 ارسال پیام پیش‌فرض سیستمی",
                    callback_data=f"admin_user_banaction_{tg_id}_{scope}_default",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✍️ ارسال پیام دلخواه ادمین",
                    callback_data=f"admin_user_banaction_{tg_id}_{scope}_custom",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به مرحله قبل",
                    callback_data=f"admin_user_ban_{tg_id}",
                )
            ],
        ]
    )


def admin_user_ban_cancel_keyboard(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 انصراف و بازگشت",
                    callback_data=f"admin_manage_user_{tg_id}_back",
                )
            ]
        ]
    )


def admin_user_subs_list_keyboard(
    clients: list[dict[str, Any]],
    tg_id: int,
    current_page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    import time
    from utils.formatting import format_size_gb

    rows: list[list[InlineKeyboardButton]] = []

    for c in clients:
        email = c.get("email", "نامشخص")
        enable = c.get("enable", True)
        st_icon = "🟢" if enable else "🔴"
        total_gb = (c.get("totalGB") or 0) / (1024**3)

        expiry_ms = c.get("expiryTime", 0) or 0
        now_ms = int(time.time() * 1000)
        if expiry_ms > 0:
            rem_days = max(0, int((expiry_ms - now_ms) / (86400 * 1000)))
            dur_str = f"{to_persian_digits(rem_days)} روز" if rem_days > 0 else "منقضی"
        elif expiry_ms < 0:
            init_days = abs(expiry_ms) // (86400 * 1000)
            dur_str = f"{to_persian_digits(init_days)} روز (پس از اتصال)"
        else:
            dur_str = "نامحدود"

        btn_text = f"{st_icon} {email} | {format_size_gb(total_gb)} | {dur_str}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"admin_user_subsel_{tg_id}_{email}_{current_page}",
                )
            ]
        )

    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if current_page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    text="⬅️ قبلی",
                    callback_data=f"admin_user_subs_{tg_id}_{current_page - 1}",
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
                    callback_data=f"admin_user_subs_{tg_id}_{current_page + 1}",
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
                text="🔙 بازگشت به مدیریت کاربر",
                callback_data=f"admin_manage_user_{tg_id}_back",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)
