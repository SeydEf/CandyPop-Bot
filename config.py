import os
from dotenv import load_dotenv

load_dotenv()

# --- Bot ---
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID: int = int(os.getenv("ADMIN_CHAT_ID", "0"))

# --- Channel ---
CHANNEL_ID: str = os.getenv("CHANNEL_ID", "")  # e.g. "@channel_name" or "-100..."
CHANNEL_LINK: str = os.getenv("CHANNEL_LINK", "")

# --- X-UI Panel ---
XUI_BASE_URL: str = os.getenv("XUI_BASE_URL", "").rstrip("/")
XUI_API_TOKEN: str = os.getenv("XUI_API_TOKEN", "")
INBOUND_IDS: list[int] = [
    int(x.strip()) for x in os.getenv("INBOUND_IDS", "1").split(",") if x.strip()
]

# --- Payment ---
CARD_NUMBER: str = os.getenv("CARD_NUMBER", "")
CARD_HOLDER: str = os.getenv("CARD_HOLDER", "")

# --- Subscription ---
SUB_BASE_URL: str = os.getenv("SUB_BASE_URL", "").rstrip("/")

# --- Test Subscription ---
TEST_DATA_GB: int = 1  # 1 GB
TEST_DURATION_DAYS: int = 1  # 1 day

# --- Invoice ---
INVOICE_EXPIRY_MINUTES: int = 20

# --- Pricing ---
# Predefined volume tiers: {gb: price_tomans}
VOLUME_TIERS: dict[int, int] = {
    10: 50_000,
    30: 144_000,
    50: 230_000,
    70: 308_000,
    90: 378_000,
    100: 398_000,
}

# Custom volume pricing (per GB in Tomans)
CUSTOM_PRICE_TIERS: list[tuple[int, int]] = [
    # (max_gb_inclusive, price_per_gb)
    (20, 5_000),
    (50, 4_800),
    (100, 4_200),
]
CUSTOM_PRICE_DEFAULT_PER_GB: int = 3_800  # for volumes > 100 GB

# Duration options (days)
DURATION_OPTIONS: list[int] = [30, 60, 90]

# --- Database ---
DB_PATH: str = os.getenv("DB_PATH", "candypop.db")
