from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from db.database import get_db
from config import INVOICE_EXPIRY_MINUTES


async def get_user(tg_id: int) -> dict[str, Any] | None:
    db = await get_db()
    row = await db.execute_fetchall("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    if row:
        return dict(row[0])
    return None


async def ensure_user(
    tg_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO users (tg_id, username, full_name)
           VALUES (?, ?, ?)
           ON CONFLICT(tg_id) DO UPDATE SET
               username = COALESCE(EXCLUDED.username, users.username),
               full_name = COALESCE(EXCLUDED.full_name, users.full_name)""",
        (tg_id, username, full_name),
    )
    await db.execute(
        "INSERT OR IGNORE INTO wallets (tg_id, balance) VALUES (?, 0)",
        (tg_id,),
    )
    await db.commit()


async def create_user(
    tg_id: int,
    username: str | None,
    full_name: str | None,
    referrer_id: int | None = None,
) -> None:
    await ensure_user(tg_id, username, full_name)
    if referrer_id is not None:
        await ensure_user(referrer_id)
        db = await get_db()
        await db.execute(
            "UPDATE users SET referrer_id = ? WHERE tg_id = ? AND referrer_id IS NULL",
            (referrer_id, tg_id),
        )
        await db.commit()


async def set_test_used(tg_id: int) -> None:
    await ensure_user(tg_id)
    db = await get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE users SET test_used = 1, last_test_at = ? WHERE tg_id = ?",
        (now_iso, tg_id),
    )
    await db.commit()


async def can_get_test_sub(tg_id: int) -> tuple[bool, int, int]:
    from services.test_sub_config import get_test_sub_config

    await ensure_user(tg_id)
    test_config = await get_test_sub_config()
    cooldown_days = test_config["cooldown_days"]

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT test_used, last_test_at FROM users WHERE tg_id = ?", (tg_id,)
    )

    if not rows:
        return True, 0, 0

    user = rows[0]
    last_test_at_str = user["last_test_at"]
    test_used = bool(user["test_used"])

    if not test_used and not last_test_at_str:
        return True, 0, 0

    now = datetime.now(timezone.utc)

    if last_test_at_str:
        try:
            last_test_at = datetime.fromisoformat(last_test_at_str)
            if last_test_at.tzinfo is None:
                last_test_at = last_test_at.replace(tzinfo=timezone.utc)
            elapsed_seconds = (now - last_test_at).total_seconds()
            cooldown_seconds = cooldown_days * 86400

            if elapsed_seconds < cooldown_seconds:
                remaining_sec = cooldown_seconds - elapsed_seconds
                days = int(remaining_sec // 86400)
                hours = int((remaining_sec % 86400) // 3600)
                return False, days, hours
            else:
                return True, 0, 0
        except Exception:
            return False, cooldown_days, 0

    return False, cooldown_days, 0


async def is_test_used(tg_id: int) -> bool:
    can_claim, _, _ = await can_get_test_sub(tg_id)
    return not can_claim


async def reset_all_test_subs() -> int:
    db = await get_db()
    cursor = await db.execute("UPDATE users SET test_used = 0, last_test_at = NULL")
    await db.commit()
    return cursor.rowcount


async def get_balance(tg_id: int) -> int:
    await ensure_user(tg_id)
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT balance FROM wallets WHERE tg_id = ?", (tg_id,)
    )
    if rows:
        return rows[0]["balance"]
    return 0


async def credit_wallet(tg_id: int, amount: int) -> int:
    await ensure_user(tg_id)
    db = await get_db()
    await db.execute(
        "UPDATE wallets SET balance = balance + ? WHERE tg_id = ?",
        (amount, tg_id),
    )
    await db.commit()
    return await get_balance(tg_id)


async def debit_wallet(tg_id: int, amount: int) -> int:
    await ensure_user(tg_id)
    balance = await get_balance(tg_id)
    if balance < amount:
        raise ValueError("Insufficient balance")
    db = await get_db()
    await db.execute(
        "UPDATE wallets SET balance = balance - ? WHERE tg_id = ?",
        (amount, tg_id),
    )
    await db.commit()
    return await get_balance(tg_id)


async def create_invoice(
    tg_id: int,
    amount: int,
    duration_days: int,
    data_gb: int,
    users_count: int = 1,
    target_email: str | None = None,
    payment_method: str = "card",
    discount_code: str | None = None,
    original_amount: int | None = None,
) -> dict[str, Any]:
    await ensure_user(tg_id)
    db = await get_db()
    invoice_id = uuid4().hex[:12].upper()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=INVOICE_EXPIRY_MINUTES)

    await db.execute(
        """INSERT INTO invoices (id, tg_id, amount, duration_days, data_gb, users_count, target_email, payment_method, discount_code, original_amount, status, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            invoice_id,
            tg_id,
            amount,
            duration_days,
            data_gb,
            users_count,
            target_email,
            payment_method,
            discount_code,
            original_amount if original_amount is not None else amount,
            now.isoformat(),
            expires_at.isoformat(),
        ),
    )
    await db.commit()
    return {
        "id": invoice_id,
        "tg_id": tg_id,
        "amount": amount,
        "duration_days": duration_days,
        "data_gb": data_gb,
        "users_count": users_count,
        "target_email": target_email,
        "payment_method": payment_method,
        "discount_code": discount_code,
        "original_amount": original_amount if original_amount is not None else amount,
        "status": "pending",
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


async def get_invoice(invoice_id: str) -> dict[str, Any] | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
    )
    if rows:
        return dict(rows[0])
    return None


async def update_invoice_status(invoice_id: str, status: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id)
    )
    await db.commit()


