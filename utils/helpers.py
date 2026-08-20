"""
Miscellaneous helpers: QR code generation, email generation, pricing calculation.
"""

from __future__ import annotations

import io
import uuid

import qrcode  # type: ignore[import-untyped]

from config import CUSTOM_PRICE_TIERS, CUSTOM_PRICE_DEFAULT_PER_GB


def generate_qr(data: str) -> io.BytesIO:
    """Generate a QR code PNG image and return as BytesIO."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_email(tg_id: int) -> str:
    """Generate a unique email identifier for an X-UI client.

    Format: cp_<tg_id>_<short_uuid>
    """
    short = uuid.uuid4().hex[:8]
    return f"cp_{tg_id}_{short}"


def generate_service_name(tg_id: int) -> str:
    """Generate a default service name."""
    short = uuid.uuid4().hex[:4]
    return f"CandyPop-{short}"


def calculate_custom_price(gb: int) -> int:
    """Calculate the total price for a custom GB volume using tiered pricing.

    Returns total price in Tomans.
    """
    if gb <= 0:
        return 0

    total_price = 0
    remaining_gb = gb
    prev_limit = 0

    for max_gb, per_gb_price in CUSTOM_PRICE_TIERS:
        if remaining_gb <= 0:
            break
        tier_gb = min(remaining_gb, max_gb - prev_limit)
        if tier_gb > 0:
            total_price += tier_gb * per_gb_price
            remaining_gb -= tier_gb
        prev_limit = max_gb

    # Any remaining GB beyond the last tier
    if remaining_gb > 0:
        total_price += remaining_gb * CUSTOM_PRICE_DEFAULT_PER_GB

    return total_price


def gb_to_bytes(gb: int) -> int:
    """Convert gigabytes to bytes."""
    return gb * 1024 * 1024 * 1024
