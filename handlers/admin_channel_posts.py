import html
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from config import BOT_NAME, SUPPORT_LINK
from db.models import (
    PERMISSION_TITLES,
    delete_channel_template,
    get_active_channel_template,
    get_channel_auto_buttons,
    get_channel_lock_config,
    get_channel_posts_config,
    get_channel_templates,
    has_admin_permission,
    is_owner,
    reset_channel_posts_config,
    save_channel_template,
    set_channel_auto_buttons,
    set_channel_posts_config,
)
from utils.formatting import format_datetime
from utils.helpers import safe_edit_text

logger = logging.getLogger(__name__)

router = Router()

# نگهداری شناسه‌های ارسالی خود ربات برای جلوگیری از اعمال دوباره کپشن
_recent_bot_sent_msg_ids: set[int] = set()

# حافظه موقت برای مدیریت آلبوم‌ها (media_group_id -> زمان ثبت)
_seen_media_groups: dict[str, float] = {}


class ChannelPostsStates(StatesGroup):
    # حالت‌های مدیریت قالب متنی
    waiting_template_title = State()
    waiting_template_text = State()
    waiting_edit_template_text = State()

    # حالت‌های ارسال پست جدید به کانال
    waiting_post_content = State()
    waiting_custom_btn_text = State()
    waiting_custom_btn_url = State()

    # حالت‌های دکمه‌های خودکار کانال
    waiting_auto_btn_custom_text = State()
    waiting_auto_btn_custom_url = State()


# -------------------------------------------------------------------------
# توابع کمکی بررسی دسترسی و اعتبارسنجی
# -------------------------------------------------------------------------
def _get_message_html(message: types.Message) -> str:
    """
    متن یا کپشن پیام را با حفظ کامل تمام قالب‌بندی‌ها (بولد، ایتالیک، هایپرلینک و...) به صورت HTML استخراج می‌کند.
    از هر دو حالت موجودیت‌های تلگرام و تگ‌های دستی HTML پشتیبانی می‌کند.
    """
    entities = message.entities or message.caption_entities or []
    has_formatting = any(
        e.type
        in (
            "bold",
            "italic",
            "underline",
            "strikethrough",
            "spoiler",
            "text_link",
            "code",
            "pre",
            "blockquote",
        )
        for e in entities
    )
    if has_formatting:
        return message.html_text or ""

    raw_text = message.caption or message.text or ""
    if any(
        tag in raw_text
        for tag in (
            "<b>",
            "<i>",
            "<u>",
            "<s>",
            "<a ",
            "<code>",
            "<pre>",
            "<blockquote>",
            "<tg-spoiler>",
        )
    ):
        return raw_text

    return message.html_text or raw_text


def _is_owner(event: types.CallbackQuery | types.Message) -> bool:
    return event.from_user is not None and is_owner(event.from_user.id)


async def _require_permission(
    event: types.CallbackQuery | types.Message, perm_key: str = "channel_posts"
) -> bool:
    if event.from_user is None:
        return False
    tg_id = event.from_user.id
    permitted = await has_admin_permission(tg_id, perm_key)
    if not permitted:
        title = PERMISSION_TITLES.get(perm_key, perm_key)
        msg = f"⛔️ شما دسترسی به بخش «{title}» را ندارید."
        if isinstance(event, types.CallbackQuery):
            await event.answer(msg, show_alert=True)
        else:
            await event.reply(msg)
        return False
    return True


