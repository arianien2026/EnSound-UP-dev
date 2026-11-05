"""Email-owner-authorized, one-device release with entitlement-wide cooldown."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuthorizedDevice, EmailClaimSession, Entitlement
from app.services.entitlement_grant import IneligibleEntitlementOrder, _eligible_order


RELEASE_COOLDOWN = timedelta(days=30)


class InvalidReleaseSession(Exception):
    pass


class ReleaseUnavailable(Exception):
    pass


class ReleaseCooldown(Exception):
    pass


@dataclass(frozen=True)
class DeviceSummary:
    device_id: UUID
    registered_at: datetime


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _authorized(session: Session, credential: str, now: datetime,
                *, lock: bool) -> tuple[EmailClaimSession, Entitlement]:
    query = select(EmailClaimSession).where(EmailClaimSession.session_hash == _digest(credential))
    proof = session.execute(query.with_for_update() if lock else query).scalar_one_or_none()
    if (proof is None or proof.scope != "device_release" or proof.consumed_at is not None or
            _utc(proof.expires_at) <= now):
        raise InvalidReleaseSession()
    reference = session.get(Entitlement, proof.entitlement_id)
    if reference is None:
        raise ReleaseUnavailable()
    try:
        order = _eligible_order(session, reference.source_order_id)
    except IneligibleEntitlementOrder:
        raise ReleaseUnavailable() from None
    entitlement_query = select(Entitlement).where(Entitlement.id == proof.entitlement_id)
    entitlement = session.execute(
        (entitlement_query.with_for_update() if lock else entitlement_query)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (entitlement is None or entitlement.status != "active" or
            entitlement.source_order_id != order.id or
            entitlement.product_code != order.product_code or
            entitlement.entitlement_email != order.entitlement_email):
        raise ReleaseUnavailable()
    return proof, entitlement


def list_release_devices(session: Session, credential: str, now: datetime) -> list[DeviceSummary]:
    """Only a valid release-scoped owner proof can view minimal device metadata."""
    _, entitlement = _authorized(session, credential, now, lock=False)
    rows = session.scalars(select(AuthorizedDevice).where(
        AuthorizedDevice.entitlement_id == entitlement.id,
        AuthorizedDevice.revoked_at.is_(None),
    ).order_by(AuthorizedDevice.registered_at, AuthorizedDevice.id)).all()
    return [DeviceSummary(row.device_id, _utc(row.registered_at)) for row in rows]


def release_device(session: Session, credential: str, device_id: UUID,
                   now: datetime) -> datetime:
    """Caller commits; failure rolls back proof, device, and cooldown together."""
    proof, entitlement = _authorized(session, credential, now, lock=True)
    # PostgreSQL clock is sampled after acquiring locks, avoiding stale worker time.
    effective_now = (session.scalar(select(func.clock_timestamp()))
                     if session.bind.dialect.name == "postgresql" else now)
    if _utc(proof.expires_at) <= effective_now:
        raise InvalidReleaseSession()
    if (entitlement.last_device_released_at is not None and
            effective_now < _utc(entitlement.last_device_released_at) + RELEASE_COOLDOWN):
        raise ReleaseCooldown()
    target = session.execute(select(AuthorizedDevice).where(
        AuthorizedDevice.entitlement_id == entitlement.id,
        AuthorizedDevice.device_id == device_id,
    ).with_for_update()).scalar_one_or_none()
    if target is None or target.revoked_at is not None:
        raise ReleaseUnavailable()
    target.revoked_at = effective_now
    target.updated_at = effective_now
    entitlement.last_device_released_at = effective_now
    entitlement.updated_at = effective_now
    proof.consumed_at = effective_now
    session.flush()
    return effective_now
