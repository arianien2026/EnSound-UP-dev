"""Temporary Claim acceptance on the exact Development database.

The release may include a public verifier and a short time window. The operator
key stays outside the repository and is only sent in the existing HTTPS header.
"""
import hashlib
import hmac
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.database import get_database_url
from app.dev_e2e import DEV_HOST
from app.dev_frontend import dev_frontend_url
from app.models import EmailClaimRateLimit, EmailClaimSession, EmailClaimToken, Entitlement, Order
from app.services.email_claim import _count_request, _rate_key, claim_eligible_query


TEST_EMAIL = "uptools.support@gmail.com"
PAID_TRADE = "ESc559edac5f64452e9a"
SERVICE_ID = "srv-db33dsm7bikc73bhukug"
REPOSITORY = "arianien2026/ensound-up-dev"
FRONTEND = "https://arianien2026.github.io/EnSound-UP-dev/"
RUN_ENV = "ENSOUND_DEV_CLAIM_E2E_RUN"
MAX_WINDOW = timedelta(minutes=30)


@dataclass(frozen=True)
class ClaimRun:
    key_digest: str
    starts_at: datetime
    expires_at: datetime


def active_claim_run(now: datetime) -> ClaimRun | None:
    raw = os.environ.get(RUN_ENV)
    if raw is None:
        return None
    try:
        config = json.loads(raw)
        if not isinstance(config, dict) or set(config) != {"operator_sha256", "starts_at", "expires_at"}:
            raise ValueError("Invalid Claim run metadata")
        run = ClaimRun(
            config["operator_sha256"], datetime.fromisoformat(config["starts_at"]),
            datetime.fromisoformat(config["expires_at"]),
        )
        if (not re.fullmatch(r"[0-9a-f]{64}", run.key_digest)
                or run.starts_at.tzinfo is None or run.expires_at.tzinfo is None
                or now.tzinfo is None
                or not timedelta(0) < run.expires_at - run.starts_at <= MAX_WINDOW
                or not run.starts_at <= now < run.expires_at
                or os.environ.get("RENDER_SERVICE_ID") != SERVICE_ID
                or os.environ.get("RENDER_GIT_REPO_SLUG", "").casefold() != REPOSITORY
                or os.environ.get("RENDER_GIT_BRANCH") != "main"
                or os.environ.get("ENSOUND_DEV_E2E_ENABLED", "0") != "0"
                or os.environ.get("EMAIL_TRANSPORT") != "gmail_api"
                or os.environ.get("GMAIL_FROM_EMAIL") != TEST_EMAIL
                or os.environ.get("ENSOUND_DEV_DATABASE_HOST") != DEV_HOST
                or os.environ.get("ENSOUND_DEV_DATABASE_NAME") != "neondb"
                or dev_frontend_url() != FRONTEND):
            raise ValueError("Invalid Claim run scope")
        url = make_url(get_database_url())
        if (url.host != DEV_HOST or url.database != "neondb" or (url.port or 5432) != 5432
                or url.query.get("sslmode") != "verify-full" or "options" in url.query):
            raise ValueError("Invalid Claim database scope")
    except (KeyError, TypeError, ValueError, RuntimeError):
        # An installed but broken/expired run must not fall back to the
        # ordinary route, even if the database default later changes.
        raise HTTPException(status_code=503, detail="Development Claim run unavailable") from None
    return run


def require_claim_operator(run: ClaimRun, supplied: str, recipient: str) -> None:
    if (recipient != TEST_EMAIL or not re.fullmatch(r"[A-Za-z0-9_-]{43}", supplied)
            or not hmac.compare_digest(hashlib.sha256(supplied.encode("ascii")).hexdigest(), run.key_digest)):
        raise HTTPException(status_code=403, detail="Development Claim operator required")


def claim_attempt_key(run: ClaimRun, rate_key: bytes) -> str:
    return _rate_key(rate_key, "dev_claim_e2e", run.key_digest)


def verify_claim_runtime(factory, run: ClaimRun, rate_key: bytes) -> bool:
    with factory() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        driver = session.connection().connection.driver_connection
        if (driver.info.host != DEV_HOST or driver.info.dbname != "neondb"
                or not driver.pgconn.ssl_in_use
                or driver.info.get_parameters().get("sslmode") != "verify-full"
                or session.scalar(text("SHOW default_transaction_read_only")) != "on"
                or session.scalar(text("SHOW transaction_read_only")) != "on"):
            return False
        eligible = session.scalar(claim_eligible_query(TEST_EMAIL, PAID_TRADE))
        unused = session.get(EmailClaimRateLimit, claim_attempt_key(run, rate_key)) is None
        return eligible is not None and unused


def reserve_claim_email(session, run: ClaimRun, now: datetime, rate_key: bytes) -> bool:
    # Existing row locking and uniqueness make this one-shot across workers and
    # restarts. The window closes well before this rate-limit bucket can reset.
    return _count_request(session, claim_attempt_key(run, rate_key), now, 1)


def require_run_proof(factory, run: ClaimRun, credential: str, now: datetime,
                      *, registration: bool = False) -> None:
    model = EmailClaimSession if registration else EmailClaimToken
    digest_column = model.session_hash if registration else model.token_hash
    scope_column = model.scope if registration else model.purpose
    with factory() as session:
        valid = session.scalar(
            select(model.id)
            .join(Entitlement, model.entitlement_id == Entitlement.id)
            .join(Order, Entitlement.source_order_id == Order.id)
            .where(digest_column == hashlib.sha256(credential.encode("ascii")).hexdigest(),
                   scope_column == "device_claim", model.consumed_at.is_(None),
                   model.created_at >= run.starts_at, model.created_at < run.expires_at,
                   model.expires_at > now, Entitlement.status == "active",
                   Entitlement.entitlement_email == TEST_EMAIL,
                   Order.entitlement_email == TEST_EMAIL, Order.provider_order_id == PAID_TRADE,
                   Order.status == "paid", Order.payment_environment == "ecpay_stage",
                   Order.email_verified_at.is_not(None))
        )
    if valid is None:
        raise HTTPException(status_code=401 if registration else 400,
                            detail="Invalid or expired access link")


@contextmanager
def claim_transaction(factory, run: ClaimRun | None):
    with factory.begin() as session:
        if run is not None:
            if not datetime.now(timezone.utc) < run.expires_at:
                raise HTTPException(status_code=403, detail="Development Claim window expired")
            # Must be the first SQL command in this transaction. The pool
            # retains its normal default on commit, rollback or checkout.
            session.execute(text("SET TRANSACTION READ WRITE"))
            if session.scalar(text("SHOW default_transaction_read_only")) != "on":
                raise RuntimeError("Development Claim requires read-only session defaults")
        yield session
        if run is not None and not datetime.now(timezone.utc) < run.expires_at:
            raise HTTPException(status_code=403, detail="Development Claim window expired")