def _clean_channel_identifier(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("https://t.me/"):
        cleaned = cleaned.replace("https://t.me/", "")
    if cleaned.startswith("t.me/"):
        cleaned = cleaned.replace("t.me/", "")
    return cleaned


def _is_matching_target_channel(chat: types.Chat, configured_channel: str) -> bool:
    if not configured_channel:
        return False
    target = _clean_channel_identifier(configured_channel)

    # مقایسه با نام کاربری کانال
    if chat.username:
        if target.lstrip("@").lower() == chat.username.lower():
            return True

    # مقایسه عددی شناسه کانال
    try:
        chat_id_str = str(chat.id)
        raw_chat_num = chat_id_str.removeprefix("-100").removeprefix("-")
        raw_target_num = target.removeprefix("-100").removeprefix("-")
        if raw_chat_num == raw_target_num or chat_id_str == target:
            return True
    except Exception:
        pass

    return False


def _build_inline_keyboard_from_rows(
    button_rows: list[list[dict[str, str]]],
    bot_username: str,
    support_link: str = SUPPORT_LINK,
) -> InlineKeyboardMarkup | None:
    if not button_rows:
        return None

    keyboard_rows: list[list[InlineKeyboardButton]] = []
    clean_bot_user = bot_username.lstrip("@")

    for row in button_rows:
        current_row: list[InlineKeyboardButton] = []
        for btn in row:
            btn_type = btn.get("type", "custom_url")
            btn_text = btn.get("text", "دکمه")
            btn_val = btn.get("value", "")

            target_url: str | None = None
            if btn_type == "smart_buy":
                target_url = f"https://t.me/{clean_bot_user}?start=buy"
            elif btn_type == "smart_test":
                target_url = f"https://t.me/{clean_bot_user}?start=test"
            elif btn_type == "smart_support":
                target_url = support_link or f"https://t.me/{clean_bot_user}"
            elif btn_type == "smart_bot":
                target_url = f"https://t.me/{clean_bot_user}"
            elif btn_type == "custom_url" and btn_val:
                target_url = btn_val

            if target_url:
                current_row.append(InlineKeyboardButton(text=btn_text, url=target_url))
        if current_row:
            keyboard_rows.append(current_row)

    return (
        InlineKeyboardMarkup(inline_keyboard=keyboard_rows) if keyboard_rows else None
    )


def _apply_template_placeholders(
    template_text: str,
    bot_user: types.User,
    chat: types.Chat,
    channel_link: str,
) -> str:
    now_persian = format_datetime(datetime.now())
    clean_bot_name = bot_user.username or BOT_NAME
    eff_link = channel_link or (f"@{chat.username}" if chat.username else "")
    eff_name = html.escape(chat.title or "کانال ما")

    rendered = (
        template_text.replace("{bot_username}", clean_bot_name)
        .replace("{channel_link}", eff_link)
        .replace("{channel_name}", eff_name)
        .replace("{date}", now_persian)
    )
    return rendered


# -------------------------------------------------------------------------
# ساخت پنل‌ها و کیبوردهای مدیریت در پنل ادمین
# -------------------------------------------------------------------------
async def _build_channel_posts_main_panel() -> tuple[str, InlineKeyboardMarkup]:
    posts_config = await get_channel_posts_config()
    lock_config = await get_channel_lock_config()
    active_tpl = await get_active_channel_template()

    auto_caption_status = (
        "🟢 فعال" if posts_config["auto_caption_enabled"] else "🔴 غیرفعال"
    )
    auto_buttons_status = (
        "🟢 فعال" if posts_config["auto_buttons_enabled"] else "🔴 غیرفعال"
    )

    pos_modes = {
        "append": "⬇️ پانویس (انتهای پست)",
        "prepend": "⬆️ سربرگ (ابتدای پست)",
        "replace": "🔄 جایگزینی کامل متن",
    }
    pos_title = pos_modes.get(posts_config["position_mode"], "⬇️ پانویس")

    media_types = posts_config.get("media_types", {})
    active_medias: list[str] = []
    if media_types.get("text", True):
        active_medias.append("متن")
    if media_types.get("photo", True):
        active_medias.append("عکس")
    if media_types.get("video", True):
        active_medias.append("ویدیو")
    if media_types.get("document", True):
        active_medias.append("سند")
    if media_types.get("audio", True):
        active_medias.append("صوت")

    medias_summary = "، ".join(active_medias) if active_medias else "هیچ‌کدام"

    target_channel_display = lock_config.get("channel_id") or "تنظیم نشده"
    if lock_config.get("channel_link"):
        target_channel_display += f" ({lock_config['channel_link']})"

    preview_disabled = posts_config.get("disable_web_page_preview", True)
    preview_status = "🔴 غیرفعال (مخفی)" if preview_disabled else "🟢 فعال (نمایش)"

    text = (
        "📢 <b>مدیریت پست‌ها و کپشن خودکار کانال</b>\n\n"
        "در این بخش می‌توانید کپشن خودکار، امضای پست‌ها و دکمه‌های شیشه‌ای را برای کانال متصل تنظیم کنید.\n\n"
        f"📡 <b>کانال متصل (از عضویت اجباری):</b>\n<code>{target_channel_display}</code>\n\n"
        f"✍️ <b>کپشن خودکار پست‌ها:</b> {auto_caption_status}\n"
        f"📍 <b>نحوه الصاق:</b> {pos_title}\n"
        f"📝 <b>قالب فعال:</b> {active_tpl.get('title', 'پیش‌فرض')}\n"
        f"🎛 <b>رسانه‌های فعال:</b> {medias_summary}\n"
        f"🔘 <b>دکمه‌های خودکار زیر پست:</b> {auto_buttons_status}\n"
        f"🌐 <b>پیش‌نمایش لینک‌های وب:</b> {preview_status}\n"
    )

    toggle_caption_text = (
        "🔴 غیرفعال‌سازی کپشن خودکار"
        if posts_config["auto_caption_enabled"]
        else "🟢 فعال‌سازی کپشن خودکار"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_caption_text,
                    callback_data="admin_channel_posts_toggle_caption",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 تغییر موقعیت الصاق (پانویس/سربرگ/جایگزین)",
                    callback_data="admin_channel_posts_toggle_mode",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎛 فیلتر انواع رسانه‌ها (عکس، ویدیو، متن...)",
                    callback_data="admin_channel_posts_media",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🌐 تغییر وضعیت پیش‌نمایش لینک وب (Web Preview)",
                    callback_data="admin_channel_posts_toggle_preview",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📝 مدیریت قالب‌های متنی امضا",
                    callback_data="admin_channel_posts_templates",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔘 تنظیم دکمه‌های شیشه‌ای خودکار",
                    callback_data="admin_channel_posts_buttons",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚀 ارسال پست جدید به کانال (همراه با دکمه)",
                    callback_data="admin_channel_post_compose",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 بازنشانی تنظیمات این بخش",
                    callback_data="admin_channel_posts_reset",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به تنظیمات",
                    callback_data="admin_settings_menu",
                ),
            ],
        ]
    )
    return text, keyboard


async def _build_media_types_panel() -> tuple[str, InlineKeyboardMarkup]:
    posts_config = await get_channel_posts_config()
    media_types = posts_config.get("media_types", {})

    def _st(key: str) -> str:
        return "🟢 فعال" if media_types.get(key, True) else "🔴 غیرفعال"

    text = (
        "🎛 <b>فیلتر انواع رسانه‌ها برای کپشن خودکار</b>\n\n"
        "مشخص کنید کپشن خودکار روی کدام‌یک از انواع پیام‌های ارسالی در کانال اعمال شود:\n"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"متن ساده: {_st('text')}",
                    callback_data="admin_channel_posts_toggle_media:text",
                ),
                InlineKeyboardButton(
                    text=f"عکس: {_st('photo')}",
                    callback_data="admin_channel_posts_toggle_media:photo",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"ویدیو و گیف: {_st('video')}",
                    callback_data="admin_channel_posts_toggle_media:video",
                ),
                InlineKeyboardButton(
                    text=f"اسناد و فایل: {_st('document')}",
                    callback_data="admin_channel_posts_toggle_media:document",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"فایل صوتی و وویس: {_st('audio')}",
                    callback_data="admin_channel_posts_toggle_media:audio",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به مدیریت کانال",
                    callback_data="admin_channel_posts_menu",
                ),
            ],
        ]
    )
    return text, keyboard


