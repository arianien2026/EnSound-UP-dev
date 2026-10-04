import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order


VERIFICATION_LIFETIME = timedelta(minutes=30)


def issue_verification_token(order: Order, now: datetime) -> str:
    token = secrets.token_urlsafe(32)
    order.email_verification_token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    order.email_verification_expires_at = now + VERIFICATION_LIFETIME
    return token


def is_payment_eligible(order: Order | None, now: datetime | None = None) -> bool:
    if now is None:
        now = datetime.now(timezone.utc)
    return bool(
        order is not None
        and order.status == "pending"
        and order.expires_at > now
        and order.email_verified_at is not None
    )


def consume_verification_token(session: Session, token: str, now: datetime) -> bool:
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    # The row lock serializes simultaneous clicks; a consumed digest cannot match again.
    order = session.execute(
        select(Order).where(Order.email_verification_token_hash == digest).with_for_update()
    ).scalar_one_or_none()
    if (
        order is None
        or order.email_verified_at is not None
        or order.status != "pending"
        or order.expires_at <= now
        or order.email_verification_expires_at is None
        or order.email_verification_expires_at <= now
    ):
        return False
    order.email_verified_at = now
    order.email_verification_token_hash = None
    order.email_verification_expires_at = None
    return True
