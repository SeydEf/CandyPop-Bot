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
        _db = await aiosqlite.connect(DB_PATH, timeout=30.0)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA busy_timeout = 30000")
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
            original_amount INTEGER,
            processed_at    TEXT,
            processed_by    INTEGER,
            processed_by_name TEXT
            user_message_id INTEGER DEFAULT NULL,
            admin_message_id INTEGER DEFAULT NULL
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

        CREATE TABLE IF NOT EXISTS reserved_renewals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT NOT NULL,
            tg_id           INTEGER NOT NULL,
            duration_days   INTEGER NOT NULL,
            data_gb         INTEGER NOT NULL,
            users_count     INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            invoice_id      TEXT,
            status          TEXT NOT NULL DEFAULT 'pending',
            activated_at    TEXT,
            activation_type TEXT
        );

        CREATE TABLE IF NOT EXISTS bank_sms_transactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint     TEXT UNIQUE NOT NULL,
            amount_toman    INTEGER NOT NULL,
            amount_rial     INTEGER NOT NULL,
            sender          TEXT,
            account_number  TEXT,
            balance_rial    INTEGER,
            timestamp_str   TEXT,
            raw_sms         TEXT,
            received_at     TEXT,
            invoice_id      TEXT REFERENCES invoices(id) ON DELETE CASCADE,
            status          TEXT NOT NULL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_bank_sms_fingerprint ON bank_sms_transactions(fingerprint);
        CREATE INDEX IF NOT EXISTS idx_bank_sms_amount ON bank_sms_transactions(amount_toman);
    """)

    async with db.execute("PRAGMA table_info(reserved_renewals)") as cur:
        cols = {row["name"]: dict(row) for row in await cur.fetchall()}
    if "status" not in cols:
        await db.execute(
            "ALTER TABLE reserved_renewals ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
        )
    if "activated_at" not in cols:
        await db.execute("ALTER TABLE reserved_renewals ADD COLUMN activated_at TEXT")
    if "activation_type" not in cols:
        await db.execute(
            "ALTER TABLE reserved_renewals ADD COLUMN activation_type TEXT"
        )

    async with db.execute("PRAGMA index_list(reserved_renewals)") as cur:
        indices = await cur.fetchall()
        has_auto_unique = any(
            idx["unique"] and "autoindex" in idx["name"] for idx in indices
        )
    if has_auto_unique:
        await db.executescript("""
            CREATE TABLE reserved_renewals_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                email           TEXT NOT NULL,
                tg_id           INTEGER NOT NULL,
                duration_days   INTEGER NOT NULL,
                data_gb         INTEGER NOT NULL,
                users_count     INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                invoice_id      TEXT,
                status          TEXT NOT NULL DEFAULT 'pending',
                activated_at    TEXT,
                activation_type TEXT
            );
            INSERT INTO reserved_renewals_new (id, email, tg_id, duration_days, data_gb, users_count, created_at, invoice_id, status, activated_at, activation_type)
            SELECT id, email, tg_id, duration_days, data_gb, users_count, created_at, invoice_id, status, activated_at, activation_type
            FROM reserved_renewals;
            DROP TABLE reserved_renewals;
            ALTER TABLE reserved_renewals_new RENAME TO reserved_renewals;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_res_renewals_pending_email
                ON reserved_renewals(email) WHERE status = 'pending';
            CREATE INDEX IF NOT EXISTS idx_res_renewals_tg_id
                ON reserved_renewals(tg_id, status);
        """)

    await db.execute("DROP TABLE IF EXISTS reserved_renewal_logs")
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_res_renewals_pending_email ON reserved_renewals(email) WHERE status = 'pending'"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_res_renewals_tg_id ON reserved_renewals(tg_id, status)"
    )
    await db.commit()


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None