async def _build_templates_panel() -> tuple[str, InlineKeyboardMarkup]:
    templates = await get_channel_templates()
    cfg = await get_channel_posts_config()
    active_id = cfg.get("active_template_id")

    text = (
        "📝 <b>مدیریت قالب‌های متنی کپشن خودکار</b>\n\n"
        "می‌توانید چندین قالب مختلف با متن و متغیرهای دلخواه بسازید و هر زمان که مایل بودید، قالب فعال را تغییر دهید.\n\n"
        "<b>متغیرهای قابل استفاده در متن قالب:</b>\n"
        "• <code>{bot_username}</code> : یوزرنیم ربات\n"
        "• <code>{channel_link}</code> : لینک کانال\n"
        "• <code>{channel_name}</code> : نام یا عنوان کانال\n"
        "• <code>{date}</code> : تاریخ و ساعت به شمسی\n\n"
        "<b>قالب‌های موجود (روی هر مورد برای مشاهده و انتخاب کلیک کنید):</b>\n"
    )

    rows: list[list[InlineKeyboardButton]] = []
    for tpl in templates:
        t_id = tpl.get("id", "")
        t_title = tpl.get("title", "بدون عنوان")
        is_active = t_id == active_id
        icon = "✅ " if is_active else "📄 "
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon}{t_title}",
                    callback_data=f"admin_channel_posts_view_tpl:{t_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ افزودن قالب جدید",
                callback_data="admin_channel_posts_new_tpl",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به مدیریت کانال",
                callback_data="admin_channel_posts_menu",
            )
        ]
    )

    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _build_template_detail_panel(
    template_id: str,
) -> tuple[str, InlineKeyboardMarkup]:
    templates = await get_channel_templates()
    cfg = await get_channel_posts_config()
    active_id = cfg.get("active_template_id")

    selected: dict[str, Any] | None = None
    for t in templates:
        if t.get("id") == template_id:
            selected = t
            break

    if not selected:
        selected = templates[0] if templates else {}

    t_id = selected.get("id", "")
    t_title = selected.get("title", "بدون عنوان")
    t_text = selected.get("text", "")
    is_active = t_id == active_id

    status_str = "🟢 <b>قالب فعال کنونی</b>" if is_active else "⚪️ قالب غیرفعال"

    text = (
        f"📄 <b>مشاهده قالب:</b> {t_title}\n"
        f"وضعیت: {status_str}\n\n"
        f"<b>پیش‌نمایش متن:</b>\n"
        f"<blockquote>{t_text}</blockquote>\n"
    )

    rows: list[list[InlineKeyboardButton]] = []
    if not is_active:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ انتخاب به عنوان قالب فعال",
                    callback_data=f"admin_channel_posts_set_active_tpl:{t_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="✏️ ویرایش متن قالب",
                callback_data=f"admin_channel_posts_edit_tpl:{t_id}",
            )
        ]
    )

    if len(templates) > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 حذف این قالب",
                    callback_data=f"admin_channel_posts_del_tpl:{t_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به لیست قالب‌ها",
                callback_data="admin_channel_posts_templates",
            )
        ]
    )

    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _build_auto_buttons_panel(
    bot_username: str,
) -> tuple[str, InlineKeyboardMarkup]:
    posts_config = await get_channel_posts_config()
    buttons = await get_channel_auto_buttons()

    auto_status = "🟢 فعال" if posts_config["auto_buttons_enabled"] else "🔴 غیرفعال"
    toggle_text = (
        "🔴 غیرفعال‌سازی دکمه‌های خودکار"
        if posts_config["auto_buttons_enabled"]
        else "🟢 فعال‌سازی دکمه‌های خودکار"
    )

    text = (
        "🔘 <b>تنظیم دکمه‌های شیشه‌ای خودکار زیر پست‌های کانال</b>\n\n"
        f"وضعیت دکمه‌ها: {auto_status}\n\n"
        "با فعال بودن این قابلیت، ربات دکمه‌های تعیین‌شده را به صورت خودکار زیر تمامی پست‌های جدید کانال الصاق می‌کند.\n\n"
        "<b>چیدمان فعلی دکمه‌ها:</b>\n"
    )

    if not buttons or not any(row for row in buttons):
        text += "<i>هیچ دکمه‌ای تنظیم نشده است.</i>\n"
    else:
        for idx, row in enumerate(buttons, start=1):
            row_titles = " | ".join(f"[{b.get('text', '')}]" for b in row)
            text += f"ردیف {idx}: {row_titles}\n"

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=toggle_text,
                callback_data="admin_channel_posts_toggle_buttons",
            )
        ],
        [
            InlineKeyboardButton(
                text="➕ افزودن دکمه جدید",
                callback_data="admin_channel_posts_add_auto_btn",
            )
        ],
    ]

    if buttons and any(row for row in buttons):
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 پاکسازی همه دکمه‌ها",
                    callback_data="admin_channel_posts_clear_auto_btns",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به مدیریت کانال",
                callback_data="admin_channel_posts_menu",
            )
        ]
    )

    return text, InlineKeyboardMarkup(inline_keyboard=rows)


# -------------------------------------------------------------------------
# منوها و کال‌بک‌های بخش تنظیمات کانال در ادمین
# -------------------------------------------------------------------------
@router.callback_query(F.data == "admin_channel_posts_menu")
async def admin_channel_posts_menu_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    await state.clear()
    text, keyboard = await _build_channel_posts_main_panel()
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "admin_channel_posts_toggle_caption")
async def admin_channel_posts_toggle_caption_handler(
    callback: types.CallbackQuery,
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    cfg = await get_channel_posts_config()
    new_val = not cfg["auto_caption_enabled"]
    await set_channel_posts_config(auto_caption_enabled=new_val)
    text, keyboard = await _build_channel_posts_main_panel()
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    status_str = "فعال" if new_val else "غیرفعال"
    await callback.answer(f"کپشن خودکار با موفقیت {status_str} شد.")


@router.callback_query(F.data == "admin_channel_posts_toggle_mode")
async def admin_channel_posts_toggle_mode_handler(
    callback: types.CallbackQuery,
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    cfg = await get_channel_posts_config()
    current_mode = cfg["position_mode"]
    next_modes = {"append": "prepend", "prepend": "replace", "replace": "append"}
    new_mode = next_modes.get(current_mode, "append")
    await set_channel_posts_config(position_mode=new_mode)
    text, keyboard = await _build_channel_posts_main_panel()
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer("حالت الصاق کپشن به‌روزرسانی شد.")


@router.callback_query(F.data == "admin_channel_posts_media")
async def admin_channel_posts_media_handler(
    callback: types.CallbackQuery,
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    text, keyboard = await _build_media_types_panel()
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "admin_channel_posts_toggle_preview")
async def admin_channel_posts_toggle_preview_handler(
    callback: types.CallbackQuery,
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    cfg = await get_channel_posts_config()
    new_val = not cfg.get("disable_web_page_preview", True)
    await set_channel_posts_config(disable_web_page_preview=new_val)
    text, keyboard = await _build_channel_posts_main_panel()
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    msg = (
        "پیش‌نمایش لینک‌های وب غیرفعال شد (کادر پیش‌نمایش سایت در پیام مخفی می‌شود)."
        if new_val
        else "پیش‌نمایش لینک‌های وب فعال شد (کادر پیش‌نمایش سایت در پیام نمایش داده می‌شود)."
    )
    await callback.answer(msg, show_alert=True)


@router.callback_query(F.data.startswith("admin_channel_posts_toggle_media:"))
async def admin_channel_posts_toggle_media_handler(
    callback: types.CallbackQuery,
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    media_key = callback.data.split(":")[1]
    cfg = await get_channel_posts_config()
    media_types = dict(cfg.get("media_types", {}))
    current_val = media_types.get(media_key, True)
    media_types[media_key] = not current_val
    await set_channel_posts_config(media_types=media_types)
    text, keyboard = await _build_media_types_panel()
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer("وضعیت رسانه به‌روزرسانی شد.")


@router.callback_query(F.data == "admin_channel_posts_templates")
async def admin_channel_posts_templates_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    await state.clear()
    text, keyboard = await _build_templates_panel()
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_channel_posts_view_tpl:"))
async def admin_channel_posts_view_tpl_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    await state.clear()
    tpl_id = callback.data.split(":")[1]
    text, keyboard = await _build_template_detail_panel(tpl_id)
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_channel_posts_set_active_tpl:"))
async def admin_channel_posts_set_active_tpl_handler(
    callback: types.CallbackQuery,
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    tpl_id = callback.data.split(":")[1]
    await set_channel_posts_config(active_template_id=tpl_id)
    text, keyboard = await _build_template_detail_panel(tpl_id)
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer("قالب با موفقیت فعال شد.", show_alert=True)


@router.callback_query(F.data.startswith("admin_channel_posts_del_tpl:"))
async def admin_channel_posts_del_tpl_handler(
    callback: types.CallbackQuery,
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    tpl_id = callback.data.split(":")[1]
    await delete_channel_template(tpl_id)
    text, keyboard = await _build_templates_panel()
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer("قالب مورد نظر حذف شد.")


@router.callback_query(F.data == "admin_channel_posts_new_tpl")
async def admin_channel_posts_new_tpl_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    await state.set_state(ChannelPostsStates.waiting_template_title)
    text = (
        "➕ <b>افزودن قالب متنی جدید</b>\n\n"
        "لطفاً ابتدا یک عنوان یا نام کوتاه برای این قالب ارسال کنید:\n"
        "(مثلاً: <i>قالب اطلاع‌رسانی نوروز</i>)\n\n"
        "جهت انصراف عبارت /cancel را ارسال فرمایید."
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="admin_channel_posts_templates",
                )
            ]
        ]
    )
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=cancel_kb)
    await callback.answer()


@router.message(ChannelPostsStates.waiting_template_title)
async def admin_channel_posts_recv_tpl_title(
    message: types.Message, state: FSMContext
) -> None:
    if not await _require_permission(message, "channel_posts"):
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        text, kb = await _build_templates_panel()
        await message.answer(text, reply_markup=kb)
        return

    title = (message.text or "").strip()
    if not title:
        await message.answer("لطفاً یک عنوان متنی معتبر ارسال کنید:")
        return

    await state.update_data(new_tpl_title=title)
    await state.set_state(ChannelPostsStates.waiting_template_text)

    text = (
        f"📝 <b>عنوان قالب:</b> {title}\n\n"
        "اکنون متن قالب را با فرمت دلخواه ارسال کنید.\n\n"
        "<b>متغیرهای هوشمند قابل استفاده:</b>\n"
        "• <code>{bot_username}</code> : یوزرنیم ربات\n"
        "• <code>{channel_link}</code> : لینک کانال\n"
        "• <code>{channel_name}</code> : نام کانال\n"
        "• <code>{date}</code> : تاریخ و ساعت شمسی\n\n"
        "همچنین امکان استفاده از تگ‌های HTML نظیر &lt;b&gt;، &lt;i&gt; و لینک مجاز است.\n"
        "جهت انصراف عبارت /cancel را ارسال کنید."
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="admin_channel_posts_templates",
                )
            ]
        ]
    )
    await message.answer(text, reply_markup=cancel_kb)


