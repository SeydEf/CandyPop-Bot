from __future__ import annotations

from typing import Any

OS_DATA: dict[str, dict[str, Any]] = {
    "android": {
        "title": "📱 اندروید (Android)",
        "badge": "Android",
        "description": "سیستم‌عامل اندروید از طیف گسترده‌ای از نرم‌افزارهای استاندارد و سریع پشتیبانی می‌کند. یکی از برنامه‌های زیر را انتخاب نمایید:",
        "apps": ["v2rayng", "hiddify", "nekobox", "singbox"],
    },
    "ios": {
        "title": "🍏 آیفون و آیپد (iOS)",
        "badge": "iOS",
        "description": "برای دستگاه‌های آیفون و آیپد، برنامه‌های زیر با عملکرد عالی و اتصال پایدار پیشنهاد می‌شوند:",
        "apps": ["streisand", "v2box", "hiddify"],
    },
    "windows": {
        "title": "💻 ویندوز (Windows)",
        "badge": "Windows",
        "description": "نرم‌افزارهای مخصوص سیستم‌عامل ویندوز برای کامپیوتر و لپ‌تاپ:",
        "apps": ["v2rayn", "hiddify", "nekoray", "singbox"],
    },
    "macos": {
        "title": "🍎 مک (macOS)",
        "badge": "macOS",
        "description": "نرم‌افزارهای سازگار با سیستم‌عامل macOS (اینتل و پردازنده‌های سری M اپل):",
        "apps": ["v2box", "hiddify", "streisand", "foxray"],
    },
    "linux": {
        "title": "🐧 لینوکس (Linux)",
        "badge": "Linux",
        "description": "نرم‌افزارهای گرافیکی و خط فرمانی متناسب با توزیع‌های مختلف لینوکس:",
        "apps": ["hiddify", "v2raya", "singbox"],
    },
}

