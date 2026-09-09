import os
import aiosqlite

from config import DB_PATH

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
    return _db


async def init_db() -> None:
    db = await get_db()

    await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id        INTEGER PRIMARY KEY,
            username     TEXT,
            full_name    TEXT,
            joined_at    TEXT NOT NULL DEFAULT (datetime('now')),
            referrer_id  INTEGER,
            test_used    INTEGER NOT NULL DEFAULT 0,
            last_test_at TEXT,
            is_banned    INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS wallets (
            tg_id   INTEGER PRIMARY KEY REFERENCES users(tg_id),
            balance INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id              TEXT PRIMARY KEY,
            tg_id           INTEGER NOT NULL REFERENCES users(tg_id),
            amount          INTEGER NOT NULL,
            duration_days   INTEGER NOT NULL,
            data_gb         INTEGER NOT NULL,
            users_count     INTEGER NOT NULL DEFAULT 1,
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at      TEXT NOT NULL,
            receipt_file_id TEXT,
            receipt_text    TEXT,
            message_id      INTEGER,
            target_email    TEXT,
            payment_method  TEXT DEFAULT 'card',
            discount_code   TEXT,
            original_amount INTEGER
        );

        CREATE TABLE IF NOT EXISTS referrals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_tg_id  INTEGER NOT NULL REFERENCES users(tg_id),
            referred_tg_id  INTEGER NOT NULL REFERENCES users(tg_id),
            rewarded        INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS discount_codes (
            code             TEXT PRIMARY KEY,
            discount_percent INTEGER NOT NULL,
            max_uses         INTEGER DEFAULT -1,
            used_count       INTEGER NOT NULL DEFAULT 0,
            is_active        INTEGER NOT NULL DEFAULT 1,
            rules            TEXT DEFAULT '{}',
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS discount_usage (
            code        TEXT NOT NULL,
            tg_id       INTEGER NOT NULL,
            used_at     TEXT NOT NULL DEFAULT (datetime('now')),
            usage_count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (code, tg_id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notified_alerts (
            email       TEXT NOT NULL,
            alert_type  TEXT NOT NULL,
            notified_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (email, alert_type)
        );

        CREATE TABLE IF NOT EXISTS ip_violations (
            email            TEXT PRIMARY KEY,
            violation_count  INTEGER NOT NULL DEFAULT 0,
            total_incidents  INTEGER NOT NULL DEFAULT 0,
            last_violated_at TEXT NOT NULL DEFAULT (datetime('now')),
            suspended        INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS bot_admins (
            tg_id       INTEGER PRIMARY KEY,
            username    TEXT,
            added_by    INTEGER,
            permissions TEXT DEFAULT '{}',
            added_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    try:
        await db.execute(
            "ALTER TABLE bot_admins ADD COLUMN permissions TEXT DEFAULT '{}'"
        )
    except Exception:
        pass

    try:
        await db.execute(
            "ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0"
        )
    except Exception:
        pass

    try:
        await db.execute(
            "ALTER TABLE discount_codes ADD COLUMN rules TEXT DEFAULT '{}'"
        )
    except Exception:
        pass

    try:
        await db.execute(
            "ALTER TABLE discount_usage ADD COLUMN usage_count INTEGER NOT NULL DEFAULT 1"
        )
    except Exception:
        pass

    await db.commit()


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None
