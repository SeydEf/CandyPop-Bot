"""
Admin router for broadcasting announcements to all bot users.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_CHAT_ID
from db.models import get_all_user_ids
from utils.formatting import to_persian_digits
from utils.helpers import safe_edit_text

logger = logging.getLogger(__name__)
router = Router(name="admin_broadcast")


def _is_admin(event: types.CallbackQuery | types.Message) -> bool:
    return event.from_user is not None and event.from_user.id == ADMIN_CHAT_ID


class AdminBroadcastStates(StatesGroup):
    waiting_broadcast_message = State()
    waiting_broadcast_confirm = State()


@router.message(Command("send_all"))
@router.callback_query(F.data == "admin_broadcast_start")
async def admin_broadcast_start(
    event: types.Message | types.CallbackQuery, state: FSMContext
) -> None:
    if not _is_admin(event):
        return

    await state.clear()
    await state.set_state(AdminBroadcastStates.waiting_broadcast_message)

    text = (
        "📢 <b>ارسال پیام همگانی (اطلاعیه به تمام کاربران)</b>\n\n"
        "لطفاً پیام اطلاعیه خود را ارسال نمایید.\n"
        "• می‌توانید متن ساده، متن دارای استایل HTML، عکس با کپشن یا ویدیو بفرستید.\n\n"
        "<i>جهت انصراف، دستور /cancel را ارسال کنید.</i>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data="admin_price_main")]
        ]
    )

    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await safe_edit_text(
            event.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await event.answer()


@router.message(AdminBroadcastStates.waiting_broadcast_message)
async def admin_broadcast_message_received(
    message: types.Message, state: FSMContext
) -> None:
    if not _is_admin(message):
        return

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ ارسال همگانی لغو گردید.")
        return

    user_ids = await get_all_user_ids()
    target_count = len(user_ids)

    await state.update_data(
        broadcast_msg_id=message.message_id,
        broadcast_chat_id=message.chat.id,
        target_user_ids=user_ids,
    )
    await state.set_state(AdminBroadcastStates.waiting_broadcast_confirm)

    preview_intro = (
        f"📢 <b>پیش‌نمایش اطلاعیه همگانی</b>\n\n"
        f"👥 <b>تعداد دریافت‌کنندگان:</b> {to_persian_digits(target_count)} کاربر\n\n"
        f"👇 پیام ارسال‌شده در زیر عیناً کپی شد. آیا از ارسال همگانی اطمینان دارید؟"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 تایید نهایی و ارسال به همه کاربران",
                    callback_data="admin_broadcast_execute",
                )
            ],
            [InlineKeyboardButton(text="❌ انصراف", callback_data="admin_price_main")],
        ]
    )

    await message.answer(preview_intro, parse_mode="HTML")

    await message.bot.copy_message(
        chat_id=message.chat.id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        reply_markup=keyboard,
    )


@router.callback_query(
    F.data == "admin_broadcast_execute", AdminBroadcastStates.waiting_broadcast_confirm
)
async def admin_broadcast_execute(
    callback: types.CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not _is_admin(callback):
        return

    data = await state.get_data()
    msg_id = data.get("broadcast_msg_id")
    chat_id = data.get("broadcast_chat_id")
    user_ids: list[int] = data.get("target_user_ids") or []

    if not msg_id or not chat_id or not user_ids:
        await state.clear()
        await callback.answer("❌ اطلاعات اطلاعیه یافت نشد.", show_alert=True)
        return

    await state.clear()
    total_users = len(user_ids)

    status_msg = await callback.message.answer(
        f"⏳ <b>درحال ارسال همگانی اطلاعیه...</b>\n"
        f"📊 پیشرفت: ۰ از {to_persian_digits(total_users)} کاربر",
        parse_mode="HTML",
    )
    await callback.answer()

    success_count = 0
    fail_count = 0

    for idx, u_id in enumerate(user_ids, 1):
        try:
            await bot.copy_message(
                chat_id=u_id,
                from_chat_id=chat_id,
                message_id=msg_id,
            )
            success_count += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            fail_count += 1
        except Exception as e:
            logger.warning("Broadcast failed to user %s: %s", u_id, e)
            fail_count += 1

        if idx % 50 == 0 or idx == total_users:
            try:
                await status_msg.edit_text(
                    f"⏳ <b>درحال ارسال همگانی اطلاعیه...</b>\n"
                    f"📊 پیشرفت: {to_persian_digits(idx)} از {to_persian_digits(total_users)} کاربر\n"
                    f"✅ موفق: {to_persian_digits(success_count)} | ❌ ناموفق: {to_persian_digits(fail_count)}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        await asyncio.sleep(0.04)

    report_text = (
        f"🎉 <b>ارسال همگانی اطلاعیه با موفقیت به پایان رسید!</b>\n\n"
        f"👥 <b>کل کاربران هدف:</b> {to_persian_digits(total_users)} کاربر\n"
        f"✅ <b>تحویل موفق:</b> {to_persian_digits(success_count)} کاربر\n"
        f"❌ <b>ناموفق / بلاک‌شده:</b> {to_persian_digits(fail_count)} کاربر"
    )

    await status_msg.edit_text(report_text, parse_mode="HTML")
