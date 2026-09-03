from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from db.models import is_owner, is_user_banned

logger = logging.getLogger(__name__)

BANNED_ALERT_TEXT = (
    "⛔️ <b>حساب کاربری شما توسط مدیریت مسدود شده است.</b>\n\n"
    "امکان استفاده از خدمات و دستورات ربات برای شما غیرفعال می‌باشد."
)


class BannedCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if not user_id:
            return await handler(event, data)

        if is_owner(user_id):
            return await handler(event, data)

        banned = await is_user_banned(user_id)
        if banned:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer(
                        "⛔️ حساب کاربری شما مسدود شده است.", show_alert=True
                    )
                except Exception:
                    pass
            elif isinstance(event, Message):
                try:
                    await event.answer(BANNED_ALERT_TEXT, parse_mode="HTML")
                except Exception:
                    pass
            return None

        return await handler(event, data)