@router.message(ChannelPostsStates.waiting_template_text)
async def admin_channel_posts_recv_tpl_text(
    message: types.Message, state: FSMContext
) -> None:
    if not await _require_permission(message, "channel_posts"):
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        text, kb = await _build_templates_panel()
        await message.answer(text, reply_markup=kb)
        return

    tpl_text = _get_message_html(message).strip()
    if not tpl_text:
        await message.answer("متن قالب نمی‌تواند خالی باشد. لطفاً متن را ارسال کنید:")
        return

    data = await state.get_data()
    title = data.get("new_tpl_title", "قالب جدید")
    tpl_id = f"tpl_{uuid.uuid4().hex[:8]}"

    await save_channel_template(tpl_id, title, tpl_text)
    await state.clear()

    await message.answer(f"✅ قالب «{title}» با موفقیت ذخیره شد.")
    text, kb = await _build_template_detail_panel(tpl_id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("admin_channel_posts_edit_tpl:"))
async def admin_channel_posts_edit_tpl_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    tpl_id = callback.data.split(":")[1]
    await state.set_state(ChannelPostsStates.waiting_edit_template_text)
    await state.update_data(edit_tpl_id=tpl_id)

    text = (
        "✏️ <b>ویرایش متن قالب</b>\n\n"
        "لطفاً متن جدید را ارسال کنید. متغیرهای هوشمند ({bot_username}، {channel_link}، {channel_name}، {date}) پشتیبانی می‌شوند.\n\n"
        "جهت انصراف عبارت /cancel را ارسال نمایید."
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data=f"admin_channel_posts_view_tpl:{tpl_id}",
                )
            ]
        ]
    )
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=cancel_kb)
    await callback.answer()


@router.message(ChannelPostsStates.waiting_edit_template_text)
async def admin_channel_posts_recv_edit_tpl_text(
    message: types.Message, state: FSMContext
) -> None:
    if not await _require_permission(message, "channel_posts"):
        return
    data = await state.get_data()
    tpl_id = data.get("edit_tpl_id")
    if not tpl_id:
        await state.clear()
        text, kb = await _build_templates_panel()
        await message.answer(text, reply_markup=kb)
        return

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        text, kb = await _build_template_detail_panel(tpl_id)
        await message.answer(text, reply_markup=kb)
        return

    tpl_text = _get_message_html(message).strip()
    if not tpl_text:
        await message.answer("متن قالب نمی‌تواند خالی باشد:")
        return

    templates = await get_channel_templates()
    title = "قالب"
    for t in templates:
        if t.get("id") == tpl_id:
            title = t.get("title", "قالب")
            break

    await save_channel_template(tpl_id, title, tpl_text)
    await state.clear()

    await message.answer("✅ متن قالب با موفقیت به‌روزرسانی شد.")
    text, kb = await _build_template_detail_panel(tpl_id)
    await message.answer(text, reply_markup=kb)


