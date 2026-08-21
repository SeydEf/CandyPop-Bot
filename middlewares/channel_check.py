from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from config import CHANNEL_ID, CHANNEL_LINK

logger = logging.getLogger(__name__)

MEMBERSHIP_STATUSES = {"member", "administrator", "creator"}


async def is_member(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in MEMBERSHIP_STATUSES
    except Exception:
        logger.exception("Failed to check channel membership for user %d", user_id)
        return False


def _join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 عضویت در کانال",
                    url=CHANNEL_LINK,
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ عضو شدم",
                    callback_data="check_membership",
                ),
            ],
        ]
    )


_JOIN_TEXT = (
    "⚠️ <b>برای استفاده از ربات، ابتدا باید در کانال ما عضو شوید.</b>\n\n"
    "پس از عضویت، دکمه «✅ عضو شدم» را بزنید."
)


class ChannelCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot: Bot = data["bot"]

        user_id: int | None = None
        user_obj = None
        if isinstance(event, Message):
            if event.from_user:
                user_id = event.from_user.id
                user_obj = event.from_user
        elif isinstance(event, CallbackQuery):
            if event.from_user:
                user_id = event.from_user.id
                user_obj = event.from_user

        if user_id is None:
            return await handler(event, data)

        from db.models import ensure_user

        username = user_obj.username if user_obj else None
        full_name = user_obj.full_name if user_obj else None
        await ensure_user(user_id, username, full_name)

        if isinstance(event, CallbackQuery) and event.data == "check_membership":
            if await is_member(bot, user_id):
                await event.answer("✅ عضویت شما تأیید شد!", show_alert=False)
                if event.message:
                    try:
                        await event.message.delete()
                    except Exception:
                        pass
                from handlers.start import send_welcome

                await send_welcome(event.message, data)
                return
            else:
                await event.answer(
                    "❌ هنوز عضو کانال نشده‌اید. لطفاً ابتدا عضو شوید.",
                    show_alert=True,
                )
                return

        if not await is_member(bot, user_id):
            if isinstance(event, Message):
                await event.answer(
                    _JOIN_TEXT,
                    reply_markup=_join_keyboard(),
                    parse_mode="HTML",
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "❌ ابتدا در کانال عضو شوید.",
                    show_alert=True,
                )
            return

        return await handler(event, data)