async def set_invoice_receipt(
    invoice_id: str,
    file_id: str | None = None,
    text: str | None = None,
) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE invoices SET receipt_file_id = ?, receipt_text = ? WHERE id = ?",
        (file_id, text, invoice_id),
    )
    await db.commit()


async def set_invoice_message_id(invoice_id: str, message_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE invoices SET message_id = ? WHERE id = ?",
        (message_id, invoice_id),
    )
    await db.commit()


async def get_pending_invoices_by_user(tg_id: int) -> list[dict[str, Any]]:
    await ensure_user(tg_id)
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM invoices WHERE tg_id = ? AND status = 'pending' ORDER BY created_at DESC",
        (tg_id,),
    )
    return [dict(r) for r in rows]


async def get_user_invoices_paginated(
    tg_id: int, offset: int = 0, limit: int = 5
) -> tuple[list[dict[str, Any]], int]:
    await ensure_user(tg_id)
    db = await get_db()
    await expire_old_invoices()

    count_rows = await db.execute_fetchall(
        "SELECT COUNT(*) as cnt FROM invoices WHERE tg_id = ?", (tg_id,)
    )
    total_count = count_rows[0]["cnt"] if count_rows else 0

    rows = await db.execute_fetchall(
        "SELECT * FROM invoices WHERE tg_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (tg_id, limit, offset),
    )
    return [dict(r) for r in rows], total_count


async def get_user_purchase_summary(tg_id: int) -> dict[str, Any]:
    await ensure_user(tg_id)
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total_spent FROM invoices WHERE tg_id = ? AND status = 'approved'",
        (tg_id,),
    )
    if rows:
        return {
            "approved_count": rows[0]["cnt"],
            "total_spent": rows[0]["total_spent"],
        }
    return {"approved_count": 0, "total_spent": 0}


async def expire_old_invoices() -> int:
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        "UPDATE invoices SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?",
        (now,),
    )
    await db.commit()
    return cursor.rowcount


async def create_referral(referrer_tg_id: int, referred_tg_id: int) -> None:
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO referrals (referrer_tg_id, referred_tg_id) VALUES (?, ?)",
        (referrer_tg_id, referred_tg_id),
    )
    await db.commit()


async def get_setting(key: str, default: str | None = None) -> str | None:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT value FROM settings WHERE key = ?", (key,))
    if rows:
        return rows[0]["value"]
    return default


async def set_setting(key: str, value: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )
    await db.commit()


async def get_referral_config() -> dict[str, Any]:
    enabled_val = await get_setting("referral_enabled", "1")
    percent_val = await get_setting("referral_commission_percent", "10")
    return {
        "enabled": enabled_val == "1",
        "percent": int(percent_val) if percent_val and percent_val.isdigit() else 10,
    }


async def set_referral_config(
    enabled: bool | None = None, percent: int | None = None
) -> None:
    if enabled is not None:
        await set_setting("referral_enabled", "1" if enabled else "0")
    if percent is not None:
        await set_setting("referral_commission_percent", str(percent))


async def get_referral_stats(tg_id: int) -> dict[str, Any]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) as count FROM users WHERE referrer_id = ?", (tg_id,)
    )
    row = await cursor.fetchone()
    invited_count = row["count"] if row else 0

    return {
        "invited_count": invited_count,
    }


async def process_referral_commission(
    buyer_tg_id: int, amount: int, bot: Any | None = None
) -> int:
    config = await get_referral_config()
    if not config["enabled"] or amount <= 0:
        return 0

    buyer = await get_user(buyer_tg_id)
    if not buyer:
        return 0

    referrer_id = buyer.get("referrer_id")
    if not referrer_id or referrer_id == buyer_tg_id:
        return 0

    percent = config["percent"]
    commission = int(round(amount * percent / 100))
    if commission <= 0:
        return 0

    new_balance = await credit_wallet(referrer_id, commission)

    if bot:
        try:
            from utils.formatting import format_price, to_persian_digits

            notify_text = (
                f"🎉 <b>یِس! پول پورسانت واریز شد!</b>\n\n"
                f"💸 <b>مبلغ پاداش ({to_persian_digits(percent)}٪):</b> +{format_price(commission)}\n"
                f"💰 <b>موجودی جدید کیف پول:</b> {format_price(new_balance)}\n\n"
                f"🔥 <i>دوستت ازت خرید کرد و تو پول گرفتی! همینجوری ادامه بده!</i>"
            )
            await bot.send_message(
                chat_id=referrer_id, text=notify_text, parse_mode="HTML"
            )
        except Exception:
            pass

    return commission