# -------------------------------------------------------------------------
# مدیریت دکمه‌های خودکار کانال (Auto Buttons)
# -------------------------------------------------------------------------
@router.callback_query(F.data == "admin_channel_posts_buttons")
async def admin_channel_posts_buttons_handler(
    callback: types.CallbackQuery, bot: Bot, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    await state.clear()
    bot_info = await bot.get_me()
    text, keyboard = await _build_auto_buttons_panel(bot_info.username or "")
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "admin_channel_posts_toggle_buttons")
async def admin_channel_posts_toggle_buttons_handler(
    callback: types.CallbackQuery, bot: Bot
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    cfg = await get_channel_posts_config()
    new_val = not cfg["auto_buttons_enabled"]
    await set_channel_posts_config(auto_buttons_enabled=new_val)
    bot_info = await bot.get_me()
    text, keyboard = await _build_auto_buttons_panel(bot_info.username or "")
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    status_str = "فعال" if new_val else "غیرفعال"
    await callback.answer(f"دکمه‌های خودکار {status_str} شدند.")


@router.callback_query(F.data == "admin_channel_posts_clear_auto_btns")
async def admin_channel_posts_clear_auto_btns_handler(
    callback: types.CallbackQuery, bot: Bot
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    await set_channel_auto_buttons([])
    bot_info = await bot.get_me()
    text, keyboard = await _build_auto_buttons_panel(bot_info.username or "")
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer("تمامی دکمه‌های خودکار پاکسازی شدند.")


@router.callback_query(F.data == "admin_channel_posts_add_auto_btn")
async def admin_channel_posts_add_auto_btn_handler(
    callback: types.CallbackQuery,
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    text = (
        "➕ <b>افزودن دکمه شیشه‌ای خودکار</b>\n\n"
        "یکی از دکمه‌های هوشمند اختصاصی یا دکمه با لینک دلخواه را انتخاب کنید:\n"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 خرید اشتراک",
                    callback_data="admin_channel_posts_add_smart_auto:smart_buy",
                ),
                InlineKeyboardButton(
                    text="🎁 تست رایگان",
                    callback_data="admin_channel_posts_add_smart_auto:smart_test",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👨‍💻 پشتیبانی",
                    callback_data="admin_channel_posts_add_smart_auto:smart_support",
                ),
                InlineKeyboardButton(
                    text="🤖 ورود به ربات",
                    callback_data="admin_channel_posts_add_smart_auto:smart_bot",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔗 لینک آزاد دلخواه",
                    callback_data="admin_channel_posts_add_custom_auto",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 انصراف",
                    callback_data="admin_channel_posts_buttons",
                ),
            ],
        ]
    )
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_channel_posts_add_smart_auto:"))
async def admin_channel_posts_add_smart_auto_handler(
    callback: types.CallbackQuery, bot: Bot
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    btn_type = callback.data.split(":")[1]
    titles = {
        "smart_buy": "🛒 خرید اشتراک",
        "smart_test": "🎁 تست رایگان",
        "smart_support": "👨‍💻 پشتیبانی",
        "smart_bot": "🤖 ورود به ربات",
    }
    title = titles.get(btn_type, "دکمه")

    buttons = await get_channel_auto_buttons()
    # افزودن به صورت ردیف جدید
    buttons.append([{"text": title, "type": btn_type, "value": ""}])
    await set_channel_auto_buttons(buttons)

    bot_info = await bot.get_me()
    text, keyboard = await _build_auto_buttons_panel(bot_info.username or "")
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer(f"دکمه «{title}» با موفقیت اضافه شد.")


@router.callback_query(F.data == "admin_channel_posts_add_custom_auto")
async def admin_channel_posts_add_custom_auto_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    await state.set_state(ChannelPostsStates.waiting_auto_btn_custom_text)
    text = (
        "🔗 <b>افزودن دکمه با لینک دلخواه</b>\n\n"
        "لطفاً عنوان روی دکمه را ارسال کنید:\n"
        "(مثلاً: <i>عضویت در کانال زاپاس</i>)\n\n"
        "جهت انصراف عبارت /cancel را ارسال نمایید."
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="admin_channel_posts_buttons",
                )
            ]
        ]
    )
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=cancel_kb)
    await callback.answer()


@router.message(ChannelPostsStates.waiting_auto_btn_custom_text)
async def admin_channel_posts_recv_custom_auto_text(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not await _require_permission(message, "channel_posts"):
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        bot_info = await bot.get_me()
        text, kb = await _build_auto_buttons_panel(bot_info.username or "")
        await message.answer(text, reply_markup=kb)
        return

    btn_text = (message.text or "").strip()
    if not btn_text:
        await message.answer("لطفاً یک متن معتبر برای دکمه ارسال کنید:")
        return

    await state.update_data(new_auto_btn_text=btn_text)
    await state.set_state(ChannelPostsStates.waiting_auto_btn_custom_url)

    text = (
        f"عنوان دکمه: <b>{btn_text}</b>\n\n"
        "اکنون لینک اینترنتی دکمه را ارسال کنید (باید با https:// یا http:// یا t.me/ شروع شود):\n\n"
        "جهت انصراف عبارت /cancel را ارسال کنید."
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="admin_channel_posts_buttons",
                )
            ]
        ]
    )
    await message.answer(text, reply_markup=cancel_kb)


@router.message(ChannelPostsStates.waiting_auto_btn_custom_url)
async def admin_channel_posts_recv_custom_auto_url(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not await _require_permission(message, "channel_posts"):
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        bot_info = await bot.get_me()
        text, kb = await _build_auto_buttons_panel(bot_info.username or "")
        await message.answer(text, reply_markup=kb)
        return

    url = (message.text or "").strip()
    if not (
        url.startswith("http://")
        or url.startswith("https://")
        or url.startswith("t.me/")
    ):
        await message.answer(
            "فرمت آدرس نامعتبر است. لینک باید با https:// یا t.me/ شروع شود. لطفاً مجدداً ارسال کنید:"
        )
        return

    if url.startswith("t.me/"):
        url = "https://" + url

    data = await state.get_data()
    btn_text = data.get("new_auto_btn_text", "لینک")

    buttons = await get_channel_auto_buttons()
    buttons.append([{"text": btn_text, "type": "custom_url", "value": url}])
    await set_channel_auto_buttons(buttons)
    await state.clear()

    await message.answer(f"✅ دکمه «{btn_text}» با موفقیت اضافه شد.")
    bot_info = await bot.get_me()
    text, kb = await _build_auto_buttons_panel(bot_info.username or "")
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "admin_channel_posts_reset")
async def admin_channel_posts_reset_handler(
    callback: types.CallbackQuery,
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    await reset_channel_posts_config()
    text, keyboard = await _build_channel_posts_main_panel()
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer("تنظیمات بخش کانال به حالت اولیه بازنشانی شد.")


# -------------------------------------------------------------------------
# ابزار ارسال تعاملی پست جدید به کانال (Channel Post Sender)
# -------------------------------------------------------------------------
def _build_draft_control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ افزودن دکمه شیشه‌ای",
                    callback_data="channel_draft_add_btn_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 پاکسازی دکمه‌ها",
                    callback_data="channel_draft_clear_btns",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚀 تأیید و انتشار در کانال",
                    callback_data="channel_draft_send_confirm",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف و لغو",
                    callback_data="channel_draft_cancel",
                ),
            ],
        ]
    )


