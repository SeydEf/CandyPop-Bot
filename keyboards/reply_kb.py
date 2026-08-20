"""
Main menu reply keyboard.
"""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

# Button labels (Persian)
BTN_BUY = "🛒 خرید اشتراک"
BTN_MY_SUBS = "📋 اشتراک‌های من"
BTN_TEST = "🎁 اشتراک تست"
BTN_PROFILE = "👤 پروفایل"
BTN_PRICING = "💰 تعرفه‌ها"
BTN_GUIDE = "📖 راهنمای اتصال"
BTN_FAQ = "❓ سوالات متداول"
BTN_SUPPORT = "🆘 پشتیبانی"
BTN_INVITE = "👥 دعوت از دوستان"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Build the main menu reply keyboard (3 columns layout)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BTN_BUY),
                KeyboardButton(text=BTN_MY_SUBS),
            ],
            [
                KeyboardButton(text=BTN_TEST),
                KeyboardButton(text=BTN_PROFILE),
            ],
            [
                KeyboardButton(text=BTN_PRICING),
                KeyboardButton(text=BTN_GUIDE),
            ],
            [
                KeyboardButton(text=BTN_FAQ),
                KeyboardButton(text=BTN_SUPPORT),
            ],
            [
                KeyboardButton(text=BTN_INVITE),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
