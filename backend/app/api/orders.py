from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_session_factory
from app.models import Order
from app.services.pricing import PRODUCT_CODE, get_price


router = APIRouter()


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def trim_email(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class CreateOrderResponse(BaseModel):
    order_id: UUID
    entitlement_email: str
    product_code: str
    amount: int
    currency: str
    pricing_code: str
    status: str
    created_at: datetime
    expires_at: datetime


@router.post("/orders", response_model=CreateOrderResponse, status_code=201)
def create_order(request: CreateOrderRequest) -> CreateOrderResponse:
    created_at = datetime.now(timezone.utc)
    price = get_price(now=created_at)
    order = Order(
        entitlement_email=str(request.email),
        product_code=PRODUCT_CODE,
        amount=price.amount,
        currency=price.currency,
        pricing_code=price.pricing_code,
        status="pending",
        created_at=created_at,
        expires_at=created_at + timedelta(hours=2),
        paid_at=None,
        provider_order_id=None,
    )
    try:
        with get_session_factory()() as session:
            try:
                session.add(order)
                session.flush()
                response = CreateOrderResponse(
                    order_id=order.id,
                    entitlement_email=order.entitlement_email,
                    product_code=order.product_code,
                    amount=order.amount,
                    currency=order.currency,
                    pricing_code=order.pricing_code,
                    status=order.status,
                    created_at=order.created_at,
                    expires_at=order.expires_at,
                )
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                raise
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Order service unavailable") from None
    return response
