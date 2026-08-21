_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_PERSIAN_TO_ENGLISH = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def to_persian_digits(text: str | int | float) -> str:
    s = str(text)
    return "".join(_PERSIAN_DIGITS[int(ch)] if ch.isdigit() else ch for ch in s)


def persian_to_english_digits(text: str) -> str:
    return text.translate(_PERSIAN_TO_ENGLISH)


def format_price(amount: int) -> str:
    formatted = f"{amount:,}"
    return f"{formatted} تومان"


def format_size(bytes_val: int) -> str:
    if bytes_val <= 0:
        return "0GB"
    gb = bytes_val / (1024**3)
    if gb >= 1:
        if gb == int(gb):
            return f"{int(gb)}GB"
        return f"{gb:.1f}GB"
    mb = bytes_val / (1024**2)
    return f"{mb:.0f}MB"


def format_size_gb(gb: int | float) -> str:
    if gb == int(gb):
        return f"{int(gb)}GB"
    return f"{gb:.1f}GB"


def format_remaining_days(expiry_ms: int) -> str:
    import time

    if expiry_ms <= 0:
        return "نامحدود"
    now_ms = int(time.time() * 1000)
    remaining_ms = expiry_ms - now_ms
    if remaining_ms <= 0:
        return "0 روز"
    days = remaining_ms // (1000 * 60 * 60 * 24)
    return f"{days} روز"


def format_datetime(iso_str: str) -> str:
    from datetime import datetime, timedelta, timezone

    if not iso_str:
        return "نامشخص"
    try:
        dt = datetime.fromisoformat(iso_str)
        tz_iran = timezone(timedelta(hours=3, minutes=30))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc).astimezone(tz_iran)
        else:
            dt = dt.astimezone(tz_iran)

        try:
            import jdatetime

            jdt = jdatetime.datetime.fromgregorian(datetime=dt)
            formatted_str = jdt.strftime("%Y/%m/%d — %H:%M")
        except ImportError:
            formatted_str = dt.strftime("%Y/%m/%d — %H:%M")

        return to_persian_digits(formatted_str)
    except Exception:
        clean_str = str(iso_str)[:16].replace("-", "/")
        return to_persian_digits(clean_str)
