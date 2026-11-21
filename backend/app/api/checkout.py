from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_session_factory
from app.dev_e2e import require_scoped_email
from app.models import Order
from app.services.ecpay_stage import (
    STAGE_ENVIRONMENT,
    checkout_enabled,
    checkout_fields,
    get_payment_config,
    merchant_trade_no,
)
from app.services.email_verification import is_payment_eligible


router = APIRouter()


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckoutResponse(BaseModel):
    checkout_url: str
    fields: dict[str, str]


@router.post("/orders/{order_id}/checkout", response_model=CheckoutResponse)
def create_stage_checkout(
    order_id: UUID, request: CheckoutRequest | None = Body(default=None)
) -> CheckoutResponse:
    # An optional empty body allows callers to send just the order ID while
    # rejecting attempted price, product, credential, or URL overrides.
    try:
        config = get_payment_config()
        if not checkout_enabled(config.environment):
            raise HTTPException(status_code=503, detail="Checkout unavailable")
    except ValueError:
        raise HTTPException(status_code=503, detail="Checkout unavailable") from None

    try:
        with get_session_factory()() as session:
            try:
                order = session.execute(
                    select(Order).where(Order.id == order_id).with_for_update()
                ).scalar_one_or_none()
                now = datetime.now(timezone.utc)
                if (not is_payment_eligible(order, now)
                        or (order.payment_environment or STAGE_ENVIRONMENT) != config.environment
                        or order.checkout_merchant_id not in (None, config.merchant_id)):
                    raise HTTPException(status_code=409, detail="Order is not eligible for checkout")
                require_scoped_email(order.entitlement_email)
                trade_no = order.provider_order_id or merchant_trade_no(order)
                if order.checkout_started_at is None:
                    order.checkout_started_at = now
                    order.checkout_merchant_id = config.merchant_id
                    order.payment_environment = config.environment
                    order.provider_order_id = trade_no
                started = order.checkout_started_at
                if started.tzinfo is None:  # SQLite fixtures strip timezone offsets.
                    started = started.replace(tzinfo=timezone.utc)
                fields = checkout_fields(order, trade_no, started, config)
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                raise
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Checkout unavailable") from None
    return CheckoutResponse(checkout_url=config.checkout_url, fields=fields)
