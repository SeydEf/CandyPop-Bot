from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command

from config import BOT_NAME
from keyboards.guide_kb import (
    guide_apps_keyboard,
    guide_detail_keyboard,
    guide_os_keyboard,
)
from keyboards.reply_kb import BTN_GUIDE
from services.guide_data import get_client_info, get_os_info
from utils.helpers import safe_edit_text

router = Router()


def build_os_menu_text() -> str:
    return (
        f"📖 <b>راهنمای تعاملی اتصال به سرویس‌های {BOT_NAME}</b>\n\n"
        "لطفاً سیستم‌عامل دستگاه خود را از گزینه‌های زیر انتخاب نمایید تا لیست برنامه‌های مناسب و راهنمای راه‌اندازی برای شما نمایش داده شود:"
    )


def build_app_list_text(os_key: str) -> str:
    os_info = get_os_info(os_key)
    if not os_info:
        return build_os_menu_text()

    title = os_info.get("title", os_key)
    description = os_info.get("description", "")
    return (
        f"📱 <b>راهنمای اتصال | {title}</b>\n\n"
        f"{description}\n\n"
        "👇 <i>برای مشاهده آموزش گام‌به‌گام و دانلود آخرین نسخه، روی برنامه مورد نظر کلیک کنید:</i>"
    )


def build_app_detail_text(os_key: str, app_key: str) -> str:
    os_info = get_os_info(os_key)
    app_info = get_client_info(app_key, os_key)

    if not app_info:
        return build_app_list_text(os_key)

    badge = os_info.get("badge", "") if os_info else ""
    icon = app_info.get("icon", "📱")
    name = app_info.get("name", app_key)
    desc = app_info.get("desc", "")
    steps = app_info.get("steps", [])
    tip = app_info.get("tip")

    text_lines = [
        f"{icon} <b>آموزش اتصال به برنامه {name} ({badge})</b>",
        "",
        f"📝 <b>درباره برنامه:</b>\n{desc}",
        "",
        "📋 <b>مراحل راه‌اندازی و اتصال:</b>",
    ]

    for idx, step in enumerate(steps, 1):
        text_lines.append(f"{idx}️⃣ {step}")

    if tip:
        text_lines.append("")
        text_lines.append(f"💡 <b>نکته کاربردی:</b> <i>{tip}</i>")

    return "\n".join(text_lines)


@router.message(Command("help"))
@router.message(Command("guide"))
@router.message(F.text == BTN_GUIDE)
async def cmd_guide(message: types.Message) -> None:
    await message.answer(
        build_os_menu_text(),
        reply_markup=guide_os_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("guide_os:"))
async def cb_guide_os(callback: types.CallbackQuery) -> None:
    await callback.answer()
    if not callback.message or not callback.data:
        return

    os_key = callback.data.split(":", 1)[1]
    text = build_app_list_text(os_key)
    kb = guide_apps_keyboard(os_key)

    await safe_edit_text(
        callback.message,
        text=text,
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("guide_app:"))
async def cb_guide_app(callback: types.CallbackQuery) -> None:
    await callback.answer()
    if not callback.message or not callback.data:
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        return

    os_key = parts[1]
    app_key = parts[2]

    text = build_app_detail_text(os_key, app_key)
    kb = guide_detail_keyboard(os_key, app_key)

    await safe_edit_text(
        callback.message,
        text=text,
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "guide_back_os")
async def cb_guide_back_os(callback: types.CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return

    await safe_edit_text(
        callback.message,
        text=build_os_menu_text(),
        reply_markup=guide_os_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("guide_back_app:"))
async def cb_guide_back_app(callback: types.CallbackQuery) -> None:
    await callback.answer()
    if not callback.message or not callback.data:
        return

    os_key = callback.data.split(":", 1)[1]
    text = build_app_list_text(os_key)
    kb = guide_apps_keyboard(os_key)

    await safe_edit_text(
        callback.message,
        text=text,
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