@router.callback_query(F.data == "admin_channel_post_compose")
async def admin_channel_post_compose_start(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return

    lock_config = await get_channel_lock_config()
    if not lock_config.get("channel_id"):
        await callback.answer(
            "⚠️ شناسه کانال مقصد تنظیم نشده است. ابتدا در بخش «عضویت اجباری کانال» آیدی کانال را تنظیم کنید.",
            show_alert=True,
        )
        return

    await state.clear()
    await state.set_state(ChannelPostsStates.waiting_post_content)
    await state.update_data(draft_buttons=[])

    text = (
        "🚀 <b>ارسال پست جدید به کانال</b>\n\n"
        "لطفاً پیامی را که می‌خواهید در کانال منتشر شود ارسال نمایید.\n"
        "پشتیبانی کامل از تمامی انواع رسانه‌ها وجود دارد:\n"
        "• متن ساده\n"
        "• عکس یا ویدیو (با یا بدون کپشن)\n"
        "• گیف (انیمیشن)\n"
        "• فایل، سند یا موزیک و وویس\n\n"
        "پس از ارسال پیام، می‌توانید دکمه‌های شیشه‌ای دلخواه را مرحله‌به‌مرحله به آن اضافه فرمایید.\n\n"
        "جهت انصراف عبارت /cancel را ارسال نمایید."
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="admin_channel_posts_menu",
                )
            ]
        ]
    )
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=cancel_kb)
    await callback.answer()


@router.message(ChannelPostsStates.waiting_post_content)
async def admin_channel_post_recv_content(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not await _require_permission(message, "channel_posts"):
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        text, kb = await _build_channel_posts_main_panel()
        await message.answer(text, reply_markup=kb)
        return

    content_type = "text"
    file_id = ""
    text_or_caption = _get_message_html(message)

    if message.photo:
        content_type = "photo"
        file_id = message.photo[-1].file_id
    elif message.video:
        content_type = "video"
        file_id = message.video.file_id
    elif message.animation:
        content_type = "animation"
        file_id = message.animation.file_id
    elif message.document:
        content_type = "document"
        file_id = message.document.file_id
    elif message.audio:
        content_type = "audio"
        file_id = message.audio.file_id
    elif message.voice:
        content_type = "voice"
        file_id = message.voice.file_id
    elif message.text:
        content_type = "text"
    else:
        await message.answer(
            "⚠️ این نوع پیام پشتیبانی نمی‌شود. لطفاً متن یا یک فایل رسانه‌ای معتبر ارسال کنید:"
        )
        return

    await state.update_data(
        content_type=content_type,
        file_id=file_id,
        text_or_caption=text_or_caption,
        draft_buttons=[],
    )

    await message.answer("👁 <b>پیش‌نمایش پست ارسالی شما:</b>")
    bot_info = await bot.get_me()
    await _send_preview_draft(
        chat_id=message.chat.id,
        bot=bot,
        content_type=content_type,
        file_id=file_id,
        text_or_caption=text_or_caption,
        buttons=[],
        bot_username=bot_info.username or "",
    )
    await message.answer(
        "🎛 برای افزودن دکمه‌های شیشه‌ای یا انتشار پست، از گزینه‌های زیر استفاده کنید:",
        reply_markup=_build_draft_control_keyboard(),
    )


async def _send_preview_draft(
    chat_id: int,
    bot: Bot,
    content_type: str,
    file_id: str,
    text_or_caption: str,
    buttons: list[list[dict[str, str]]],
    bot_username: str,
) -> None:
    kb = _build_inline_keyboard_from_rows(buttons, bot_username)
    cfg = await get_channel_posts_config()
    preview_opts = LinkPreviewOptions(
        is_disabled=cfg.get("disable_web_page_preview", True)
    )

    try:
        if content_type == "text":
            await bot.send_message(
                chat_id=chat_id,
                text=text_or_caption or "<i>[بدون متن]</i>",
                reply_markup=kb,
                parse_mode="HTML",
                link_preview_options=preview_opts,
            )
        elif content_type == "photo":
            await bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
        elif content_type == "video":
            await bot.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
        elif content_type == "animation":
            await bot.send_animation(
                chat_id=chat_id,
                animation=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
        elif content_type == "document":
            await bot.send_document(
                chat_id=chat_id,
                document=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
        elif content_type == "audio":
            await bot.send_audio(
                chat_id=chat_id,
                audio=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
        elif content_type == "voice":
            await bot.send_voice(
                chat_id=chat_id,
                voice=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
    except TelegramBadRequest:
        if content_type == "text":
            await bot.send_message(
                chat_id=chat_id,
                text=text_or_caption or "[بدون متن]",
                reply_markup=kb,
                link_preview_options=preview_opts,
            )
        else:
            await bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
            )


@router.callback_query(F.data == "channel_draft_add_btn_menu")
async def channel_draft_add_btn_menu_handler(
    callback: types.CallbackQuery,
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    text = "➕ <b>افزودن دکمه شیشه‌ای به پیش‌نویس</b>\n\nنوع دکمه را انتخاب فرمایید:\n"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 خرید اشتراک",
                    callback_data="channel_draft_add_smart:smart_buy",
                ),
                InlineKeyboardButton(
                    text="🎁 تست رایگان",
                    callback_data="channel_draft_add_smart:smart_test",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👨‍💻 پشتیبانی",
                    callback_data="channel_draft_add_smart:smart_support",
                ),
                InlineKeyboardButton(
                    text="🤖 ورود به ربات",
                    callback_data="channel_draft_add_smart:smart_bot",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔗 لینک آزاد دلخواه",
                    callback_data="channel_draft_add_custom",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 انصراف",
                    callback_data="channel_draft_return_ctrl",
                ),
            ],
        ]
    )
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("channel_draft_add_smart:"))
async def channel_draft_add_smart_handler(
    callback: types.CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    btn_type = callback.data.split(":")[1]
    titles = {
        "smart_buy": "🛒 خرید اشتراک",
        "smart_test": "🎁 تست رایگان",
        "smart_support": "👨‍💻 پشتیبانی",
        "smart_bot": "🤖 ورود به ربات",
    }
    btn_text = titles.get(btn_type, "دکمه")

    data = await state.get_data()
    draft_buttons: list[list[dict[str, str]]] = data.get("draft_buttons", [])
    draft_buttons.append([{"text": btn_text, "type": btn_type, "value": ""}])
    await state.update_data(draft_buttons=draft_buttons)

    bot_info = await bot.get_me()
    if callback.message:
        await callback.message.answer(
            f"✅ دکمه «{btn_text}» افزوده شد. پیش‌نمایش به‌روزرسانی‌شده:"
        )
        await _send_preview_draft(
            chat_id=callback.message.chat.id,
            bot=bot,
            content_type=data.get("content_type", "text"),
            file_id=data.get("file_id", ""),
            text_or_caption=data.get("text_or_caption", ""),
            buttons=draft_buttons,
            bot_username=bot_info.username or "",
        )
        await callback.message.answer(
            "کنترل پیش‌نویس:", reply_markup=_build_draft_control_keyboard()
        )
    await callback.answer()


@router.callback_query(F.data == "channel_draft_add_custom")
async def channel_draft_add_custom_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    await state.set_state(ChannelPostsStates.waiting_custom_btn_text)
    text = (
        "🔗 <b>افزودن دکمه با لینک دلخواه</b>\n\n"
        "لطفاً عنوان روی دکمه را ارسال کنید:\n"
        "(مثلاً: <i>مشاهده وب‌سایت</i> یا <i>عضویت در کانال اخبار</i>)\n\n"
        "جهت انصراف عبارت /cancel را ارسال فرمایید."
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="channel_draft_return_ctrl",
                )
            ]
        ]
    )
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=cancel_kb)
    await callback.answer()


