from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from config import INVOICE_EXPIRY_MINUTES
from db.database import get_db

logger = logging.getLogger(__name__)


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


async def reset_user_test_sub(tg_id: int) -> bool:
    """Reset test sub usage and cooldown for a single specific user."""
    db = await get_db()
    cursor = await db.execute(
        "UPDATE users SET test_used = 0, last_test_at = NULL WHERE tg_id = ?",
        (tg_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


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


async def get_users_paginated(
    page: int = 0, page_size: int = 5
) -> tuple[list[dict[str, Any]], int, int]:
    import math

    db = await get_db()
    async with db.execute("SELECT COUNT(*) FROM users") as c:
        row = await c.fetchone()
        total_users = row[0] if row else 0

    total_pages = max(1, math.ceil(total_users / page_size)) if total_users > 0 else 1
    safe_page = max(0, min(page, total_pages - 1))
    offset = safe_page * page_size

    rows = await db.execute_fetchall(
        """
        SELECT u.tg_id, u.username, u.full_name, u.joined_at, u.test_used, COALESCE(w.balance, 0) as balance
        FROM users u
        LEFT JOIN wallets w ON u.tg_id = w.tg_id
        ORDER BY u.joined_at DESC
        LIMIT ? OFFSET ?
        """,
        (page_size, offset),
    )

    return [dict(r) for r in rows], total_users, total_pages


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


async def get_alert_config() -> dict[str, Any]:
    gb_str = await get_setting("alert_min_gb", "2.0") or "2.0"
    days_str = await get_setting("alert_min_days", "3") or "3"
    del_days_str = await get_setting("auto_delete_expired_days", "3") or "3"
    interval_str = await get_setting("alert_poll_interval_minutes", "30") or "30"
    low_gb_en_str = await get_setting("alert_low_gb_enabled", "1") or "1"
    expiring_days_en_str = await get_setting("alert_expiring_days_enabled", "1") or "1"
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
    try:
        interval_minutes = int(interval_str)
    except ValueError:
        interval_minutes = 30
    low_gb_enabled = low_gb_en_str == "1"
    expiring_days_enabled = expiring_days_en_str == "1"
    return {
        "min_gb": min_gb,
        "min_days": min_days,
        "auto_delete_days": auto_delete_days,
        "interval_minutes": interval_minutes,
        "low_gb_enabled": low_gb_enabled,
        "expiring_days_enabled": expiring_days_enabled,
    }


async def set_alert_config(
    min_gb: float | None = None,
    min_days: int | None = None,
    auto_delete_days: int | None = None,
    interval_minutes: int | None = None,
    low_gb_enabled: bool | None = None,
    expiring_days_enabled: bool | None = None,
) -> None:
    if min_gb is not None:
        await set_setting("alert_min_gb", str(min_gb))
    if min_days is not None:
        await set_setting("alert_min_days", str(min_days))
    if auto_delete_days is not None:
        await set_setting("auto_delete_expired_days", str(auto_delete_days))
    if interval_minutes is not None:
        await set_setting("alert_poll_interval_minutes", str(interval_minutes))
    if low_gb_enabled is not None:
        await set_setting("alert_low_gb_enabled", "1" if low_gb_enabled else "0")
    if expiring_days_enabled is not None:
        await set_setting(
            "alert_expiring_days_enabled", "1" if expiring_days_enabled else "0"
        )


async def get_shop_status() -> dict[str, bool]:
    purchases_str = await get_setting("shop_purchases_enabled", "1") or "1"
    renewals_str = await get_setting("shop_renewals_enabled", "1") or "1"
    return {
        "purchases_enabled": purchases_str == "1",
        "renewals_enabled": renewals_str == "1",
    }


async def set_shop_status(
    purchases_enabled: bool | None = None,
    renewals_enabled: bool | None = None,
) -> None:
    if purchases_enabled is not None:
        await set_setting("shop_purchases_enabled", "1" if purchases_enabled else "0")
    if renewals_enabled is not None:
        await set_setting("shop_renewals_enabled", "1" if renewals_enabled else "0")


async def has_notified_alert(email: str, alert_type: str) -> bool:
    db = await get_db()
    async with db.execute(
        "SELECT 1 FROM notified_alerts WHERE email = ? AND alert_type = ?",
        (email, alert_type),
    ) as cursor:
        row = await cursor.fetchone()
        return row is not None


async def get_all_notified_alerts_set() -> set[tuple[str, str]]:
    db = await get_db()
    async with db.execute("SELECT email, alert_type FROM notified_alerts") as cursor:
        rows = await cursor.fetchall()
        return {(r["email"], r["alert_type"]) for r in rows}


async def resolve_client_tg_id(client: dict[str, Any]) -> int:
    tg_id = client.get("tgId")
    if tg_id and isinstance(tg_id, int) and tg_id > 0:
        return tg_id

    try:
        tg_id_int = int(tg_id)
        if tg_id_int > 0:
            return tg_id_int
    except (TypeError, ValueError):
        pass

    email = client.get("email", "")
    if not email:
        return 0

    db = await get_db()
    async with db.execute(
        "SELECT tg_id FROM invoices WHERE target_email = ? ORDER BY created_at DESC LIMIT 1",
        (email,),
    ) as cursor:
        row = await cursor.fetchone()
        if row:
            return row["tg_id"]

    return 0


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


async def get_ip_violation(email: str) -> dict[str, Any] | None:
    db = await get_db()
    async with db.execute(
        "SELECT email, violation_count, total_incidents, last_violated_at, suspended FROM ip_violations WHERE email = ?",
        (email,),
    ) as cursor:
        row = await cursor.fetchone()
        if row:
            return dict(row)
    return None


async def get_all_ip_violations_dict() -> dict[str, dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT email, violation_count, total_incidents, last_violated_at, suspended FROM ip_violations"
    ) as cursor:
        rows = await cursor.fetchall()
        return {row["email"]: dict(row) for row in rows}


async def record_ip_violation(email: str) -> tuple[int, int]:
    db = await get_db()
    rec = await get_ip_violation(email)
    if not rec:
        current_strikes = 1
        total_incidents = 0
        await db.execute(
            "INSERT INTO ip_violations (email, violation_count, total_incidents, suspended) VALUES (?, 1, 0, 0)",
            (email,),
        )
    else:
        current_strikes = rec["violation_count"] + 1
        total_incidents = rec["total_incidents"]
        if current_strikes >= 3:
            total_incidents += 1
        await db.execute(
            "UPDATE ip_violations SET violation_count = ?, total_incidents = ?, last_violated_at = datetime('now') WHERE email = ?",
            (current_strikes, total_incidents, email),
        )
    await db.commit()
    return current_strikes, total_incidents


async def set_ip_suspended(email: str, suspended: bool) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE ip_violations SET suspended = ? WHERE email = ?",
        (1 if suspended else 0, email),
    )
    await db.commit()


async def reset_ip_violations(email: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE ip_violations SET violation_count = 0, suspended = 0 WHERE email = ?",
        (email,),
    )
    await db.commit()


async def get_ip_checker_config() -> dict[str, Any]:
    enabled_str = await get_setting("ip_checker_enabled", "1") or "1"
    interval_str = await get_setting("ip_checker_interval_minutes", "5") or "5"
    try:
        interval_minutes = int(interval_str)
    except ValueError:
        interval_minutes = 5
    return {
        "enabled": enabled_str == "1",
        "interval_minutes": interval_minutes,
    }


async def set_ip_checker_config(
    enabled: bool | None = None, interval_minutes: int | None = None
) -> None:
    if enabled is not None:
        await set_setting("ip_checker_enabled", "1" if enabled else "0")
    if interval_minutes is not None:
        await set_setting("ip_checker_interval_minutes", str(interval_minutes))


def is_owner(tg_id: int) -> bool:
    from config import ADMIN_CHAT_ID

    return tg_id > 0 and tg_id == ADMIN_CHAT_ID


async def is_admin(tg_id: int) -> bool:
    if is_owner(tg_id):
        return True
    if tg_id <= 0:
        return False

    db = await get_db()
    async with db.execute(
        "SELECT 1 FROM bot_admins WHERE tg_id = ?", (tg_id,)
    ) as cursor:
        row = await cursor.fetchone()
        return row is not None


async def get_all_admins() -> list[dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT tg_id, username, added_by, permissions, added_at FROM bot_admins ORDER BY added_at DESC"
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def add_admin(tg_id: int, username: str = "", added_by: int = 0) -> bool:
    if is_owner(tg_id):
        return False
    db = await get_db()
    try:
        default_perms_json = json.dumps(DEFAULT_ADMIN_PERMISSIONS)
        await db.execute(
            "INSERT OR REPLACE INTO bot_admins (tg_id, username, added_by, permissions) VALUES (?, ?, ?, ?)",
            (tg_id, username.lstrip("@"), added_by, default_perms_json),
        )
        await db.commit()
        return True
    except Exception as e:
        logger.error("Failed to add bot admin %d: %s", tg_id, e)
        return False


async def remove_admin(tg_id: int) -> bool:
    if is_owner(tg_id):
        return False
    db = await get_db()
    await db.execute("DELETE FROM bot_admins WHERE tg_id = ?", (tg_id,))
    await db.commit()
    return True


PERMISSION_TITLES: dict[str, str] = {
    "manage_subs": "جستجو و مدیریت اشتراک‌ها",
    "create_sub": "ساخت اشتراک سفارشی",
    "approve_invoices": "تأیید و رد پرداخت فاکتورها",
    "pricing": "قیمت‌گذاری و تغییر نرخ‌ها",
    "shop_status": "وضعیت فروش و تمدید",
    "discounts": "مدیریت کدهای تخفیف",
    "test_sub": "تنظیمات اشتراک تست",
    "bulk_gift": "اعمال هدیه همگانی",
    "alerts": "هشدارها و پایش IP",
    "card_config": "تنظیمات کارت بانکی",
    "inbounds": "مدیریت اینباندهای سرور",
    "referral": "تنظیمات زیرمجموعه‌گیری",
    "broadcast": "ارسال پیام همگانی",
    "reset_configs": "بازنشانی تنظیمات به پیش‌فرض",
    "stats": "مشاهده آمار و گزارشات ربات",
}

DEFAULT_ADMIN_PERMISSIONS: dict[str, bool] = {
    "manage_subs": True,
    "create_sub": True,
    "approve_invoices": True,
    "pricing": False,
    "shop_status": True,
    "discounts": True,
    "test_sub": True,
    "bulk_gift": True,
    "alerts": True,
    "card_config": False,
    "inbounds": False,
    "referral": True,
    "broadcast": True,
    "reset_configs": False,
    "stats": True,
}


async def get_admin_permissions(tg_id: int) -> dict[str, bool]:
    if is_owner(tg_id):
        return {k: True for k in PERMISSION_TITLES}

    db = await get_db()
    async with db.execute(
        "SELECT permissions FROM bot_admins WHERE tg_id = ?", (tg_id,)
    ) as cursor:
        row = await cursor.fetchone()
        if not row or not row["permissions"]:
            return dict(DEFAULT_ADMIN_PERMISSIONS)

        try:
            saved_perms = json.loads(row["permissions"])
            full_perms = dict(DEFAULT_ADMIN_PERMISSIONS)
            full_perms.update(saved_perms)
            return full_perms
        except Exception:
            return dict(DEFAULT_ADMIN_PERMISSIONS)


async def has_admin_permission(tg_id: int, perm_key: str) -> bool:
    if is_owner(tg_id):
        return True
    if tg_id <= 0:
        return False

    perms = await get_admin_permissions(tg_id)
    return perms.get(perm_key, False)


async def toggle_admin_permission(tg_id: int, perm_key: str) -> dict[str, bool]:
    if is_owner(tg_id) or perm_key not in PERMISSION_TITLES:
        return {k: True for k in PERMISSION_TITLES}

    current_perms = await get_admin_permissions(tg_id)
    current_perms[perm_key] = not current_perms.get(perm_key, False)

    db = await get_db()
    await db.execute(
        "UPDATE bot_admins SET permissions = ? WHERE tg_id = ?",
        (json.dumps(current_perms), tg_id),
    )
    await db.commit()
    return current_perms


async def get_bot_statistics() -> dict[str, Any]:
    """Calculate and return comprehensive bot statistics."""
    db = await get_db()

    async with db.execute("SELECT COUNT(*) FROM users") as c:
        row = await c.fetchone()
        total_users = row[0] if row else 0

    async with db.execute(
        "SELECT COUNT(*) FROM users WHERE date(joined_at) = date('now')"
    ) as c:
        row = await c.fetchone()
        users_today = row[0] if row else 0

    async with db.execute(
        "SELECT COUNT(*) FROM users WHERE joined_at >= datetime('now', '-7 days')"
    ) as c:
        row = await c.fetchone()
        users_week = row[0] if row else 0

    async with db.execute(
        "SELECT COUNT(*) FROM users WHERE joined_at >= datetime('now', '-30 days')"
    ) as c:
        row = await c.fetchone()
        users_month = row[0] if row else 0

    async with db.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM invoices WHERE status = 'paid'"
    ) as c:
        row = await c.fetchone()
        total_paid_invoices = row[0] if row else 0
        total_revenue = row[1] if row else 0

    async with db.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM invoices WHERE status = 'paid' AND (target_email = 'TOPUP' OR (duration_days = 0 AND data_gb = 0))"
    ) as c:
        row = await c.fetchone()
        topups_count = row[0] if row else 0
        topups_revenue = row[1] if row else 0

    async with db.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM invoices WHERE status = 'paid' AND target_email != 'TOPUP' AND (duration_days > 0 OR data_gb > 0)"
    ) as c:
        row = await c.fetchone()
        subs_sales_count = row[0] if row else 0
        subs_sales_revenue = row[1] if row else 0

    async with db.execute(
        "SELECT COUNT(*) FROM ip_violations WHERE suspended = 1"
    ) as c:
        row = await c.fetchone()
        suspended_count = row[0] if row else 0

    async with db.execute("SELECT COUNT(*) FROM bot_admins") as c:
        row = await c.fetchone()
        admins_count = row[0] if row else 0

    active_inbounds = await get_active_inbound_ids()

    return {
        "total_users": total_users,
        "users_today": users_today,
        "users_week": users_week,
        "users_month": users_month,
        "total_paid_invoices": total_paid_invoices,
        "total_revenue": total_revenue,
        "topups_count": topups_count,
        "topups_revenue": topups_revenue,
        "subs_sales_count": subs_sales_count,
        "subs_sales_revenue": subs_sales_revenue,
        "suspended_count": suspended_count,
        "admins_count": admins_count,
        "active_inbounds_count": len(active_inbounds),
    }
