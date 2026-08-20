"""
Persian text and number formatting utilities.
"""

_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


def to_persian_digits(text: str | int | float) -> str:
    """Convert Latin digits (0-9) in *text* to their Persian equivalents."""
    s = str(text)
    return "".join(_PERSIAN_DIGITS[int(ch)] if ch.isdigit() else ch for ch in s)


def format_price(amount: int) -> str:
    """Format price in Tomans with commas and Persian digits: ۵۰,۰۰۰ تومان"""
    formatted = f"{amount:,}"
    return to_persian_digits(formatted) + " تومان"


def format_size(bytes_val: int) -> str:
    """Human-readable data size in Persian.

    Returns GB if ≥ 1 GB, otherwise MB.
    """
    if bytes_val <= 0:
        return to_persian_digits("0 GB")
    gb = bytes_val / (1024 ** 3)
    if gb >= 1:
        if gb == int(gb):
            return to_persian_digits(f"{int(gb)} GB")
        return to_persian_digits(f"{gb:.1f} GB")
    mb = bytes_val / (1024 ** 2)
    return to_persian_digits(f"{mb:.0f} MB")


def format_size_gb(gb: int | float) -> str:
    """Format a value already in GB."""
    if gb == int(gb):
        return to_persian_digits(f"{int(gb)} GB")
    return to_persian_digits(f"{gb:.1f} GB")


def format_remaining_days(expiry_ms: int) -> str:
    """Calculate remaining days from a Unix timestamp in milliseconds."""
    import time

    if expiry_ms <= 0:
        return "نامحدود"
    now_ms = int(time.time() * 1000)
    remaining_ms = expiry_ms - now_ms
    if remaining_ms <= 0:
        return to_persian_digits("0") + " روز"
    days = remaining_ms // (1000 * 60 * 60 * 24)
    return to_persian_digits(str(days)) + " روز"


def format_traffic_usage(up: int, down: int, total: int) -> str:
    """Format traffic usage as: مصرف شده / کل."""
    used = up + down
    return f"{format_size(used)} / {format_size(total)}"