@router.message(ChannelPostsStates.waiting_custom_btn_text)
async def channel_draft_recv_custom_btn_text(
    message: types.Message, state: FSMContext
) -> None:
    if not await _require_permission(message, "channel_posts"):
        return
    if message.text and message.text.strip() == "/cancel":
        await message.answer(
            "کنترل پیش‌نویس:", reply_markup=_build_draft_control_keyboard()
        )
        return

    btn_text = (message.text or "").strip()
    if not btn_text:
        await message.answer("لطفاً یک متن معتبر برای دکمه ارسال کنید:")
        return

    await state.update_data(draft_custom_btn_text=btn_text)
    await state.set_state(ChannelPostsStates.waiting_custom_btn_url)

    text = (
        f"عنوان دکمه: <b>{btn_text}</b>\n\n"
        "اکنون لینک اینترنتی دکمه را ارسال نمایید (شروع با https:// یا t.me/):\n\n"
        "جهت انصراف عبارت /cancel را ارسال کنید."
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="channel_draft_return_ctrl",
                )
            ]
        ]
    )
    await message.answer(text, reply_markup=cancel_kb)


@router.message(ChannelPostsStates.waiting_custom_btn_url)
async def channel_draft_recv_custom_btn_url(
    message: types.Message, state: FSMContext, bot: Bot
) -> None:
    if not await _require_permission(message, "channel_posts"):
        return
    if message.text and message.text.strip() == "/cancel":
        await message.answer(
            "کنترل پیش‌نویس:", reply_markup=_build_draft_control_keyboard()
        )
        return

    url = (message.text or "").strip()
    if not (
        url.startswith("http://")
        or url.startswith("https://")
        or url.startswith("t.me/")
    ):
        await message.answer(
            "آدرس نامعتبر است. لینک باید با https:// یا t.me/ شروع شود. لطفاً مجدداً ارسال کنید:"
        )
        return

    if url.startswith("t.me/"):
        url = "https://" + url

    data = await state.get_data()
    btn_text = data.get("draft_custom_btn_text", "لینک")
    draft_buttons: list[list[dict[str, str]]] = data.get("draft_buttons", [])
    draft_buttons.append([{"text": btn_text, "type": "custom_url", "value": url}])
    await state.update_data(draft_buttons=draft_buttons)

    bot_info = await bot.get_me()
    await message.answer(f"✅ دکمه «{btn_text}» افزوده شد. پیش‌نمایش به‌روزرسانی‌شده:")
    await _send_preview_draft(
        chat_id=message.chat.id,
        bot=bot,
        content_type=data.get("content_type", "text"),
        file_id=data.get("file_id", ""),
        text_or_caption=data.get("text_or_caption", ""),
        buttons=draft_buttons,
        bot_username=bot_info.username or "",
    )
    await message.answer("کنترل پیش‌نویس:", reply_markup=_build_draft_control_keyboard())


