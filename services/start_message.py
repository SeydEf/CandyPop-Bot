from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import get_start_message_config

logger = logging.getLogger(__name__)

MESSAGE_TYPE_TITLES: dict[str, str] = {
    "text": "📝 متن",
    "photo": "🖼 تصویر",
    "video": "🎬 ویدیو",
    "animation": "🎞 گیف / انیمیشن",
    "document": "📁 فایل / سند",
    "audio": "🎵 آهنگ / صدا",
    "voice": "🎙 پیام صوتی (وویس)",
    "sticker": "🎭 استیکر",
}


def extract_message_data(message: types.Message) -> dict[str, Any]:
    msg_type = "text"
    file_id = None
    text_content = None

    if message.photo:
        msg_type = "photo"
        file_id = message.photo[-1].file_id
        text_content = message.html_text if message.caption else None
    elif message.video:
        msg_type = "video"
        file_id = message.video.file_id
        text_content = message.html_text if message.caption else None
    elif message.animation:
        msg_type = "animation"
        file_id = message.animation.file_id
        text_content = message.html_text if message.caption else None
    elif message.document:
        msg_type = "document"
        file_id = message.document.file_id
        text_content = message.html_text if message.caption else None
    elif message.audio:
        msg_type = "audio"
        file_id = message.audio.file_id
        text_content = message.html_text if message.caption else None
    elif message.voice:
        msg_type = "voice"
        file_id = message.voice.file_id
        text_content = message.html_text if message.caption else None
    elif message.sticker:
        msg_type = "sticker"
        file_id = message.sticker.file_id
        text_content = None
    elif message.text:
        msg_type = "text"
        file_id = None
        text_content = message.html_text

    buttons: list[list[dict[str, str]]] = []
    if message.reply_markup and getattr(message.reply_markup, "inline_keyboard", None):
        for row in message.reply_markup.inline_keyboard:
            row_buttons = []
            for btn in row:
                btn_dict: dict[str, str] = {"text": btn.text}
                if getattr(btn, "url", None):
                    btn_dict["url"] = btn.url
                elif getattr(btn, "callback_data", None):
                    btn_dict["callback_data"] = btn.callback_data
                row_buttons.append(btn_dict)
            if row_buttons:
                buttons.append(row_buttons)

    return {
        "type": msg_type,
        "file_id": file_id,
        "content": text_content,
        "buttons": buttons,
    }


def build_keyboard_from_buttons(
    buttons: list[list[dict[str, str]]] | None,
) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for row in buttons:
        row_btns: list[InlineKeyboardButton] = []
        for b in row:
            text = b.get("text", "")
            url = b.get("url")
            callback_data = b.get("callback_data")
            if url:
                row_btns.append(InlineKeyboardButton(text=text, url=url))
            elif callback_data:
                row_btns.append(
                    InlineKeyboardButton(text=text, callback_data=callback_data)
                )
        if row_btns:
            keyboard_rows.append(row_btns)
    return (
        InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
        if keyboard_rows
        else None
    )


async def send_start_payload(
    bot: Bot,
    chat_id: int,
    message_data: dict[str, Any],
    extra_keyboard: InlineKeyboardMarkup | None = None,
) -> types.Message | None:
    msg_type = message_data.get("type", "text")
    file_id = message_data.get("file_id")
    content = message_data.get("content")
    buttons = message_data.get("buttons") or []

    reply_markup = extra_keyboard or build_keyboard_from_buttons(buttons)

    if msg_type == "photo" and file_id:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=file_id,
            caption=content,
            parse_mode="HTML" if content else None,
            reply_markup=reply_markup,
        )
    elif msg_type == "video" and file_id:
        return await bot.send_video(
            chat_id=chat_id,
            video=file_id,
            caption=content,
            parse_mode="HTML" if content else None,
            reply_markup=reply_markup,
        )
    elif msg_type == "animation" and file_id:
        return await bot.send_animation(
            chat_id=chat_id,
            animation=file_id,
            caption=content,
            parse_mode="HTML" if content else None,
            reply_markup=reply_markup,
        )
    elif msg_type == "document" and file_id:
        return await bot.send_document(
            chat_id=chat_id,
            document=file_id,
            caption=content,
            parse_mode="HTML" if content else None,
            reply_markup=reply_markup,
        )
    elif msg_type == "audio" and file_id:
        return await bot.send_audio(
            chat_id=chat_id,
            audio=file_id,
            caption=content,
            parse_mode="HTML" if content else None,
            reply_markup=reply_markup,
        )
    elif msg_type == "voice" and file_id:
        return await bot.send_voice(
            chat_id=chat_id,
            voice=file_id,
            caption=content,
            parse_mode="HTML" if content else None,
            reply_markup=reply_markup,
        )
    elif msg_type == "sticker" and file_id:
        return await bot.send_sticker(
            chat_id=chat_id,
            sticker=file_id,
            reply_markup=reply_markup,
        )
    else:
        text = content or "پیامی ثبت نشده است."
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )


async def send_post_start_message(
    bot: Bot,
    chat_id: int,
    is_new_user: bool = False,
) -> None:
    try:
        config = await get_start_message_config()
        if not config.get("enabled"):
            return
        msg_data = config.get("message_data")
        if not msg_data:
            return

        target = config.get("target", "all")
        if target == "new_only" and not is_new_user:
            return

        await send_start_payload(bot, chat_id, msg_data)
    except Exception as e:
        logger.warning(
            "Failed to send post start message to %s: %s", chat_id, e
        )