CLIENT_DATA: dict[str, dict[str, Any]] = {
    "v2rayng": {
        "name": "v2rayNG",
        "icon": "⚡️",
        "desc": "محبوب‌ترین، پایدارترین و سبک‌ترین کلاینت اندروید با پشتیبانی از تمامی پروتکل‌ها و هسته به‌روز Xray.",
        "direct_link": "https://github.com/2dust/v2rayNG/releases/latest",
        "store_link": None,
        "store_name": None,
        "steps": [
            "ابتدا لینک ساب‌اسکریپشن اشتراک خود را در تلگرام کپی کنید.",
            "برنامه <b>v2rayNG</b> را باز کنید.",
            "روی آیکون <b>+</b> (بالا سمت راست) یا منوی برنامه ضربه بزنید.",
            "گزینه <b>Import config from clipboard</b> را انتخاب کنید تا لیست سرورها افزوده شود.",
            "یکی از کانفیگ‌ها را انتخاب کرده و روی دکمه دایره‌ای اتصال (پایین صفحه) بزنید.",
        ],
        "tip": "برای به‌روزرسانی دوره‌ای سرورها، از منوی سه‌نقطه بالا گزینه Update subscription را لمس کنید.",
    },
    "hiddify": {
        "name": "Hiddify",
        "icon": "🚀",
        "desc": "کلاینت فوق‌العاده مدرن، تمام خودکار با منوی فارسی و قابلیت انتخاب خودکار سریع‌ترین کانکشن بدون قطعی.",
        "direct_link": "https://github.com/hiddify/hiddify-next/releases/latest",
        "store_link": "https://play.google.com/store/apps/details?id=app.hiddify.com",
        "store_name": "Google Play",
        "steps": [
            "لینک ساب‌اسکریپشن خود را کپی کنید.",
            "برنامه <b>Hiddify</b> را اجرا کنید.",
            "روی دکمه <b>New Profile</b> یا علامت <b>+</b> (بالا سمت راست) بزنید.",
            "گزینه <b>Add from Clipboard</b> را لمس کنید تا کانفیگ‌ها بارگذاری شوند.",
            "دکمه بزرگ دایره‌ای <b>Connect</b> را لمس کنید تا متصل شوید.",
        ],
        "tip": "در بخش تنظیمات برنامه می‌توانید زبان برنامه را فارسی کرده و حالت اتصال هوشمند را فعال کنید.",
    },
    "nekobox": {
        "name": "NekoBox",
        "icon": "🐱",
        "desc": "کلاینت قدرتمند و منعطف بر پایه Sing-box مناسب برای عبور از اختلالات شدید اینترنت.",
        "direct_link": "https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest",
        "store_link": None,
        "store_name": None,
        "steps": [
            "لینک ساب‌اسکریپشن خود را کپی کنید.",
            "برنامه <b>NekoBox</b> را باز کنید.",
            "علامت <b>+</b> (بالا سمت راست) را لمس کرده و <b>Import from clipboard</b> را انتخاب نمایید.",
            "نام گروه ساخته‌شده را باز کنید و یک سرور را برگزینید.",
            "دکمه سوئیچ پایین صفحه را برای آغاز اتصال روشن کنید.",
        ],
        "tip": "برای تست تاخیر و پینگ سرورها، از دکمه تست اتصال (آیکون رعدوبرق) استفاده کنید.",
    },
    "singbox": {
        "name": "Sing-box",
        "icon": "📦",
        "desc": "کلاینت رسمی و بسیار بهینه، سبک و کم‌مصرف بر پایه هسته Sing-box.",
        "direct_link": "https://github.com/SagerNet/sing-box/releases/latest",
        "store_link": None,
        "store_name": None,
        "steps": [
            "لینک ساب‌اسکریپشن خود را کپی کنید.",
            "برنامه <b>Sing-box</b> را باز کرده و به تب <b>Profiles</b> بروید.",
            "روی دکمه <b>New Profile</b> ضربه زده، نوع را Remote بگذارید و لینک را در قسمت URL وارد کنید.",
            "پروفایل را ذخیره (Create) کرده و به تب <b>Dashboard</b> بازگردید.",
            "سوئیچ اتصال (Enabled) را روشن کنید.",
        ],
        "tip": "در تب Dashboard می‌توانید سرعت دانلود و آپلود لحظه‌ای را مانیتور کنید.",
    },
    "streisand": {
        "name": "Streisand",
        "icon": "🎻",
        "desc": "یکی از بهترین، محبوب‌ترین و روان‌ترین اپلیکیشن‌های رایگان در اپ‌استور برای آیفون و آیپد.",
        "direct_link": None,
        "store_link": "https://apps.apple.com/us/app/streisand/id6450534064",
        "store_name": "App Store",
        "steps": [
            "ابتدا لینک ساب‌اسکریپشن اشتراک خود را کپی کنید.",
            "برنامه <b>Streisand</b> را در آیفون خود باز کنید.",
            "روی آیکون <b>+</b> (بالا سمت راست) ضربه بزنید.",
            "گزینه <b>Import from Clipboard</b> را لمس کنید.",
            "دکمه بزرگ دایره‌ای وسط صفحه را لمس کنید تا اتصال شما برقرار گردد.",
        ],
        "tip": "برنامه به‌طور خودکار هنگام اتصال، ساب‌اسکریپشن شما را به‌روزرسانی می‌کند.",
    },
    "v2box": {
        "name": "V2Box",
        "icon": "📦",
        "desc": "اپلیکیشن رایگان و قدرتمند iOS و macOS با اتصال پایدار و رابط کاربری چشم‌نواز.",
        "direct_link": None,
        "store_link": "https://apps.apple.com/us/app/v2box-v2ray-client/id6446814690",
        "store_name": "App Store",
        "steps": [
            "لینک ساب‌اسکریپشن را کپی کنید.",
            "برنامه <b>V2Box</b> را باز کرده و از نوار پایین وارد بخش <b>Configs</b> شوید.",
            "روی دکمه <b>+</b> (بالا سمت راست) بزنید و گزینه <b>Add Subscription</b> را انتخاب کنید.",
            "یک نام دلخواه تعیین کرده و لینک را در فیلد Subscription URL وارد نمایید و دکمه Add را بزنید.",
            "به صفحه <b>Home</b> بازگشته و دکمه اتصال (Connect) را روشن کنید.",
        ],
        "tip": "در بخش Configs می‌توانید پینگ سرورها را با دکمه Ping Test مشاهده فرمایید.",
    },
    "v2rayn": {
        "name": "v2rayN",
        "icon": "💻",
        "desc": "استانداردترین و کامل‌ترین نرم‌افزار ویندوز با پشتیبانی از کلیه پروتکل‌های مدرن.",
        "direct_link": "https://github.com/2dust/v2rayN/releases/latest",
        "store_link": None,
        "store_name": None,
        "steps": [
            "فایل زیپ آخرین نسخه v2rayN را دانلود و در یک پوشه Extract کنید و فایل <code>v2rayN.exe</code> را اجرا نمایید.",
            "لینک ساب‌اسکریپشن خود را کپی کنید.",
            "از منوی بالای برنامه روی <b>Subscription Group</b> کلیک کرده و گزینه <b>Subscription group setting</b> را بزنید.",
            "روی <b>Add</b> کلیک کنید، در کادر URL لینک ساب را Paste نموده و <b>Confirm</b> را بزنید.",
            "مجدداً روی منوی <b>Subscription Group</b> کلیک کرده و <b>Update subscription without proxy</b> را بزنید.",
            "یک سرور را انتخاب کرده، دکمه Enter را بزنید و در نوار پایین برنامه وضعیت System proxy را روی <b>Set system proxy</b> بگذارید.",
        ],
        "tip": "رنگ آیکون v2rayN در تسک‌بار ویندوز پس از اتصال موفق به رنگ آبی یا قرمز تغییر می‌کند.",
    },
    "nekoray": {
        "name": "NekoRay",
        "icon": "🐱",
        "desc": "کلاینت سریع، سبک و بدون دردسر با قابلیت اجرای حالت VPN کامل در ویندوز.",
        "direct_link": "https://github.com/MatsuriDayo/nekoray/releases/latest",
        "store_link": None,
        "store_name": None,
        "steps": [
            "فایل فشرده آخرین نسخه NekoRay را دانلود و اجرا کنید.",
            "لینک ساب‌اسکریپشن خود را کپی کنید.",
            "در برنامه از منوی <b>Program</b> گزینه <b>Add profile from clipboard</b> را بزنید.",
            "سرور دلخواه را انتخاب کرده و روی آن راست‌کلیک کرده و <b>Start</b> را بزنید.",
            "تیک گزینه <b>VPN Mode</b> یا <b>System Proxy</b> بالای برنامه را فعال نمایید.",
        ],
        "tip": "در صورتی که برخی برنامه‌ها از پروکسی عبور نمی‌کنند، گزینه VPN Mode را فعال فرمایید.",
    },
    "v2raya": {
        "name": "v2rayA",
        "icon": "🐧",
        "desc": "کلاینت تحت وب فوق‌العاده برای توزیع‌های لینوکس با قابلیت Transparent Proxy کل سیستم.",
        "direct_link": "https://github.com/v2rayA/v2rayA/releases/latest",
        "store_link": None,
        "store_name": None,
        "steps": [
            "نرم‌افزار v2rayA را روی توزیع لینوکس خود نصب کرده و سرویس آن را اجرا کنید (<code>sudo systemctl start v2raya</code>).",
            "مرورگر را باز کرده و به آدرس <code>http://localhost:2017</code> بروید.",
            "روی دکمه <b>Import</b> کلیک کرده و لینک ساب‌اسکریپشن را وارد کنید.",
            "سرورهای مورد نظر را علامت زده، دکمه <b>Ready</b> و سپس <b>Start</b> در بالا را بزنید.",
        ],
        "tip": "در تنظیمات وب می‌توانید شفاف‌سازی پورت‌ها (Transparent Proxy) را فعال کنید.",
    },
}


def get_os_info(os_key: str) -> dict[str, Any] | None:
    return OS_DATA.get(os_key)


def get_client_info(app_key: str, os_key: str | None = None) -> dict[str, Any] | None:
    app_info = CLIENT_DATA.get(app_key)
    if not app_info:
        return None

    info = dict(app_info)

    if os_key == "ios":
        if app_key == "hiddify":
            info["direct_link"] = None
            info["store_link"] = (
                "https://apps.apple.com/us/app/hiddify-proxy-vpn/id6596777532"
            )
            info["store_name"] = "App Store"
    elif os_key == "windows":
        if app_key == "hiddify":
            info["direct_link"] = (
                "https://github.com/hiddify/hiddify-next/releases/latest"
            )
    elif os_key == "macos":
        if app_key == "hiddify":
            info["direct_link"] = None
            info["store_link"] = (
                "https://apps.apple.com/app/hiddify-proxy-vpn/id6596777532"
            )
            info["store_name"] = "App Store"
    elif os_key == "linux":
        if app_key == "hiddify":
            info["direct_link"] = (
                "https://github.com/hiddify/hiddify-next/releases/latest"
            )
            info["store_link"] = None
            info["store_name"] = None

    return info
