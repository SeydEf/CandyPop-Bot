"""
Async SQLite database setup using aiosqlite.
"""

import os
import aiosqlite

from config import DB_PATH

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Return the singleton database connection, creating it if needed."""
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
    """Create all tables if they do not exist."""
    db = await get_db()

    await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id        INTEGER PRIMARY KEY,
            username     TEXT,
            full_name    TEXT,
            joined_at    TEXT NOT NULL DEFAULT (datetime('now')),
            referrer_id  INTEGER,
            test_used    INTEGER NOT NULL DEFAULT 0,
            last_test_at TEXT
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
            target_email    TEXT
        );

        CREATE TABLE IF NOT EXISTS referrals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_tg_id  INTEGER NOT NULL REFERENCES users(tg_id),
            referred_tg_id  INTEGER NOT NULL REFERENCES users(tg_id),
            rewarded        INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)

    # Column migrations
    try:
        await db.execute("ALTER TABLE invoices ADD COLUMN target_email TEXT")
    except Exception:
        pass
    await db.commit()


async def close_db() -> None:
    """Close the database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
