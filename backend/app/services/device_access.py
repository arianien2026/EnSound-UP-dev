"""Backend-only online authorization; browser flags and device IDs are not proof."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuthorizedDevice, EmailClaimSession, Entitlement, Order
from app.services.entitlement_grant import IneligibleEntitlementOrder, _eligible_order
from app.services.pricing import PRODUCT_CODE


MAX_DEVICES = 3
ONLINE_CREDENTIAL_LIFETIME = timedelta(days=30)


class InvalidClaimSession(Exception):
    pass


class IneligibleAccess(Exception):
    pass


class DeviceLimitReached(Exception):
    pass


@dataclass(frozen=True)
class RegisteredDevice:
    credential: str
    credential_expires_at: datetime


@dataclass(frozen=True)
class OnlineAccess:
    product_code: str
    credential_expires_at: datetime


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _utc(value: datetime) -> datetime:
    # Isolated SQLite tests drop timezone information; PostgreSQL preserves it.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def register_device(session: Session, claim_credential: str, device_id: UUID,
                    now: datetime) -> RegisteredDevice:
    """Consume email proof with registration in one transaction.

    The caller must commit before returning the new credential. An entitlement
    row lock serializes the slot count across distinct claim sessions on PG.
    A fresh email claim for the same active Device ID rotates its credential
    without consuming another slot. A consumed claim cannot be replayed.
    """
    claim = session.execute(select(EmailClaimSession).where(
        EmailClaimSession.session_hash == _digest(claim_credential),
    ).with_for_update()).scalar_one_or_none()
    if (claim is None or claim.scope != "device_claim" or
            claim.consumed_at is not None or _utc(claim.expires_at) <= now):
        raise InvalidClaimSession()

    reference = session.get(Entitlement, claim.entitlement_id)
    if reference is None:
        raise IneligibleAccess()
    try:
        order = _eligible_order(session, reference.source_order_id)  # Locks the paid order.
    except IneligibleEntitlementOrder:
        raise IneligibleAccess() from None
    entitlement = session.execute(select(Entitlement).where(
        Entitlement.id == claim.entitlement_id,
    ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if (entitlement is None or entitlement.status != "active" or
            entitlement.source_order_id != order.id or
            entitlement.product_code != order.product_code or
            entitlement.entitlement_email != order.entitlement_email):
        raise IneligibleAccess()

    existing = session.execute(select(AuthorizedDevice).where(
        AuthorizedDevice.entitlement_id == entitlement.id,
        AuthorizedDevice.device_id == device_id,
    ).with_for_update()).scalar_one_or_none()
    if existing is not None and existing.revoked_at is not None:
        raise IneligibleAccess()
    if existing is None:
        count = session.scalar(select(func.count()).select_from(AuthorizedDevice).where(
            AuthorizedDevice.entitlement_id == entitlement.id,
            AuthorizedDevice.revoked_at.is_(None),
        ))
        if count >= MAX_DEVICES:
            raise DeviceLimitReached()

    credential = secrets.token_urlsafe(32)
    expiry = now + ONLINE_CREDENTIAL_LIFETIME
    if existing is None:
        session.add(AuthorizedDevice(
            entitlement_id=entitlement.id, device_id=device_id,
            credential_hash=_digest(credential), registered_at=now,
            updated_at=now, credential_expires_at=expiry,
        ))
    else:
        existing.credential_hash = _digest(credential)
        existing.updated_at = now
        existing.credential_expires_at = expiry
    claim.consumed_at = now
    session.flush()
    return RegisteredDevice(credential, expiry)


def verify_online_access(session: Session, device_id: UUID, credential: str,
                         now: datetime) -> OnlineAccess | None:
    device = session.execute(select(AuthorizedDevice).where(
        AuthorizedDevice.device_id == device_id,
        AuthorizedDevice.credential_hash == _digest(credential),
        AuthorizedDevice.revoked_at.is_(None),
    )).scalar_one_or_none()
    if device is None or _utc(device.credential_expires_at) <= now:
        return None
    entitlement = session.get(Entitlement, device.entitlement_id)
    if entitlement is None or entitlement.status != "active" or entitlement.product_code != PRODUCT_CODE:
        return None
    order = session.get(Order, entitlement.source_order_id)
    if (order is None or order.status not in ("paid", "refund_pending") or
            order.paid_at is None or order.email_verified_at is None or
            order.product_code != PRODUCT_CODE or order.currency != "TWD" or
            type(order.amount) is not int or order.amount <= 0 or
            not order.provider_order_id or not order.provider_trade_no or
            order.payment_environment != "ecpay_stage" or order.provider_paid_at is None or
            entitlement.entitlement_email != order.entitlement_email):
        return None
    return OnlineAccess(entitlement.product_code, _utc(device.credential_expires_at))
