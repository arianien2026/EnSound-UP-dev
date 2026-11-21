"""Record an operator-verified full refund. This module makes no provider calls."""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.models import Entitlement, Order, RefundEvent
from app.services.ecpay_stage import StageConfig
from app.services.pricing import PRODUCT_CODE


class RefundEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: UUID
    merchant_id: str = Field(pattern=r"^[A-Za-z0-9]{1,10}$")
    merchant_trade_no: str = Field(pattern=r"^[A-Za-z0-9]{1,20}$")
    provider_trade_no: str = Field(pattern=r"^[A-Za-z0-9]{1,20}$")
    amount: int = Field(gt=0, strict=True)
    currency: Literal["TWD"]
    reference: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,96}$")
    outcome: Literal["pending", "failed", "uncertain", "confirmed"]
    requested_at: AwareDatetime
    evidence_at: AwareDatetime


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def record_refund(factory, config: StageConfig, evidence: RefundEvidence, actor: str,
                  now: datetime, *, apply: bool = False) -> str:
    """Dry run by default. Commit outcome, audit and revocation atomically."""
    if not actor or len(actor) > 64 or not actor.isascii() or not all(
        c.isalnum() or c in "._-" for c in actor
    ):
        raise ValueError("Invalid operator")
    if not evidence.requested_at <= evidence.evidence_at <= now:
        raise ValueError("Invalid refund evidence timeline")
    digest = hashlib.sha256(evidence.model_dump_json().encode()).hexdigest()
    with factory.begin() as session:
        order = session.get(Order, evidence.order_id, with_for_update=apply)
        if (order is None or order.product_code != PRODUCT_CODE
                or order.status not in {"paid", "refund_pending", "refunded"}
                or order.provider_paid_at is None or order.paid_at is None
                or order.email_verified_at is None
                or order.payment_environment != config.environment
                or order.currency != evidence.currency or order.amount != evidence.amount
                or order.provider_order_id != evidence.merchant_trade_no
                or order.provider_trade_no != evidence.provider_trade_no
                or config.merchant_id != evidence.merchant_id
                or order.checkout_merchant_id != evidence.merchant_id):
            raise ValueError("Refund payment identity mismatch")
        paid_at = _utc(order.provider_paid_at)
        if not paid_at <= evidence.requested_at <= paid_at + timedelta(days=7):
            raise ValueError("Refund request is outside the seven-day policy")
        existing = session.scalar(select(RefundEvent).where(
            RefundEvent.order_id == order.id, RefundEvent.reference == evidence.reference,
        ))
        if existing is not None:
            if existing.evidence_digest != digest:
                raise ValueError("Refund evidence reference has conflicting contents")
            return "already_recorded"
        if order.status == "refunded":
            raise ValueError("Refund already closed")
        requested = session.scalar(select(func.min(RefundEvent.requested_at)).where(
            RefundEvent.order_id == order.id,
        ))
        if requested is not None and _utc(requested) != _utc(evidence.requested_at):
            raise ValueError("Refund request timestamp differs from the audit trail")
        if evidence.outcome == "confirmed" and order.status != "refund_pending":
            raise ValueError("Record the refund request before confirmation")
        if not apply:
            return "eligible_dry_run"
        target = "refunded" if evidence.outcome == "confirmed" else "refund_pending"
        if target == "refunded":
            entitlement = session.scalar(select(Entitlement).where(
                Entitlement.source_order_id == order.id,
            ).with_for_update())
            if entitlement is not None and entitlement.status == "active":
                entitlement.status = "revoked"
                entitlement.revoked_at = now
                entitlement.updated_at = now
                entitlement.revocation_reason = "provider_refund_confirmed"
        order.status = target
        session.add(RefundEvent(
            order_id=order.id, reference=evidence.reference, outcome=evidence.outcome,
            actor=actor, evidence_digest=digest, requested_at=_utc(evidence.requested_at),
            evidence_at=_utc(evidence.evidence_at), recorded_at=now,
        ))
        return target


def refund_followups(factory, environment: str, now: datetime) -> tuple[UUID, ...]:
    """Read-only queue for manual follow-up. No access state is changed."""
    with factory() as session:
        return tuple(session.scalars(
            select(Order.id).join(RefundEvent, RefundEvent.order_id == Order.id)
            .where(Order.status == "refund_pending", Order.payment_environment == environment)
            .group_by(Order.id)
            .having(func.min(RefundEvent.requested_at) < now - timedelta(days=14))
            .order_by(Order.id)
        ))