async def get_active_inbound_ids() -> list[int]:
    from config import INBOUND_IDS

    val = await get_setting("active_inbound_ids")
    if not val or not val.strip():
        return INBOUND_IDS

    try:
        ids = [int(x.strip()) for x in val.split(",") if x.strip()]
        return ids if ids else INBOUND_IDS
    except ValueError:
        return INBOUND_IDS


async def set_active_inbound_ids(inbound_ids: list[int]) -> None:
    str_val = ",".join(str(i) for i in sorted(set(inbound_ids)))
    await set_setting("active_inbound_ids", str_val)


async def toggle_assigned_inbound_id(inbound_id: int) -> list[int]:
    current_ids = set(await get_active_inbound_ids())
    if inbound_id in current_ids:
        current_ids.remove(inbound_id)
    else:
        current_ids.add(inbound_id)

    new_list = sorted(current_ids)
    await set_active_inbound_ids(new_list)
    return new_list


async def get_active_client_group() -> str:
    return await get_setting("active_client_group", "") or ""


async def set_active_client_group(group_name: str) -> None:
    await set_setting("active_client_group", group_name)


async def search_users(query: str) -> list[dict[str, Any]]:
    clean_q = query.strip().lstrip("@").lower()
    db = await get_db()

    rows = []
    if clean_q.isdigit():
        tg_id = int(clean_q)
        r = await db.execute_fetchall(
            "SELECT * FROM users WHERE tg_id = ? OR username LIKE ? OR full_name LIKE ?",
            (tg_id, f"%{clean_q}%", f"%{clean_q}%"),
        )
        rows.extend(r)
    else:
        r = await db.execute_fetchall(
            "SELECT * FROM users WHERE username LIKE ? OR full_name LIKE ?",
            (f"%{clean_q}%", f"%{clean_q}%"),
        )
        rows.extend(r)

    seen = set()
    unique_users = []
    for row in rows:
        d = dict(row)
        if d["tg_id"] not in seen:
            seen.add(d["tg_id"])
            unique_users.append(d)

    return unique_users


async def get_card_config() -> dict[str, str]:
    card_number = await get_setting("card_number")
    card_holder = await get_setting("card_holder")

    return {
        "card_number": card_number,
        "card_holder": card_holder,
    }


async def set_card_config(
    card_number: str | None = None, card_holder: str | None = None
) -> None:
    if card_number is not None:
        await set_setting("card_number", card_number.strip())
    if card_holder is not None:
        await set_setting("card_holder", card_holder.strip())


async def get_all_user_ids() -> list[int]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT DISTINCT tg_id FROM users")
    return [r["tg_id"] for r in rows]


async def get_alert_config() -> dict[str, float | int]:
    gb_str = await get_setting("alert_min_gb", "2.0") or "2.0"
    days_str = await get_setting("alert_min_days", "3") or "3"
    del_days_str = await get_setting("auto_delete_expired_days", "3") or "3"
    try:
        min_gb = float(gb_str)
    except ValueError:
        min_gb = 2.0
    try:
        min_days = int(days_str)
    except ValueError:
        min_days = 3
    try:
        auto_delete_days = int(del_days_str)
    except ValueError:
        auto_delete_days = 3
    return {
        "min_gb": min_gb,
        "min_days": min_days,
        "auto_delete_days": auto_delete_days,
    }


async def set_alert_config(
    min_gb: float | None = None,
    min_days: int | None = None,
    auto_delete_days: int | None = None,
) -> None:
    if min_gb is not None:
        await set_setting("alert_min_gb", str(min_gb))
    if min_days is not None:
        await set_setting("alert_min_days", str(min_days))
    if auto_delete_days is not None:
        await set_setting("auto_delete_expired_days", str(auto_delete_days))


async def has_notified_alert(email: str, alert_type: str) -> bool:
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT 1 FROM notified_alerts WHERE email = ? AND alert_type = ?",
        (email, alert_type),
    )
    return row is not None


async def record_notified_alert(email: str, alert_type: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO notified_alerts (email, alert_type) VALUES (?, ?)",
        (email, alert_type),
    )
    await db.commit()


async def clear_notified_alerts(email: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM notified_alerts WHERE email = ?", (email,))
    await db.commit()