@router.callback_query(F.data == "channel_draft_clear_btns")
async def channel_draft_clear_btns_handler(
    callback: types.CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return
    await state.update_data(draft_buttons=[])
    data = await state.get_data()
    bot_info = await bot.get_me()

    if callback.message:
        await callback.message.answer("🗑 دکمه‌های پیش‌نویس پاک شدند. پیش‌نمایش:")
        await _send_preview_draft(
            chat_id=callback.message.chat.id,
            bot=bot,
            content_type=data.get("content_type", "text"),
            file_id=data.get("file_id", ""),
            text_or_caption=data.get("text_or_caption", ""),
            buttons=[],
            bot_username=bot_info.username or "",
        )
        await callback.message.answer(
            "کنترل پیش‌نویس:", reply_markup=_build_draft_control_keyboard()
        )
    await callback.answer("تمامی دکمه‌ها حذف شدند.")


@router.callback_query(F.data == "channel_draft_return_ctrl")
async def channel_draft_return_ctrl_handler(
    callback: types.CallbackQuery,
) -> None:
    if callback.message:
        await safe_edit_text(
            callback.message,
            "کنترل پیش‌نویس پست کانال:",
            reply_markup=_build_draft_control_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "channel_draft_cancel")
async def channel_draft_cancel_handler(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.clear()
    text, keyboard = await _build_channel_posts_main_panel()
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer("ارسال پست لغو شد.")


@router.callback_query(F.data == "channel_draft_send_confirm")
async def channel_draft_send_confirm_handler(
    callback: types.CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not await _require_permission(callback, "channel_posts"):
        return

    data = await state.get_data()
    content_type = data.get("content_type", "text")
    file_id = data.get("file_id", "")
    text_or_caption = data.get("text_or_caption", "")
    buttons: list[list[dict[str, str]]] = data.get("draft_buttons", [])

    lock_config = await get_channel_lock_config()
    target_channel = lock_config.get("channel_id")
    if not target_channel:
        await callback.answer(
            "⚠️ شناسه کانال مقصد یافت نشد. لطفاً در تنظیمات عضویت اجباری شناسه کانال را ثبت کنید.",
            show_alert=True,
        )
        return

    bot_info = await bot.get_me()
    kb = _build_inline_keyboard_from_rows(buttons, bot_info.username or "")

    posts_config = await get_channel_posts_config()
    disable_preview = posts_config.get("disable_web_page_preview", True)
    preview_opts = LinkPreviewOptions(is_disabled=disable_preview)

    sent_msg: types.Message | None = None
    try:
        if content_type == "text":
            if not text_or_caption or not text_or_caption.strip():
                await callback.answer(
                    "⚠️ متن پیام خالی است. ارسال پیام متنی بدون محتوا امکان‌پذیر نیست.",
                    show_alert=True,
                )
                return
            sent_msg = await bot.send_message(
                chat_id=target_channel,
                text=text_or_caption,
                reply_markup=kb,
                parse_mode="HTML",
                link_preview_options=preview_opts,
            )
        elif content_type == "photo":
            sent_msg = await bot.send_photo(
                chat_id=target_channel,
                photo=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
        elif content_type == "video":
            sent_msg = await bot.send_video(
                chat_id=target_channel,
                video=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
        elif content_type == "animation":
            sent_msg = await bot.send_animation(
                chat_id=target_channel,
                animation=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
        elif content_type == "document":
            sent_msg = await bot.send_document(
                chat_id=target_channel,
                document=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
        elif content_type == "audio":
            sent_msg = await bot.send_audio(
                chat_id=target_channel,
                audio=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
        elif content_type == "voice":
            sent_msg = await bot.send_voice(
                chat_id=target_channel,
                voice=file_id,
                caption=text_or_caption or None,
                reply_markup=kb,
                parse_mode="HTML" if text_or_caption else None,
            )
    except TelegramAPIError as e:
        logger.error(f"Failed to post to channel {target_channel}: {e}")
        await callback.answer(
            f"❌ ارسال پست به کانال با خطا مواجه شد:\n{e}", show_alert=True
        )
        return

    if sent_msg:
        _recent_bot_sent_msg_ids.add(sent_msg.message_id)

    await state.clear()
    await callback.answer("✅ پست با موفقیت در کانال منتشر شد.", show_alert=True)

    text, keyboard = await _build_channel_posts_main_panel()
    if callback.message:
        await callback.message.answer(
            "🎉 <b>پست با موفقیت در کانال منتشر شد.</b>",
            reply_markup=keyboard,
        )


# -------------------------------------------------------------------------
# شنونده رویدادهای ارسالی در کانال (Channel Post Auto-Caption Listener)
# -------------------------------------------------------------------------
@router.channel_post()
async def channel_post_auto_caption_listener(message: types.Message, bot: Bot) -> None:
    # ۱. اگر پیام توسط ابزار ارسال پست ربات فرستاده شده است، صرف‌نظر شود
    if message.message_id in _recent_bot_sent_msg_ids:
        _recent_bot_sent_msg_ids.discard(message.message_id)
        return

    # ۲. بررسی وضعیت فعال بودن کپشن خودکار
    posts_config = await get_channel_posts_config()
    if not posts_config.get("auto_caption_enabled", False):
        return

    # ۳. بررسی تطابق کانال با کانال هدف
    lock_config = await get_channel_lock_config()
    target_channel_cfg = lock_config.get("channel_id", "")
    if not target_channel_cfg or not _is_matching_target_channel(
        message.chat, target_channel_cfg
    ):
        return

    # ۴. مدیریت آلبوم‌ها (Media Groups): اعمال فقط روی اولین بخش آلبوم
    if message.media_group_id:
        now = time.time()
        expired_groups = [
            gid for gid, ts in _seen_media_groups.items() if now - ts > 60.0
        ]
        for gid in expired_groups:
            _seen_media_groups.pop(gid, None)

        if message.media_group_id in _seen_media_groups:
            return
        _seen_media_groups[message.media_group_id] = now

    # ۵. بررسی نوع رسانه
    media_types_cfg = posts_config.get("media_types", {})
    msg_type = "text"
    is_media = False
    original_text = ""

    if message.photo:
        msg_type = "photo"
        is_media = True
        original_text = message.caption or ""
    elif message.video or message.animation:
        msg_type = "video"
        is_media = True
        original_text = message.caption or ""
    elif message.document:
        msg_type = "document"
        is_media = True
        original_text = message.caption or ""
    elif message.audio or message.voice:
        msg_type = "audio"
        is_media = True
        original_text = message.caption or ""
    elif message.text:
        msg_type = "text"
        is_media = False
        original_text = message.text
    else:
        return

    if not media_types_cfg.get(msg_type, True):
        return

    # ۶. دریافت قالب فعال و اعمال متغیرها
    active_tpl = await get_active_channel_template()
    tpl_raw_text = active_tpl.get("text", "")
    if not tpl_raw_text.strip():
        return

    bot_info = await bot.get_me()
    rendered_tpl = _apply_template_placeholders(
        tpl_raw_text,
        bot_info,
        message.chat,
        lock_config.get("channel_link", ""),
    )

    # ۷. ترکیب متن بر اساس حالت الصاق
    pos_mode = posts_config.get("position_mode", "append")
    if pos_mode == "append":
        if original_text.strip():
            final_text = f"{original_text.strip()}\n\n{rendered_tpl}"
        else:
            final_text = rendered_tpl
    elif pos_mode == "prepend":
        if original_text.strip():
            final_text = f"{rendered_tpl}\n\n{original_text.strip()}"
        else:
            final_text = rendered_tpl
    elif pos_mode == "replace":
        final_text = rendered_tpl
    else:
        final_text = f"{original_text}\n\n{rendered_tpl}"

    # ۸. بررسی سقف کاراکتر تلگرام
    max_len = 1024 if is_media else 4096
    if len(final_text) > max_len:
        logger.warning(
            f"Auto-caption text length ({len(final_text)}) exceeds Telegram limit ({max_len}). Skipping edit."
        )
        return

    # ۹. آماده‌سازی دکمه‌های شیشه‌ای خودکار
    reply_markup: InlineKeyboardMarkup | None = None
    if posts_config.get("auto_buttons_enabled", False):
        auto_buttons = await get_channel_auto_buttons()
        reply_markup = _build_inline_keyboard_from_rows(
            auto_buttons, bot_info.username or ""
        )

    # ۱۰. ویرایش پیام در کانال
    try:
        if is_media:
            await bot.edit_message_caption(
                chat_id=message.chat.id,
                message_id=message.message_id,
                caption=final_text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        else:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=message.message_id,
                text=final_text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(
                    is_disabled=posts_config.get("disable_web_page_preview", True)
                ),
            )
        logger.info(
            f"Auto-caption applied to message {message.message_id} in channel {message.chat.id}"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        if "can't parse entities" in str(e).lower():
            logger.warning(
                f"HTML entity parse error on channel post {message.message_id}: {e}. Retrying without HTML parse mode."
            )
            try:
                if is_media:
                    await bot.edit_message_caption(
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        caption=final_text,
                        reply_markup=reply_markup,
                        parse_mode=None,
                    )
                else:
                    await bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        text=final_text,
                        reply_markup=reply_markup,
                        parse_mode=None,
                        link_preview_options=LinkPreviewOptions(
                            is_disabled=posts_config.get(
                                "disable_web_page_preview", True
                            )
                        ),
                    )
                return
            except Exception:
                pass
        logger.warning(
            f"TelegramBadRequest while editing channel post {message.message_id}: {e}"
        )
    except Exception as e:
        logger.warning(
            f"Could not apply auto-caption to channel post {message.message_id}: {e}"
        )
