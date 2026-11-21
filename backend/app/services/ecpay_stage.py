"""ECPay All-in-One payment configuration, checkout fields and checksum."""

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote_plus, urlsplit
from zoneinfo import ZoneInfo

from app.models import Order
from app.services.pricing import PRODUCT_CODE
from app.dev_frontend import payment_frontend_url, strict_https_url


STAGE_ENVIRONMENT = "ecpay_stage"
PRODUCTION_ENVIRONMENT = "ecpay_production"
STAGE_CALLBACK = "/payments/ecpay/return"
PRODUCTION_CALLBACK = "/payments/ecpay/production/return"
PRODUCTION_CHECKOUT_URL = "https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5"
STAGE_CHECKOUT_URL = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"


@dataclass(frozen=True)
class StageConfig:
    merchant_id: str
    hash_key: str = field(repr=False)
    hash_iv: str = field(repr=False)
    return_url: str
    environment: str = STAGE_ENVIRONMENT

    @property
    def checkout_url(self) -> str:
        return PRODUCTION_CHECKOUT_URL if self.environment == PRODUCTION_ENVIRONMENT else STAGE_CHECKOUT_URL


def payment_environment() -> str:
    value = os.environ.get("ECPAY_PAYMENT_ENV", "stage")
    if value == "stage":
        return STAGE_ENVIRONMENT
    if (value == "production"
            and not os.environ.get("ENSOUND_DEV_DATABASE_HOST")
            and not os.environ.get("ENSOUND_DEV_FRONTEND_URL")
            and os.environ.get("ENSOUND_DEV_E2E_ENABLED", "0") == "0"
            and os.environ.get("RENDER_SERVICE_ID") != "srv-db33dsm7bikc73bhukug"):
        return PRODUCTION_ENVIRONMENT
    raise ValueError("Payment environment unavailable")


def checkout_enabled(environment: str) -> bool:
    return environment == STAGE_ENVIRONMENT or os.environ.get("ECPAY_PRODUCTION_CHECKOUT_ENABLED") == "1"


def get_payment_config() -> StageConfig:
    environment = payment_environment()
    if environment == STAGE_ENVIRONMENT:
        return get_stage_config()
    config = _load_config("PRODUCTION", environment)
    url = strict_https_url(config.return_url)
    if (url.path != PRODUCTION_CALLBACK or url.query
            or not re.fullmatch(r"[0-9]{1,10}", config.merchant_id)
            or config.merchant_id in {"2000132", "3002607", "2000933"}
            or any(os.environ.get("ECPAY_STAGE_" + key) for key in ("MERCHANT_ID", "HASH_KEY", "HASH_IV", "RETURN_URL"))):
        raise ValueError("Production payment configuration unavailable")
    payment_frontend_url(environment)  # Mandatory before issuing a chargeable checkout.
    return config


def get_stage_config() -> StageConfig:
    return _load_config("STAGE", STAGE_ENVIRONMENT)


def _load_config(prefix: str, environment: str) -> StageConfig:
    config = StageConfig(
        merchant_id=os.environ.get(f"ECPAY_{prefix}_MERCHANT_ID", "").strip(),
        hash_key=os.environ.get(f"ECPAY_{prefix}_HASH_KEY", ""),
        hash_iv=os.environ.get(f"ECPAY_{prefix}_HASH_IV", ""),
        return_url=os.environ.get(f"ECPAY_{prefix}_RETURN_URL", "").strip(),
        environment=environment,
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
        raise ValueError("ECPay configuration unavailable")
    try:
        url.port  # Reject malformed port syntax without exposing the URL.
    except ValueError:
        raise ValueError("ECPay configuration unavailable") from None
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
    return ("EP" if order.payment_environment == PRODUCTION_ENVIRONMENT else "ES") + order.id.hex[:18]


def checkout_fields(order: Order, trade_no: str, now: datetime, config: StageConfig) -> dict[str, str]:
    if (
        order.product_code != PRODUCT_CODE
        or order.currency != "TWD"
        or type(order.amount) is not int
        or order.amount <= 0
    ):
        raise ValueError("Invalid order for ECPay checkout")
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
    destination = payment_frontend_url(config.environment)
    if destination:
        fields["ClientBackURL"] = destination  # Navigation only, never payment proof.
    fields["CheckMacValue"] = check_mac_value(fields, config)
    return fields
