"""ECPay Stage server-to-server payment-result notification."""

import hmac
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_session_factory
from app.models import Order
from app.services.ecpay_stage import check_mac_value, get_stage_config
from app.services.pricing import PRODUCT_CODE


router = APIRouter()
ACK = "1|OK"


def _notification_fields(body: bytes, content_type: str) -> dict[str, str]:
    if content_type.split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded" or len(body) > 16_384:
        raise HTTPException(status_code=400, detail="Invalid notification")
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True,
                          strict_parsing=True, max_num_fields=100, errors="strict")
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=400, detail="Invalid notification") from None
    fields = dict(pairs)
    if len(fields) != len(pairs) or not all(fields.get(key) for key in (
        "CheckMacValue", "MerchantID", "MerchantTradeNo", "TradeAmt", "RtnCode"
    )):
        raise HTTPException(status_code=400, detail="Invalid notification")
    return fields


def _paid_details(fields: dict[str, str]) -> tuple[str, str, datetime]:
    trade_no = fields.get("TradeNo", "")
    payment_type = fields.get("PaymentType", "")
    if not re.fullmatch(r"[A-Za-z0-9]{1,20}", trade_no) or not payment_type or len(payment_type) > 50:
        raise HTTPException(status_code=400, detail="Invalid notification")
    try:
        paid_at = datetime.strptime(fields["PaymentDate"], "%Y/%m/%d %H:%M:%S")
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid notification") from None
    return trade_no, payment_type, paid_at.replace(tzinfo=ZoneInfo("Asia/Taipei"))


@router.post("/payments/ecpay/return", include_in_schema=False)
async def receive_ecpay_stage_result(request: Request) -> Response:
    """Only authenticated, matching notifications receive ECPay's exact acknowledgement."""
    fields = _notification_fields(await request.body(), request.headers.get("content-type", ""))
    try:
        config = get_stage_config()
    except ValueError:
        raise HTTPException(status_code=503, detail="Payment notification unavailable") from None

    mac = fields.pop("CheckMacValue")
    if not re.fullmatch(r"[0-9A-Fa-f]{64}", mac) or not hmac.compare_digest(
        mac.upper(), check_mac_value(fields, config)
    ):
        raise HTTPException(status_code=403, detail="Invalid notification")
    if fields["MerchantID"] != config.merchant_id or not re.fullmatch(
        r"[A-Za-z0-9]{1,20}", fields["MerchantTradeNo"]
    ) or not re.fullmatch(r"[0-9]+", fields["TradeAmt"]) or not re.fullmatch(
        r"[0-9]+", fields["RtnCode"]
    ) or fields.get("SimulatePaid", "0") not in ("0", "1"):
        raise HTTPException(status_code=400, detail="Invalid notification")

    success = fields["RtnCode"] == "1" and fields.get("SimulatePaid", "0") == "0"
    paid_details = _paid_details(fields) if success else None

    try:
        with get_session_factory()() as session:
            try:
                order = session.execute(
                    select(Order).where(Order.provider_order_id == fields["MerchantTradeNo"])
                    .with_for_update()
                ).scalar_one_or_none()
                if (order is None or order.product_code != PRODUCT_CODE or
                    order.currency != "TWD" or type(order.amount) is not int or
                    order.amount <= 0 or str(order.amount) != fields["TradeAmt"] or
                    order.email_verified_at is None):
                    raise HTTPException(status_code=409, detail="Payment notification does not match order")

                if success:
                    trade_no, payment_type, provider_paid_at = paid_details
                    if order.status == "paid":
                        if (order.provider_trade_no != trade_no or
                            order.payment_environment != "ecpay_stage" or
                            order.provider_payment_type != payment_type or
                            order.provider_paid_at != provider_paid_at):
                            raise HTTPException(status_code=409, detail="Conflicting payment notification")
                    elif order.status in ("pending", "expired") and order.provider_trade_no is None:
                        # expires_at gates checkout, not authenticated payment results.
                        order.status = "paid"
                        order.paid_at = datetime.now(timezone.utc)
                        order.provider_trade_no = trade_no
                        order.payment_environment = "ecpay_stage"
                        order.provider_payment_type = payment_type
                        order.provider_paid_at = provider_paid_at
                        session.commit()
                    else:
                        raise HTTPException(status_code=409, detail="Conflicting payment notification")
                elif order.status not in ("pending", "expired", "paid"):
                    raise HTTPException(status_code=409, detail="Conflicting payment notification")
            except SQLAlchemyError:
                session.rollback()
                raise
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Payment notification unavailable") from None
    return Response(content=ACK, media_type="text/plain")
