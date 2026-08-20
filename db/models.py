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
    row = await db.execute_fetchall(
        "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
    )
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


async def get_user(tg_id: int) -> dict[str, Any] | None:
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
    )
    if row:
        return dict(row[0])
    return None


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
    await db.execute(
        "UPDATE users SET test_used = 1 WHERE tg_id = ?", (tg_id,)
    )
    await db.commit()


async def is_test_used(tg_id: int) -> bool:
    await ensure_user(tg_id)
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT test_used FROM users WHERE tg_id = ?", (tg_id,)
    )
    if rows:
        return bool(rows[0]["test_used"])
    return False


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
) -> dict[str, Any]:
    await ensure_user(tg_id)
    db = await get_db()
    invoice_id = uuid4().hex[:12].upper()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=INVOICE_EXPIRY_MINUTES)

    await db.execute(
        """INSERT INTO invoices (id, tg_id, amount, duration_days, data_gb, status, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            invoice_id,
            tg_id,
            amount,
            duration_days,
            data_gb,
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


# ──────────────────────────── Subscriptions ────────────────────────────


async def create_subscription(
    tg_id: int,
    email: str,
    sub_id: str | None,
    service_name: str,
    data_gb: int,
    duration_days: int,
    is_test: bool = False,
) -> int:
    await ensure_user(tg_id)
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO subscriptions (tg_id, email, sub_id, service_name, data_gb, duration_days, is_test)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (tg_id, email, sub_id, service_name, data_gb, duration_days, int(is_test)),
    )
    await db.commit()
    return cursor.lastrowid  # type: ignore[return-value]


async def get_user_subscriptions(tg_id: int) -> list[dict[str, Any]]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM subscriptions WHERE tg_id = ? ORDER BY created_at DESC",
        (tg_id,),
    )
    return [dict(r) for r in rows]


async def get_subscription_by_email(email: str) -> dict[str, Any] | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM subscriptions WHERE email = ?", (email,)
    )
    if rows:
        return dict(rows[0])
    return None


async def update_subscription_name(email: str, new_name: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE subscriptions SET service_name = ? WHERE email = ?",
        (new_name, email),
    )
    await db.commit()


async def delete_subscription(email: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM subscriptions WHERE email = ?", (email,))
    await db.commit()


# ──────────────────────────── Referrals ────────────────────────────


async def create_referral(referrer_tg_id: int, referred_tg_id: int) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO referrals (referrer_tg_id, referred_tg_id) VALUES (?, ?)",
        (referrer_tg_id, referred_tg_id),
    )
    await db.commit()
