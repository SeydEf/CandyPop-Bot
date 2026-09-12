from __future__ import annotations

from datetime import datetime

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


def format_size(bytes_val: int | float) -> str:
    if bytes_val <= 0:
        return "0GB"
    tb = bytes_val / (1024**4)
    if tb >= 1:
        if tb == int(tb):
            return f"{int(tb)}TB"
        return f"{tb:.2f}TB"
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

    if expiry_ms == 0:
        return "نامحدود"
    if expiry_ms < 0:
        days = abs(expiry_ms) // (1000 * 60 * 60 * 24)
        return f"{days} روز (پس از اتصال)"
    now_ms = int(time.time() * 1000)
    remaining_ms = expiry_ms - now_ms
    if remaining_ms <= 0:
        return "0 روز"
    days = remaining_ms // (1000 * 60 * 60 * 24)
    return f"{days} روز"


def format_datetime(
    val: str | int | float | datetime | None, with_seconds: bool = False
) -> str:
    from datetime import datetime, timedelta, timezone

    if val is None or val == "":
        return "نامشخص"
    try:
        if isinstance(val, (int, float)):
            ts = float(val)
            if ts > 1e11:
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif isinstance(val, datetime):
            dt = val
        else:
            s = str(val).strip()
            try:
                ts = float(s)
                if ts > 1e11:
                    ts /= 1000.0
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            except ValueError:
                dt = datetime.fromisoformat(s)

        tz_iran = timezone(timedelta(hours=3, minutes=30))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc).astimezone(tz_iran)
        else:
            dt = dt.astimezone(tz_iran)

        time_fmt = "%Y/%m/%d — %H:%M:%S" if with_seconds else "%Y/%m/%d — %H:%M"

        try:
            import jdatetime

            jdt = jdatetime.datetime.fromgregorian(datetime=dt)
            formatted_str = jdt.strftime(time_fmt)
        except ImportError:
            formatted_str = dt.strftime(time_fmt)

        return to_persian_digits(formatted_str)
    except Exception:
        clean_str = str(val)[:16].replace("-", "/")
        return to_persian_digits(clean_str)
