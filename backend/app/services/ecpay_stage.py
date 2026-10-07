"""ECPay All-in-One Stage checkout fields and shared checksum."""

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote_plus, urlsplit
from zoneinfo import ZoneInfo

from app.models import Order
from app.services.pricing import PRODUCT_CODE


STAGE_CHECKOUT_URL = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"


@dataclass(frozen=True)
class StageConfig:
    merchant_id: str
    hash_key: str
    hash_iv: str
    return_url: str


def get_stage_config() -> StageConfig:
    config = StageConfig(
        merchant_id=os.environ.get("ECPAY_STAGE_MERCHANT_ID", "").strip(),
        hash_key=os.environ.get("ECPAY_STAGE_HASH_KEY", ""),
        hash_iv=os.environ.get("ECPAY_STAGE_HASH_IV", ""),
        return_url=os.environ.get("ECPAY_STAGE_RETURN_URL", "").strip(),
    )
    url = urlsplit(config.return_url)
    if (
        not re.fullmatch(r"[A-Za-z0-9]{1,10}", config.merchant_id)
        or len(config.hash_key) != 16
        or len(config.hash_iv) != 16
        or any(not 33 <= ord(char) <= 126 for char in config.hash_key + config.hash_iv)
        or url.scheme != "https"
        or not url.hostname
        or url.hostname in {"localhost", "127.0.0.1", "::1"}
        or url.username is not None
        or url.password is not None
        or url.fragment
        or len(config.return_url) > 200
    ):
        raise ValueError("ECPay Stage configuration unavailable")
    try:
        url.port  # Reject malformed port syntax without exposing the URL.
    except ValueError:
        raise ValueError("ECPay Stage configuration unavailable") from None
    return config


def check_mac_value(fields: dict[str, str], config: StageConfig) -> str:
    # ECPay All-in-One: sort fields, wrap with HashKey/HashIV, .NET-style
    # form URL encoding, lowercase, SHA-256, uppercase hex. CheckMacValue
    # itself must not be included in the checksum input.
    payload = "&".join(
        f"{key}={value}" for key, value in sorted(fields.items()) if key != "CheckMacValue"
    )
    wrapped = f"HashKey={config.hash_key}&{payload}&HashIV={config.hash_iv}"
    # Python always leaves RFC 3986's unreserved '~' literal, even when it
    # is omitted from `safe`. ECPay's .NET URL-encoding table requires %7e.
    encoded = quote_plus(wrapped, safe="-_.!*()").replace("~", "%7e").lower()
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest().upper()


def merchant_trade_no(order: Order) -> str:
    # One trade number per order, persisted under the existing unique DB
    # constraint. The UUID-derived suffix avoids timestamp collisions.
    return "ES" + order.id.hex[:18]


def checkout_fields(order: Order, trade_no: str, now: datetime, config: StageConfig) -> dict[str, str]:
    if (
        order.product_code != PRODUCT_CODE
        or order.currency != "TWD"
        or type(order.amount) is not int
        or order.amount <= 0
    ):
        raise ValueError("Invalid order for ECPay Stage checkout")
    fields = {
        "MerchantID": config.merchant_id,
        "MerchantTradeNo": trade_no,
        "MerchantTradeDate": now.astimezone(ZoneInfo("Asia/Taipei")).strftime(
            "%Y/%m/%d %H:%M:%S"
        ),
        "PaymentType": "aio",
        "TotalAmount": str(order.amount),
        "TradeDesc": "EnSound UP Full Access",
        "ItemName": "EnSound UP Web Full Access",
        "ReturnURL": config.return_url,
        "ChoosePayment": "ALL",
        "EncryptType": "1",
    }
    fields["CheckMacValue"] = check_mac_value(fields, config)
    return fields
