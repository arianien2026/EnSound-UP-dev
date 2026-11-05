"""Internal, opt-in recovery for paid orders without an active entitlement.

No HTTP route or scheduler calls this function. An authorized maintenance runner
may call it after reviewing the deployment's payment and migration state.
"""

import logging
from dataclasses import dataclass
from threading import Lock

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import sessionmaker

from app.models import Entitlement, Order
from app.services.entitlement_grant import (
    EntitlementGrantUnavailable,
    IneligibleEntitlementOrder,
    grant_paid_order,
)


@dataclass(frozen=True)
class ReconciliationResult:
    attempted: int
    activated: int
    rejected: int
    failed: int


# A process-local, one-use misuse guard, NOT a security boundary against Python code
# that can import private names or directly call grant_paid_order().
_PERMIT_ISSUER_KEY = object()


class RecoveryPermit:
    """Bind one recovery attempt to its exact factory and order ID."""

    __slots__ = ("_factory", "_order_ids", "_unused", "_lock")

    def __init__(self, factory: sessionmaker, order_ids: tuple[UUID, ...], *, _key: object = None):
        if _key is not _PERMIT_ISSUER_KEY:
            raise PermissionError("Recovery permit cannot be constructed directly")
        self._factory = factory
        self._order_ids = order_ids
        self._unused = True
        self._lock = Lock()

    def consume(self, factory: sessionmaker, order_ids: tuple[UUID, ...]) -> None:
        with self._lock:
            if not self._unused or factory is not self._factory or order_ids != self._order_ids:
                raise PermissionError("Recovery permit is invalid, mismatched or already used")
            self._unused = False


def _issue_recovery_permit(factory: sessionmaker, order_ids: list[UUID]) -> RecoveryPermit:
    """For guarded CLI after verification; tests may issue for isolated SQLite only."""
    if not 1 <= len(order_ids) <= 100 or any(not isinstance(i, UUID) for i in order_ids):
        raise ValueError("Recovery scope must contain 1–100 UUIDs")
    if len(set(order_ids)) != len(order_ids):
        raise ValueError("Duplicate recovery order IDs")
    return RecoveryPermit(factory, tuple(order_ids), _key=_PERMIT_ISSUER_KEY)


def recovery_candidates(
    factory: sessionmaker, *, order_id: UUID | None = None, limit: int = 100,
) -> list[UUID]:
    """Bounded, read-only candidate scan. Grant eligibility is checked again under lock."""
    if not 1 <= limit <= 1000:
        raise ValueError("Invalid reconciliation limit")
    with factory() as session:
        query = (
            select(Order.id)
            .outerjoin(Entitlement, Entitlement.source_order_id == Order.id)
            .where(
                Order.status.in_(("paid", "refund_pending")),
                or_(Entitlement.id.is_(None), Entitlement.status.in_(("pending", "failed"))),
            )
            .order_by(Order.id)
            .limit(limit)
        )
        if order_id is not None:
            query = query.where(Order.id == order_id)
        return list(session.execute(query).scalars().all())


def reconcile_order_ids(
    order_ids: list[UUID], factory: sessionmaker, *, permit: RecoveryPermit | None = None
) -> ReconciliationResult:
    """Process an explicitly bounded snapshot; a failure in one order does not stop the next."""
    if not 1 <= len(order_ids) <= 100:
        raise ValueError("Recovery batch must contain 1–100 orders")
    if not isinstance(permit, RecoveryPermit):
        raise PermissionError("Explicit Recovery permit required")
    permit.consume(factory, tuple(order_ids))
    attempted = activated = rejected = failed = 0
    for order_id in order_ids:
        attempted += 1
        try:
            result = grant_paid_order(order_id, factory)
            if result.source_order_id != order_id or result.status != "active":
                raise EntitlementGrantUnavailable("Entitlement is not active")
            activated += 1
        except IneligibleEntitlementOrder:
            rejected += 1
            logging.warning("Entitlement recovery rejected order %s", order_id)
        except EntitlementGrantUnavailable:
            failed += 1
            logging.warning("Entitlement recovery failed for order %s", order_id)
    return ReconciliationResult(attempted, activated, rejected, failed)


def reconcile_paid_orders(factory: sessionmaker | None = None, batch_size: int = 100) -> ReconciliationResult:
    """Disabled legacy batch entrypoint; use the validated, bounded Recovery CLI."""
    raise RuntimeError("Legacy batch recovery is disabled; use the guarded Recovery CLI")
