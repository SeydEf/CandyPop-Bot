from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_TEST = "🎁 اشتراک تست"
BTN_BUY = "🛒 خرید اشتراک"
BTN_MY_SUBS = "📋 اشتراک‌های من"
BTN_INCREASE_WALLET = "💳 افزایش موجودی"
BTN_PROFILE = "👤 پروفایل"
BTN_PRICING = "💰 تعرفه‌ها"
BTN_GUIDE = "📖 راهنمای اتصال"
BTN_SUPPORT = "🆘 پشتیبانی"
BTN_INVITE = "👥 دعوت از دوستان"
# BTN_FAQ = "❓ سوالات متداول"


_pricing_button_hidden: bool = False


def set_pricing_button_hidden_cache(hidden: bool) -> None:
    global _pricing_button_hidden
    _pricing_button_hidden = hidden


def is_pricing_button_hidden() -> bool:
    return _pricing_button_hidden


def main_menu_keyboard(show_pricing: bool | None = None) -> ReplyKeyboardMarkup:
    if show_pricing is None:
        show_pricing = not _pricing_button_hidden

    pricing_row = (
        [KeyboardButton(text=BTN_PRICING), KeyboardButton(text=BTN_GUIDE)]
        if show_pricing
        else [KeyboardButton(text=BTN_GUIDE)]
    )

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BTN_TEST),
            ],
            [
                KeyboardButton(text=BTN_BUY),
                KeyboardButton(text=BTN_MY_SUBS),
            ],
            [
                KeyboardButton(text=BTN_PROFILE),
                KeyboardButton(text=BTN_INCREASE_WALLET),
            ],
            pricing_row,
            [
                KeyboardButton(text=BTN_INVITE),
            ],
            [
                KeyboardButton(text=BTN_SUPPORT),
            ],
        ],
        resize_keyboard=True,
    )
