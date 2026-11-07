"""Idempotent entitlement grant after a verified ECPay Stage paid order.

The recovered callback invokes this after the payment transaction commits.
No public endpoint grants access directly; internal recovery requires a permit.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.database import get_session_factory
from app.dev_e2e import require_scoped_email
from app.models import Entitlement, Order
from app.services.pricing import PRODUCT_CODE


class IneligibleEntitlementOrder(Exception):
    pass


class EntitlementGrantUnavailable(Exception):
    pass


@dataclass(frozen=True)
class GrantedEntitlement:
    id: UUID
    source_order_id: UUID
    status: str


def _eligible_order(session: Session, order_id: UUID) -> Order:
    # Only the trusted, authenticated Stage callback currently writes this
    # combination of paid status and provider payment identity. DB writers are
    # inside the trust boundary; an email or browser claim is never proof.
    order = session.execute(
        select(Order).where(Order.id == order_id).with_for_update()
    ).scalar_one_or_none()
    if (
        order is None
        or order.status not in ("paid", "refund_pending")
        or order.paid_at is None
        or order.email_verified_at is None
        or not order.entitlement_email
        or order.product_code != PRODUCT_CODE
        or order.currency != "TWD"
        or type(order.amount) is not int
        or order.amount <= 0
        or not order.provider_order_id
        or not order.provider_trade_no
        or order.payment_environment != "ecpay_stage"
        or order.provider_paid_at is None
    ):
        raise IneligibleEntitlementOrder("Order is not a verified paid Stage order")
    require_scoped_email(order.entitlement_email)
    return order


def _matching_entitlement(session: Session, order: Order) -> Entitlement | None:
    entitlement = session.execute(
        select(Entitlement).where(Entitlement.source_order_id == order.id).with_for_update()
    ).scalar_one_or_none()
    if entitlement is not None and (
        entitlement.product_code != order.product_code
        or entitlement.entitlement_email != order.entitlement_email
        or entitlement.status == "revoked"
    ):
        raise IneligibleEntitlementOrder("Existing entitlement conflicts with paid order")
    return entitlement


def _granted(entitlement: Entitlement) -> GrantedEntitlement:
    return GrantedEntitlement(entitlement.id, entitlement.source_order_id, entitlement.status)


def _record_failure(factory: sessionmaker, order_id: UUID) -> None:
    # A second transaction can retain the failed state after activation rolls back.
    # If the database itself is unavailable, the paid order without an active
    # entitlement is still a reconciliation target; never claim a successful grant.
    try:
        with factory.begin() as session:
            entitlement = session.execute(
                select(Entitlement).where(Entitlement.source_order_id == order_id).with_for_update()
            ).scalar_one_or_none()
            if entitlement is not None and entitlement.status == "pending":
                entitlement.status = "failed"
                entitlement.grant_failed_at = datetime.now(timezone.utc)
                entitlement.updated_at = entitlement.grant_failed_at
    except (SQLAlchemyError, RuntimeError, ValueError):
        logging.warning("Entitlement grant failure state could not be saved for order %s", order_id)


def grant_paid_order(order_id: UUID, factory: sessionmaker | None = None) -> GrantedEntitlement:
    """Idempotently grant a verified paid Stage order, with no access credential.

    The unique source-order constraint and order row lock serialize concurrent
    attempts on PostgreSQL. A committed pending/failed record identifies an
    incomplete grant for safe retry and reconciliation. Both transactions
    recheck order eligibility so a refund completed between them cannot grant.
    """
    prepared = False
    try:
        if factory is None:
            factory = get_session_factory()
        with factory.begin() as session:
            order = _eligible_order(session, order_id)
            entitlement = _matching_entitlement(session, order)
            if entitlement is None:
                entitlement = Entitlement(
                    source_order_id=order.id,
                    product_code=order.product_code,
                    entitlement_email=order.entitlement_email,
                    status="pending",
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(entitlement)
                session.flush()
            if entitlement.status == "active":
                return _granted(entitlement)
        prepared = True

        with factory.begin() as session:
            order = _eligible_order(session, order_id)
            entitlement = _matching_entitlement(session, order)
            if entitlement is None:
                raise EntitlementGrantUnavailable("Prepared entitlement is missing")
            if entitlement.status in ("pending", "failed"):
                entitlement.status = "active"
                entitlement.activated_at = datetime.now(timezone.utc)
                entitlement.grant_failed_at = None
                entitlement.updated_at = entitlement.activated_at
                session.flush()
            return _granted(entitlement)
    except IneligibleEntitlementOrder:
        raise
    except (SQLAlchemyError, RuntimeError, ValueError, EntitlementGrantUnavailable):
        if prepared and factory is not None:
            _record_failure(factory, order_id)
        logging.warning("Entitlement grant did not complete for order %s", order_id)
        raise EntitlementGrantUnavailable("Entitlement grant unavailable") from None
