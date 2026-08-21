"""
Data-access helpers for all tables (raw SQL, no ORM).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from db.database import get_db
from config import INVOICE_EXPIRY_MINUTES


# ──────────────────────────── Users ────────────────────────────


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
    """Ensure user and wallet exist in DB to prevent FOREIGN KEY constraint errors."""
    db = await get_db()
    await db.execute(
        """INSERT INTO users (tg_id, username, full_name)
           VALUES (?, ?, ?)
           ON CONFLICT(tg_id) DO UPDATE SET
               username = COALESCE(EXCLUDED.username, users.username),
               full_name = COALESCE(EXCLUDED.full_name, users.full_name)""",
        (tg_id, username, full_name),
    )
    # Create wallet automatically
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
    """Check if a user can claim a test subscription.

    Returns:
        (can_claim: bool, remaining_days: int, remaining_hours: int)
    """
    from config import TEST_COOLDOWN_DAYS

    await ensure_user(tg_id)
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
            cooldown_seconds = TEST_COOLDOWN_DAYS * 86400

            if elapsed_seconds < cooldown_seconds:
                remaining_sec = cooldown_seconds - elapsed_seconds
                days = int(remaining_sec // 86400)
                hours = int((remaining_sec % 86400) // 3600)
                return False, days, hours
            else:
                return True, 0, 0
        except Exception:
            return False, TEST_COOLDOWN_DAYS, 0

    # If test_used is 1 but last_test_at is NULL (legacy user), enforce cooldown or allow based on reset
    return False, TEST_COOLDOWN_DAYS, 0


async def is_test_used(tg_id: int) -> bool:
    can_claim, _, _ = await can_get_test_sub(tg_id)
    return not can_claim


async def reset_all_test_subs() -> int:
    """Reset test subscription usage for all users."""
    db = await get_db()
    cursor = await db.execute("UPDATE users SET test_used = 0, last_test_at = NULL")
    await db.commit()
    return cursor.rowcount


# ──────────────────────────── Wallets ────────────────────────────


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
    """Add funds. Returns new balance."""
    await ensure_user(tg_id)
    db = await get_db()
    await db.execute(
        "UPDATE wallets SET balance = balance + ? WHERE tg_id = ?",
        (amount, tg_id),
    )
    await db.commit()
    return await get_balance(tg_id)


async def debit_wallet(tg_id: int, amount: int) -> int:
    """Subtract funds. Returns new balance. Raises ValueError if insufficient."""
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


# ──────────────────────────── Invoices ────────────────────────────


async def create_invoice(
    tg_id: int,
    amount: int,
    duration_days: int,
    data_gb: int,
    users_count: int = 1,
    target_email: str | None = None,
    payment_method: str = "card",
) -> dict[str, Any]:
    await ensure_user(tg_id)
    db = await get_db()
    invoice_id = uuid4().hex[:12].upper()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=INVOICE_EXPIRY_MINUTES)

    await db.execute(
        """INSERT INTO invoices (id, tg_id, amount, duration_days, data_gb, users_count, target_email, payment_method, status, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            invoice_id,
            tg_id,
            amount,
            duration_days,
            data_gb,
            users_count,
            target_email,
            payment_method,
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
    """Fetch paginated invoices for a user along with total count."""
    await ensure_user(tg_id)
    db = await get_db()
    # First, expire any old pending invoices to ensure status is accurate
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
    """Get purchase history summary (approved count and total spent)."""
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
    """Mark all expired pending invoices. Returns count of expired."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        "UPDATE invoices SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?",
        (now,),
    )
    await db.commit()
    return cursor.rowcount


# ──────────────────────────── Referrals ────────────────────────────


async def create_referral(referrer_tg_id: int, referred_tg_id: int) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO referrals (referrer_tg_id, referred_tg_id) VALUES (?, ?)",
        (referrer_tg_id, referred_tg_id),
    )
    await db.commit()


# ──────────────────────────── Settings ────────────────────────────


async def get_setting(key: str, default: str | None = None) -> str | None:
    """Get a setting value by key."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT value FROM settings WHERE key = ?", (key,))
    if rows:
        return rows[0]["value"]
    return default


async def set_setting(key: str, value: str) -> None:
    """Set or update a setting value."""
    db = await get_db()
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )
    await db.commit()
