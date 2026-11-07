from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_session_factory
from app.dev_e2e import require_scoped_email
from app.dev_e2e import configured_scope
from app.services.email_claim import _count_request, _rate_key, get_rate_limit_key
from app.dev_frontend import dev_frontend_url
from html import escape
from app.models import Order
from app.services.email_delivery import (
    EmailDeliveryError,
    get_email_config,
    send_verification_email,
)
from app.services.email_verification import consume_verification_token, issue_verification_token
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


class VerifyEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/orders", response_model=CreateOrderResponse, status_code=201)
def create_order(request: CreateOrderRequest) -> CreateOrderResponse:
    require_scoped_email(str(request.email))
    try:
        email_config = get_email_config()
    except EmailDeliveryError:
        raise HTTPException(status_code=503, detail="Verification email unavailable") from None
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
    token = issue_verification_token(order, created_at)
    try:
        with get_session_factory()() as session:
            try:
                session.add(order)
                scope = configured_scope()
                if scope and scope.buyer and not _count_request(session,
                    _rate_key(get_rate_limit_key(), "buyer_order", scope.email), created_at, 3):
                    raise HTTPException(status_code=429, detail="Try again later")
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
    try:
        send_verification_email(response.entitlement_email, token, email_config)
    except EmailDeliveryError:
        # The pending order remains unverified and expires normally; a new POST can retry.
        raise HTTPException(status_code=503, detail="Verification email unavailable") from None
    return response


@router.get("/orders/{order_id}/status")
def order_status(order_id: UUID) -> Response:
    from fastapi.responses import JSONResponse
    try:
        with get_session_factory()() as session:
            order = session.get(Order, order_id)
            if order is None:
                raise HTTPException(status_code=404, detail="Order unavailable")
            require_scoped_email(order.entitlement_email)
            now = datetime.now(timezone.utc)
            expiry = order.expires_at if order.expires_at.tzinfo else order.expires_at.replace(tzinfo=timezone.utc)
            status = "expired" if order.status == "pending" and expiry <= now else order.status
            return JSONResponse({"status": status, "email_verified": order.email_verified_at is not None},
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Order status unavailable") from None


_VERIFY_PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>EnSound UP email verification</title></head><body>
<main><h1>EnSound UP</h1><p id="result-zh">正在驗證 Email...</p>
<p id="result-zh-detail" hidden></p><p id="result-en" lang="en">Verifying your email...</p></main>
__RETURN_LINK__<script src="/orders/verify-email.js" defer></script></body></html>"""

_VERIFY_SCRIPT = """(async () => {
  const token = new URLSearchParams(location.hash.slice(1)).get('token');
  history.replaceState(null, '', location.pathname);
  const zh = document.getElementById('result-zh');
  const detail = document.getElementById('result-zh-detail');
  const en = document.getElementById('result-en');
  const showMessage = (heading, description, english) => {
    zh.textContent = heading;
    detail.textContent = description;
    detail.hidden = !description;
    en.textContent = english;
  };
  const showInvalid = () => showMessage('驗證連結無效或已過期', '請重新取得 Email 驗證連結。',
    'This verification link is invalid or has expired.');
  if (!token) { showInvalid(); return; }
  try {
    const response = await fetch('/orders/verify-email', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token}), cache: 'no-store'
    });
    if (response.ok) {
      showMessage('Email 驗證完成', '您的 Email 已確認，可以返回 EnSound UP。',
        'Email confirmed. You may return to EnSound UP.');
    } else { showInvalid(); }
  } catch (_) {
    showMessage('目前無法完成驗證', '請稍後再試。',
      'Verification is unavailable. Please try again later.');
  }
})();"""

_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
}


@router.get("/orders/verify-email", response_class=HTMLResponse)
def verification_page() -> HTMLResponse:
    try:
        destination = dev_frontend_url()
    except ValueError:
        destination = None
    link = (f'<p><a href="{escape(destination, quote=True)}">返回 EnSound UP / Return to EnSound UP</a></p>'
            if destination else "")
    return HTMLResponse(_VERIFY_PAGE.replace("__RETURN_LINK__", link), headers=_PAGE_HEADERS)


@router.get("/orders/verify-email.js")
def verification_script() -> Response:
    return Response(_VERIFY_SCRIPT, media_type="application/javascript", headers=_PAGE_HEADERS)


@router.post("/orders/verify-email")
def verify_email(request: VerifyEmailRequest) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    try:
        with get_session_factory()() as session:
            try:
                if not consume_verification_token(session, request.token, now):
                    raise HTTPException(status_code=400, detail="Invalid or expired verification link")
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                raise
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Verification unavailable") from None
    return {"status": "email_verified"}
