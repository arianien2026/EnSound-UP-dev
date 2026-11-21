"""Post-purchase email proof. This module never authorizes a device or Full Access."""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import EmailClaimRateLimit, EmailClaimSession, EmailClaimToken, Entitlement, Order
from app.services.entitlement_grant import IneligibleEntitlementOrder, _eligible_order
from app.dev_e2e import require_scoped_email


CLAIM_LIFETIME = timedelta(minutes=15)
SESSION_LIFETIME = timedelta(minutes=10)
RATE_WINDOW = timedelta(hours=1)
EMAIL_MIN_INTERVAL = timedelta(minutes=10)


def get_rate_limit_key() -> bytes:
    key = os.environ.get("CLAIM_RATE_LIMIT_KEY", "")
    if len(key) < 32 or key.strip() != key:
        raise RuntimeError("Email claim service unavailable")
    return key.encode("utf-8")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _rate_key(secret: bytes, kind: str, value: str) -> str:
    return hmac.new(secret, f"{kind}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()


def _utc(value: datetime) -> datetime:
    # SQLite drops timezone info in isolated tests; PostgreSQL retains it.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _count_request(session: Session, key_hash: str, now: datetime, maximum: int,
                   interval: timedelta = timedelta(0)) -> bool:
    """Atomic on PostgreSQL: unique key plus row lock serialize competing requests."""
    row = session.get(EmailClaimRateLimit, key_hash, with_for_update=True)
    if row is None:
        try:
            with session.begin_nested():
                session.add(EmailClaimRateLimit(
                    key_hash=key_hash, window_started_at=now,
                    last_requested_at=now, request_count=1,
                ))
                session.flush()
        except IntegrityError:
            row = session.get(EmailClaimRateLimit, key_hash, with_for_update=True)
        else:
            return True
    if row is None:
        raise RuntimeError("Email claim rate limit unavailable")
    if now >= _utc(row.window_started_at) + RATE_WINDOW:
        row.window_started_at = now
        row.request_count = 0
    allowed = (row.request_count < maximum and
               now >= _utc(row.last_requested_at) + interval)
    # Count permitted requests toward the hourly allowance. Rejected retries
    # neither spend an email allowance nor restart its resend cooldown.
    if allowed:
        row.request_count += 1
        row.last_requested_at = now
    return allowed


def issue_claim_if_eligible(session: Session, email: str, client_ip: str, now: datetime,
                            secret: bytes) -> str | None:
    # Count unknown email requests too. A generic HTTP response never discloses this result.
    ip_allowed = _count_request(session, _rate_key(secret, "ip", client_ip), now, 30)
    # A blocked IP cannot create unlimited per-email limiter rows.
    email_allowed = ip_allowed and _count_request(
        session, _rate_key(secret, "email", email), now, 3, EMAIL_MIN_INTERVAL
    )
    entitlement = session.execute(
        select(Entitlement).join(Order, Entitlement.source_order_id == Order.id).where(
            Entitlement.entitlement_email == email,
            Entitlement.status == "active",
            Order.email_verified_at.is_not(None),
            Order.status.in_(("paid", "refund_pending")),
        ).order_by(Entitlement.created_at.desc(), Entitlement.id.desc()).limit(1)
    ).scalar_one_or_none()
    if not email_allowed or entitlement is None:
        return None
    token = secrets.token_urlsafe(32)
    session.add(EmailClaimToken(
        entitlement_id=entitlement.id, token_hash=_digest(token),
        purpose="device_claim", created_at=now, expires_at=now + CLAIM_LIFETIME,
    ))
    return token


def allow_redemption_attempt(session: Session, client_ip: str, now: datetime,
                             secret: bytes) -> bool:
    return _count_request(session, _rate_key(secret, "redeem_ip", client_ip), now, 60)


def redeem_claim(session: Session, token: str, now: datetime) -> str | None:
    # Row lock and consumed_at update are committed with the session record.
    claim = session.execute(select(EmailClaimToken).where(
        EmailClaimToken.token_hash == _digest(token),
    ).with_for_update()).scalar_one_or_none()
    if (claim is None or claim.purpose != "device_claim" or
            claim.consumed_at is not None or _utc(claim.expires_at) <= now):
        return None
    reference = session.get(Entitlement, claim.entitlement_id)
    if reference is None:
        return None
    order = session.execute(select(Order).where(
        Order.id == reference.source_order_id,
    ).with_for_update()).scalar_one_or_none()
    entitlement = session.execute(select(Entitlement).where(
        Entitlement.id == claim.entitlement_id,
    ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if entitlement is None or entitlement.status != "active":
        return None
    require_scoped_email(entitlement.entitlement_email)
    if (order is None or order.status not in ("paid", "refund_pending") or
            order.email_verified_at is None):
        return None
    session_token = secrets.token_urlsafe(32)
    claim.consumed_at = now
    session.add(EmailClaimSession(
        entitlement_id=entitlement.id, session_hash=_digest(session_token),
        scope="device_claim", created_at=now, expires_at=now + SESSION_LIFETIME,
    ))
    return session_token


def issue_release_if_eligible(session: Session, email: str, client_ip: str,
                              now: datetime, secret: bytes) -> str | None:
    """Use the existing shared email/IP limits without disclosing purchaser status."""
    ip_allowed = _count_request(session, _rate_key(secret, "ip", client_ip), now, 30)
    email_allowed = ip_allowed and _count_request(
        session, _rate_key(secret, "email", email), now, 3, EMAIL_MIN_INTERVAL
    )
    reference = session.execute(select(Entitlement).where(
        Entitlement.entitlement_email == email, Entitlement.status == "active",
    ).order_by(Entitlement.created_at.desc(), Entitlement.id.desc()).limit(1)).scalar_one_or_none()
    if not email_allowed or reference is None:
        return None
    try:
        order = _eligible_order(session, reference.source_order_id)
    except IneligibleEntitlementOrder:
        return None
    if reference.product_code != order.product_code or reference.entitlement_email != order.entitlement_email:
        return None
    token = secrets.token_urlsafe(32)
    session.add(EmailClaimToken(
        entitlement_id=reference.id, token_hash=_digest(token), purpose="device_release",
        created_at=now, expires_at=now + CLAIM_LIFETIME,
    ))
    return token


def redeem_release_claim(session: Session, token: str, now: datetime) -> str | None:
    claim = session.execute(select(EmailClaimToken).where(
        EmailClaimToken.token_hash == _digest(token),
    ).with_for_update()).scalar_one_or_none()
    if (claim is None or claim.purpose != "device_release" or
            claim.consumed_at is not None or _utc(claim.expires_at) <= now):
        return None
    reference = session.get(Entitlement, claim.entitlement_id)
    if reference is None:
        return None
    try:
        order = _eligible_order(session, reference.source_order_id)
    except IneligibleEntitlementOrder:
        return None
    entitlement = session.execute(select(Entitlement).where(
        Entitlement.id == claim.entitlement_id,
    ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if (entitlement is None or entitlement.status != "active" or
            entitlement.product_code != order.product_code or
            entitlement.entitlement_email != order.entitlement_email):
        return None
    session_token = secrets.token_urlsafe(32)
    claim.consumed_at = now
    session.add(EmailClaimSession(
        entitlement_id=entitlement.id, session_hash=_digest(session_token),
        scope="device_release", created_at=now, expires_at=now + SESSION_LIFETIME,
    ))
    return session_token
