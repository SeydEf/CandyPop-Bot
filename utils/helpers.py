from __future__ import annotations

import io
import uuid

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
import qrcode


def generate_qr(data: str) -> io.BytesIO:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_email(tg_id: int, username: str | None = None, test: bool = False) -> str:
    short = uuid.uuid4().hex[:6]
    name = username or "user"
    name = "".join(c if c.isalnum() or c == "_" else "" for c in name)
    if test:
        return f"{name}_{short}_test"
    else:
        return f"{name}_{short}"


def gb_to_bytes(gb: int | float) -> int:
    return int(round(gb * 1024 * 1024 * 1024))


async def safe_edit_text(
    message: types.Message,
    text: str,
    reply_markup: types.InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool | None = None,
) -> bool:
    try:
        if message.photo:
            await message.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        else:
            await message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return False
        raise e
