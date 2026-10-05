from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_session_factory
from app.models import Order
from app.services.ecpay_stage import (
    STAGE_CHECKOUT_URL,
    checkout_fields,
    get_stage_config,
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
        config = get_stage_config()
    except ValueError:
        raise HTTPException(status_code=503, detail="Checkout unavailable") from None

    try:
        with get_session_factory()() as session:
            try:
                order = session.execute(
                    select(Order).where(Order.id == order_id).with_for_update()
                ).scalar_one_or_none()
                now = datetime.now(timezone.utc)
                if not is_payment_eligible(order, now):
                    raise HTTPException(status_code=409, detail="Order is not eligible for checkout")
                trade_no = order.provider_order_id or merchant_trade_no(order)
                fields = checkout_fields(order, trade_no, now, config)
                if order.provider_order_id is None:
                    order.provider_order_id = trade_no
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                raise
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Checkout unavailable") from None
    return CheckoutResponse(checkout_url=STAGE_CHECKOUT_URL, fields=fields)
