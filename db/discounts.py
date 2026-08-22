from __future__ import annotations

import random
import string
from typing import Any

from db.database import get_db


def generate_random_code(length: int = 8) -> str:
    chars = string.ascii_uppercase + string.digits
    chars = chars.replace("0", "").replace("O", "").replace("1", "").replace("I", "")
    return "".join(random.choice(chars) for _ in range(length))


async def create_discount_code(
    code: str,
    discount_percent: int,
    max_uses: int = -1,
) -> bool:
    clean_code = code.strip().upper()
    if not clean_code or discount_percent < 1 or discount_percent > 100:
        return False

    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO discount_codes (code, discount_percent, max_uses, used_count, is_active)
            VALUES (?, ?, ?, 0, 1)
            """,
            (clean_code, discount_percent, max_uses),
        )
        await db.commit()
        return True
    except Exception:
        return False


async def get_discount_code(code: str) -> dict[str, Any] | None:
    clean_code = code.strip().upper()
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM discount_codes WHERE code = ?", (clean_code,)
    )
    row = await cursor.fetchone()
    if row:
        return dict(row)
    return None


async def list_discount_codes() -> list[dict[str, Any]]:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM discount_codes ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def update_discount_code(
    code: str,
    discount_percent: int | None = None,
    max_uses: int | None = None,
    is_active: bool | None = None,
) -> bool:
    clean_code = code.strip().upper()
    current = await get_discount_code(clean_code)
    if not current:
        return False

    new_percent = (
        discount_percent
        if discount_percent is not None
        else current["discount_percent"]
    )
    new_max = max_uses if max_uses is not None else current["max_uses"]
    new_active = int(is_active) if is_active is not None else current["is_active"]

    db = await get_db()
    await db.execute(
        """
        UPDATE discount_codes
        SET discount_percent = ?, max_uses = ?, is_active = ?
        WHERE code = ?
        """,
        (new_percent, new_max, new_active, clean_code),
    )
    await db.commit()
    return True


async def delete_discount_code(code: str) -> bool:
    clean_code = code.strip().upper()
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM discount_codes WHERE code = ?", (clean_code,)
    )
    await db.commit()
    return cursor.rowcount > 0


async def validate_discount_code(code: str) -> tuple[bool, str, dict[str, Any] | None]:
    clean_code = code.strip().upper()
    if not clean_code:
        return False, "❌ کد تخفیف نمی‌تواند خالی باشد.", None

    dc = await get_discount_code(clean_code)
    if not dc:
        return False, "❌ کد تخفیف وارد شده معتبر نیست.", None

    if not dc.get("is_active"):
        return False, "❌ این کد تخفیف غیرفعال شده است.", None

    max_uses = dc.get("max_uses", -1)
    used_count = dc.get("used_count", 0)

    if max_uses is not None and max_uses > 0 and used_count >= max_uses:
        return False, "❌ ظرفیت استفاده از این کد تخفیف به پایان رسیده است.", None

    return True, "✅ کد تخفیف معتبر است.", dc


async def increment_discount_usage(code: str) -> None:
    clean_code = code.strip().upper()
    db = await get_db()
    await db.execute(
        "UPDATE discount_codes SET used_count = used_count + 1 WHERE code = ?",
        (clean_code,),
    )
    await db.commit()
