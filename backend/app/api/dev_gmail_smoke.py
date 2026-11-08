"""Temporary Development smoke test. No database imports, token issuance or retry."""
import base64
from datetime import datetime, timezone
import os
import re
import secrets
import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from app.services.email_delivery import EmailDeliveryError, get_email_config, _deliver

router = APIRouter()
RECIPIENT = "uptools.support@gmail.com"
PATH = "/internal/development/gmail-smoke"
SUBJECT = "EnSound UP Development Gmail Test"
TEXT = "Development-only Gmail API transport test. No order, payment, verification token, claim token or access grant is included. No action is required."


class SignatureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signature: str = Field(min_length=88, max_length=88)


class OneBootCapability:
    def __init__(self):
        self.boot = secrets.token_hex(32)
        self.used = False
        self.lock = threading.Lock()

    def settings(self):
        try:
            expiry = datetime.fromisoformat(os.environ.get("ENSOUND_GMAIL_SMOKE_EXPIRES_AT", ""))
            now = datetime.now(timezone.utc)
            run = os.environ.get("ENSOUND_GMAIL_SMOKE_RUN_ID", "")
            public = bytes.fromhex(os.environ.get("ENSOUND_GMAIL_SMOKE_PUBLIC_KEY", ""))
            if (os.environ.get("ENSOUND_GMAIL_SMOKE_ENABLED", "0") != "1"
                    or os.environ.get("RENDER_SERVICE_ID") != "srv-db33dsm7bikc73bhukug"
                    or os.environ.get("EMAIL_TRANSPORT") != "gmail_api"
                    or expiry.tzinfo is None or not 0 < (expiry - now).total_seconds() <= 3600
                    or not re.fullmatch(r"[a-f0-9]{32}", run) or len(public) != 32):
                raise ValueError()
            return run, public
        except Exception:
            raise HTTPException(404, "Not available") from None

    def message(self, run):
        return ("EnSound-Gmail-Smoke-v1\n" + run + "\n" + self.boot).encode("ascii")

    def consume(self, signature):
        run, public = self.settings()
        try:
            raw = base64.b64decode(signature, validate=True)
            Ed25519PublicKey.from_public_bytes(public).verify(raw, self.message(run))
        except Exception:
            raise HTTPException(403, "Invalid operation proof") from None
        with self.lock:
            if self.used:
                raise HTTPException(409, "Attempt already consumed")
            self.used = True  # Consume BEFORE refresh/send, including failures/timeouts.


capability = OneBootCapability()


@router.get(PATH, include_in_schema=False)
def challenge():
    run, _ = capability.settings()
    return {"run_id": run, "boot_nonce": capability.boot}


@router.post(PATH, include_in_schema=False)
def send_once(body: SignatureRequest):
    capability.consume(body.signature)
    try:
        config = get_email_config()
        if (config.transport != "gmail_api" or config.sender != RECIPIENT
                or config.base_url != "https://ensound-up-dev.onrender.com"):
            return {"result": "FAIL", "phase": "configuration", "http_status": None,
                    "send_attempts": 0, "message_id": None}
        result = _deliver({"from": RECIPIENT, "to": [RECIPIENT],
                           "subject": SUBJECT, "text": TEXT}, config)
        return {"result": "SEND_PASS", "phase": "gmail_send", **result,
                "send_attempts": 1}
    except EmailDeliveryError as error:
        return {"result": "FAIL", "phase": error.phase,
                "http_status": error.http_status, "message_id": None,
                "send_attempts": 1 if error.phase == "gmail_send" else 0}
