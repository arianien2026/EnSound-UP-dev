import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


PRODUCT_CODE = "ensound_up_web_full_access"


@dataclass(frozen=True)
class Price:
    amount: int
    currency: str
    pricing_code: str


REGULAR_PRICE = Price(199, "TWD", "regular")
PROMO_PRICE = Price(99, "TWD", "launch_promo")


def _parse_aware_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None
    return timestamp.astimezone(timezone.utc)


def get_price(now: datetime | None = None) -> Price:
    """Price a new order from backend time and a valid seven-day launch window."""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Pricing time must be timezone-aware")

    start = _parse_aware_timestamp(os.environ.get("LAUNCH_PROMO_START_AT"))
    end = _parse_aware_timestamp(os.environ.get("LAUNCH_PROMO_END_AT"))
    if start is None or end is None or end - start != timedelta(days=7):
        return REGULAR_PRICE
    return PROMO_PRICE if start <= now.astimezone(timezone.utc) < end else REGULAR_PRICE
