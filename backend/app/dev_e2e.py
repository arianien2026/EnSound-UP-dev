"""Opt-in, short-lived operator gate for one synthetic Development mailbox.

Does not enable database writes. Deployment/configuration require separate approval.
Never place the operator key in a learner build, URL, email, or log.
"""
import hmac
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url

from app.database import get_database_url
from app.services.ecpay_stage import get_stage_config
from app.dev_frontend import dev_frontend_url


DEV_HOST = "ep-sparkling-smoke-b80uupgp.c-14.us-east-1.aws.neon.tech"
DEV_CALLBACK = "https://ensound-up-dev.onrender.com/payments/ecpay/return"


@dataclass(frozen=True, repr=False)
class DevE2EScope:
    email: str
    run_id: str
    key: str
    expires_at: datetime
    buyer: bool = False


def configured_scope() -> DevE2EScope | None:
    flag = os.environ.get("ENSOUND_DEV_E2E_ENABLED", "0")
    if flag == "0":
        return None
    if flag != "1":
        raise ValueError("Development E2E scope unavailable")
    try:
        run_id = os.environ["ENSOUND_DEV_E2E_RUN_ID"]
        email = os.environ["ENSOUND_DEV_E2E_EMAIL"]
        key = os.environ["ENSOUND_DEV_E2E_KEY"]
        expiry = datetime.fromisoformat(os.environ["ENSOUND_DEV_E2E_EXPIRES_AT"])
        now = datetime.now(timezone.utc)
        url = make_url(get_database_url())
        stage = get_stage_config()
        buyer_flag = os.environ.get("ENSOUND_DEV_E2E_BUYER_ENABLED", "0")
        if buyer_flag not in {"0", "1"} or (buyer_flag == "1" and not dev_frontend_url()):
            raise ValueError()
        if (not re.fullmatch(r"E2E-[a-zA-Z0-9]{12,32}", run_id)
                or email != email.strip() or email.count("@") != 1 or len(email) > 254
                or run_id.lower() not in email.split("@")[0].lower()
                or not re.fullmatch(r"[a-zA-Z0-9_-]{32,256}", key)
                or expiry.tzinfo is None or not now < expiry <= now + timedelta(hours=24)
                or os.environ.get("ENSOUND_DEV_DATABASE_HOST") != DEV_HOST
                or os.environ.get("ENSOUND_DEV_DATABASE_NAME") != "neondb"
                or url.host != DEV_HOST or url.database != "neondb"
                or (url.port or 5432) != 5432 or url.query.get("sslmode") != "verify-full"
                or "options" in url.query
                or stage.merchant_id not in {"3002607", "2000132"}
                or stage.return_url != DEV_CALLBACK):
            raise ValueError()
    except Exception:
        raise ValueError("Development E2E scope unavailable") from None
    return DevE2EScope(email, run_id, key, expiry, buyer_flag == "1")


def require_scoped_email(email: str) -> None:
    scope = configured_scope()
    if scope and email != scope.email:
        raise HTTPException(status_code=403, detail="Outside Development E2E scope")


async def development_e2e_gate(request: Request, call_next):
    try:
        scope = configured_scope()
    except ValueError:
        # Health remains readable; every business route fails closed after expiry.
        if request.method == "GET" and request.url.path in {"/health", "/health/db"}:
            return await call_next(request)
        return JSONResponse({"detail": "Development E2E scope unavailable"}, status_code=503)
    if scope and request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.method == "POST" and request.url.path == "/payments/ecpay/return":
            # Provider cannot send the operator header. Existing MAC verification
            # plus the order-email scope check in the callback remain mandatory.
            return await call_next(request)
        supplied = request.headers.get("X-EnSound-E2E-Key", "")
        operator = hmac.compare_digest(supplied.encode(), scope.key.encode())
        buyer_paths = {"/orders", "/orders/verify-email", "/access/email-links",
                       "/access/email-links/redeem", "/access/devices"}
        checkout = bool(re.fullmatch(r"/orders/[0-9a-fA-F-]{36}/checkout", request.url.path))
        frontend = dev_frontend_url()
        from urllib.parse import urlsplit
        front_origin = (f"{urlsplit(frontend).scheme}://{urlsplit(frontend).netloc}" if frontend else "")
        proof_path = request.url.path in {"/orders/verify-email", "/access/email-links/redeem"}
        allowed_origins = {front_origin} | ({"https://ensound-up-dev.onrender.com"} if proof_path else set())
        buyer = (scope.buyer and request.method == "POST" and
                 (request.url.path in buyer_paths or checkout) and
                 request.headers.get("Origin", "") in allowed_origins)
        if not operator and not buyer:
            return JSONResponse({"detail": "Development E2E operator required"}, status_code=403)
        if request.url.path in {"/orders", "/access/email-links", "/access/device-release/email-links"}:
            try:
                body = await request.json()
                if not isinstance(body, dict) or body.get("email", "").strip() != scope.email:
                    raise ValueError()
            except Exception:
                return JSONResponse({"detail": "Outside Development E2E scope"}, status_code=403)
    return await call_next(request)
