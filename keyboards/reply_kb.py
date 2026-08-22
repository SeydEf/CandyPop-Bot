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


def main_menu_keyboard() -> ReplyKeyboardMarkup:
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
            [
                KeyboardButton(text=BTN_PRICING),
                KeyboardButton(text=BTN_GUIDE),
            ],
            [
                KeyboardButton(text=BTN_INVITE),
            ],
            [
                KeyboardButton(text=BTN_SUPPORT),
            ],
        ],
        resize_keyboard=True,
    )
