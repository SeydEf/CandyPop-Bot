from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.guide_data import get_client_info, get_os_info


def guide_os_keyboard() -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="📱 اندروید (Android)",
                callback_data="guide_os:android",
            ),
            InlineKeyboardButton(
                text="🍏 آیفون / آیپد (iOS)",
                callback_data="guide_os:ios",
            ),
        ],
        [
            InlineKeyboardButton(
                text="💻 ویندوز (Windows)",
                callback_data="guide_os:windows",
            ),
            InlineKeyboardButton(
                text="🍎 مک (macOS)",
                callback_data="guide_os:macos",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🐧 لینوکس (Linux)",
                callback_data="guide_os:linux",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def guide_apps_keyboard(os_key: str) -> InlineKeyboardMarkup:
    os_info = get_os_info(os_key)
    if not os_info:
        return guide_os_keyboard()

    apps = os_info.get("apps", [])
    recommended = set(os_info.get("recommended", []))
    buttons: list[list[InlineKeyboardButton]] = []
    other_buttons: list[InlineKeyboardButton] = []

    for app_key in apps:
        app_data = get_client_info(app_key, os_key)
        if not app_data:
            continue
        icon = app_data.get("icon", "📱")
        name = app_data.get("name", app_key)
        if app_key in recommended:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"⭐️ {icon} {name} (پیشنهادی)",
                        callback_data=f"guide_app:{os_key}:{app_key}",
                    )
                ]
            )
        else:
            other_buttons.append(
                InlineKeyboardButton(
                    text=f"{icon} {name}",
                    callback_data=f"guide_app:{os_key}:{app_key}",
                )
            )

    row: list[InlineKeyboardButton] = []
    for btn in other_buttons:
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به سیستم‌عامل‌ها",
                callback_data="guide_back_os",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def guide_detail_keyboard(os_key: str, app_key: str) -> InlineKeyboardMarkup:
    app_info = get_client_info(app_key, os_key)
    os_info = get_os_info(os_key)
    buttons: list[list[InlineKeyboardButton]] = []

    if app_info:
        direct_link = app_info.get("direct_link")
        store_link = app_info.get("store_link")
        store_name = app_info.get("store_name")

        action_row: list[InlineKeyboardButton] = []
        if direct_link:
            action_row.append(
                InlineKeyboardButton(
                    text="⬇️ دانلود آخرین نسخه",
                    url=direct_link,
                )
            )
        if store_link and store_name and store_link != direct_link:
            action_row.append(
                InlineKeyboardButton(
                    text=f"🏪 {store_name}",
                    url=store_link,
                )
            )

        if action_row:
            buttons.append(action_row)

    badge = os_info.get("badge", "سیستم‌عامل") if os_info else "برنامه‌ها"
    buttons.append(
        [
            InlineKeyboardButton(
                text=f"🔙 لیست برنامه‌های {badge}",
                callback_data=f"guide_back_app:{os_key}",
            ),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="🏠 انتخاب سیستم‌عامل",
                callback_data="guide_back_os",
            ),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)
