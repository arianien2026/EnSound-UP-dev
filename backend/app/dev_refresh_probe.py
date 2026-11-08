"""Temporary, opt-in refresh-only background probe. No routes, sends or DB calls.

Public signed approval contains only run/boot identifiers; it cannot authorize
mail or access. The isolated issuer signs one boot and exits without saving its
private key. A restart/new process cannot reuse the previous boot approval.
"""
import base64
from datetime import datetime, timezone
import json
import os
import re
import secrets
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from app.services.email_delivery import get_email_config

PREFIX = "EnSound-Gmail-Refresh-v1"
APPROVAL_URL = "https://api.github.com/repos/arianien2026/EnSound-UP-dev/contents/docs/gmail-refresh-approval.json?ref=main"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Redirect rejected")


def emit(value):
    # Only call with locally constructed allowlisted fields; never provider bodies.
    print("GMAIL_REFRESH_PROBE " + json.dumps(value, separators=(",", ":")), flush=True)


def exchange_only(config):
    """Exactly one POST. Access token is checked in memory and never returned."""
    attempted = False
    try:
        if config.transport != "gmail_api" or config.sender != "uptools.support@gmail.com":
            return {"result": "FAIL", "http_status": None, "error_type": "configuration", "access_token_format_valid": False, "refresh_attempts": 0}
        request = Request(TOKEN_URL, data=urlencode({
            "client_id": config.client_id, "client_secret": config.client_secret,
            "refresh_token": config.refresh_token, "grant_type": "refresh_token",
        }).encode("ascii"), headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        attempted = True
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            status = response.status
            result = json.loads(response.read(65536))
        access = result.get("access_token") if isinstance(result, dict) else None
        valid = (isinstance(access, str) and 0 < len(access) <= 8192
                 and all(33 <= ord(c) <= 126 for c in access)
                 and result.get("token_type", "").lower() == "bearer"
                 and type(result.get("expires_in")) is int and result["expires_in"] > 0
                 and ("scope" not in result or result["scope"].split() == ["https://www.googleapis.com/auth/gmail.send"]))
        return {"result": "PASS" if valid else "FAIL", "http_status": status,
                "error_type": None if valid else "invalid_token_response", "access_token_format_valid": bool(valid), "refresh_attempts": 1}
    except HTTPError as error:
        return {"result": "FAIL", "http_status": error.code,
                "error_type": "oauth_http_error", "access_token_format_valid": False, "refresh_attempts": 1}
    except Exception:
        return {"result": "FAIL", "http_status": None,
                "error_type": "network_or_format_error", "access_token_format_valid": False, "refresh_attempts": int(attempted)}


class Probe:
    def __init__(self):
        self.boot = secrets.token_hex(32)
        self.started = False
        self.consumed = False
        self.lock = threading.Lock()

    def settings(self):
        try:
            if os.environ.get("ENSOUND_REFRESH_PROBE_ENABLED", "0") != "1":
                return None
            expiry = datetime.fromisoformat(os.environ.get("ENSOUND_REFRESH_PROBE_EXPIRES_AT", ""))
            now = datetime.now(timezone.utc)
            run = os.environ.get("ENSOUND_REFRESH_PROBE_RUN_ID", "")
            public = bytes.fromhex(os.environ.get("ENSOUND_REFRESH_PROBE_PUBLIC_KEY", ""))
            if (os.environ.get("RENDER_SERVICE_ID") != "srv-db33dsm7bikc73bhukug"
                    or os.environ.get("EMAIL_TRANSPORT") != "gmail_api"
                    or expiry.tzinfo is None or not 0 < (expiry-now).total_seconds() <= 3600
                    or not re.fullmatch(r"[a-f0-9]{32}", run) or len(public) != 32):
                return None
            return run, public
        except Exception:
            return None

    def consume(self, approval):
        settings = self.settings()
        if not settings:
            return False
        run, public = settings
        try:
            if approval.get("run_id") != run or approval.get("boot_nonce") != self.boot:
                return False
            message = (PREFIX + "\n" + run + "\n" + self.boot).encode("ascii")
            Ed25519PublicKey.from_public_bytes(public).verify(base64.b64decode(approval["signature"], validate=True), message)
        except Exception:
            return False
        with self.lock:
            if self.consumed:
                return False
            self.consumed = True
            return True

    def read_approval(self):
        try:
            request = Request(APPROVAL_URL, headers={"Accept": "application/vnd.github+json", "Cache-Control": "no-cache", "User-Agent": "EnSound-Dev-Refresh-Probe"})
            with build_opener(NoRedirect()).open(request, timeout=3) as response:
                data = json.loads(response.read(16384))
            return json.loads(base64.b64decode(data["content"]).decode("utf-8"))
        except Exception:
            return {}

    def run(self):
        try:
            settings = self.settings()
            if not settings or self.consumed:
                return
            run, _ = settings
            emit({"event": "challenge", "run_id": run, "boot_nonce": self.boot})
            # Poll only public approval metadata. No OAuth retry occurs here.
            for _ in range(60):
                if self.consumed or not self.settings():
                    return
                if self.consume(self.read_approval()):
                    try:
                        outcome = exchange_only(get_email_config())
                    except Exception:
                        outcome = {"result": "FAIL", "http_status": None, "error_type": "configuration", "access_token_format_valid": False, "refresh_attempts": 0}
                    emit({"event": "result", "run_id": run, "refresh_attempts": 1, **outcome})
                    return
                time.sleep(5)
            emit({"event": "approval_timeout", "run_id": run, "refresh_attempts": 0})
        except Exception:
            emit({"event": "internal_error", "refresh_attempts": 0})

    def start(self):
        if not self.settings():
            return
        with self.lock:
            if self.started:
                return
            self.started = True
        # Never block app readiness or change its normal startup path.
        threading.Thread(target=self.run, daemon=True, name="dev-refresh-probe").start()


probe = Probe()
