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

logger = logging.getLogger(__name__)

MEMBERSHIP_STATUSES = {"member", "administrator", "creator"}


async def is_member(
    bot: Bot, user_id: int, channel_id: str | int | None = None
) -> bool:
    if not channel_id:
        from config import CHANNEL_ID

        channel_id = CHANNEL_ID
    if not channel_id:
        return True
    try:
        target_chat_id: str | int = channel_id
        if isinstance(channel_id, str) and (
            channel_id.startswith("-100")
            or channel_id.isdigit()
            or (channel_id.startswith("-") and channel_id[1:].isdigit())
        ):
            target_chat_id = int(channel_id)
        member = await bot.get_chat_member(chat_id=target_chat_id, user_id=user_id)
        return member.status in MEMBERSHIP_STATUSES
    except Exception:
        logger.exception(
            "Failed to check channel membership for user %d in %s",
            user_id,
            channel_id,
        )
        return False


def _join_keyboard(channel_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 عضویت در کانال",
                    url=channel_link,
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
    "⚠️ <b>برای استفاده از امکانات ربات، ابتدا باید در کانال ما عضو شوید.</b>\n\n"
    "پس از عضویت در کانال، روی دکمه «✅ عضو شدم» کلیک کنید."
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

        from db.models import (
            ensure_user,
            get_channel_lock_config,
            is_admin,
            is_owner,
        )

        username = user_obj.username if user_obj else None
        full_name = user_obj.full_name if user_obj else None
        await ensure_user(user_id, username, full_name)

        if is_owner(user_id) or await is_admin(user_id):
            return await handler(event, data)

        lock_config = await get_channel_lock_config()
        if not lock_config["enabled"] or not lock_config["channel_id"]:
            return await handler(event, data)

        channel_id = lock_config["channel_id"]
        channel_link = lock_config["channel_link"]
        mode = lock_config["mode"]

        if isinstance(event, CallbackQuery) and event.data == "check_membership":
            if await is_member(bot, user_id, channel_id):
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

        is_user_member = await is_member(bot, user_id, channel_id)
        if is_user_member:
            return await handler(event, data)

        if mode == "on_action":
            if (
                isinstance(event, Message)
                and event.text
                and event.text.strip().startswith("/start")
            ):
                return await handler(event, data)

            if isinstance(event, Message):
                await event.answer(
                    _JOIN_TEXT,
                    reply_markup=_join_keyboard(channel_link),
                    parse_mode="HTML",
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "⚠️ برای دسترسی به امکانات ربات، ابتدا باید در کانال عضو شوید.",
                    show_alert=True,
                )
            return

        if isinstance(event, Message):
            await event.answer(
                _JOIN_TEXT,
                reply_markup=_join_keyboard(channel_link),
                parse_mode="HTML",
            )
        elif isinstance(event, CallbackQuery):
            await event.answer(
                "❌ ابتدا در کانال عضو شوید.",
                show_alert=True,
            )
        return
