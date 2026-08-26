import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
BOT_NAME: str = os.getenv("BOT_NAME", "CandyPop")
ADMIN_CHAT_ID: int = int(os.getenv("ADMIN_CHAT_ID", "0"))
PROXY_URL: str | None = os.getenv("PROXY_URL") or None

CHANNEL_ID: str = os.getenv("CHANNEL_ID", "")
CHANNEL_LINK: str = os.getenv("CHANNEL_LINK", "")
SUPPORT_HANDLE: str = os.getenv("SUPPORT_HANDLE", "@support")
SUPPORT_LINK: str = os.getenv("SUPPORT_LINK", "https://t.me/candypop_v?direct")

XUI_BASE_URL: str = os.getenv("XUI_BASE_URL", "").rstrip("/")
XUI_API_TOKEN: str = os.getenv("XUI_API_TOKEN", "")
INBOUND_IDS: list[int] = [
    int(x.strip()) for x in os.getenv("INBOUND_IDS", "1").split(",") if x.strip()
]

SUB_BASE_URL: str = os.getenv("SUB_BASE_URL", "").rstrip("/")

TEST_DATA_GB: float = 0.5
TEST_DURATION_DAYS: int = 1
TEST_COOLDOWN_DAYS: int = int(os.getenv("TEST_COOLDOWN_DAYS", "14"))

INVOICE_EXPIRY_MINUTES: int = 20

VOLUME_TIERS: dict[int, int] = {
    10: 50_000,
    30: 144_000,
    50: 230_000,
    70: 308_000,
    90: 378_000,
    100: 398_000,
}

CUSTOM_PRICE_TIERS: list[tuple[int, int]] = [
    (20, 5_000),
    (50, 4_800),
    (100, 4_200),
]
CUSTOM_PRICE_DEFAULT_PER_GB: int = 3_800

DURATION_OPTIONS: list[int] = [30, 60, 90]

DB_PATH: str = os.getenv("DB_PATH", "data/candypop.db")
