"""Explicit Development frontend handoff; never infer a destination from a request."""

import os
from urllib.parse import urlsplit


def dev_frontend_url() -> str | None:
    value = os.environ.get("ENSOUND_DEV_FRONTEND_URL", "").strip()
    if not value:
        return None
    parsed = urlsplit(value)
    hosted = value == "https://arianien2026.github.io/EnSound-UP-dev/"
    local = (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")
             and parsed.port in (5173, 5174) and parsed.path == "/EnSound-UP-dev/")
    if (not (hosted or local) or parsed.username or parsed.password or parsed.query or
            parsed.fragment or not parsed.netloc or not value.endswith("/")):
        raise ValueError("Invalid Development frontend URL")
    return value


def strict_https_url(value: str):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or "." not in parsed.hostname
            or parsed.port not in (None, 443) or parsed.username is not None
            or parsed.password is not None or parsed.fragment or parsed.query
            or parsed.hostname == "ensound-up-dev.onrender.com"
            or value.startswith("https://arianien2026.github.io/EnSound-UP-dev/")):
        raise ValueError("Invalid Production URL")
    return parsed


def payment_frontend_url(environment: str | None = None) -> str | None:
    if environment is None:
        from app.services.ecpay_stage import payment_environment
        environment = payment_environment()
    if environment == "ecpay_stage":
        return dev_frontend_url()
    value = os.environ.get("ENSOUND_PRODUCTION_FRONTEND_URL", "").strip()
    strict_https_url(value)
    if not value.endswith("/"):
        raise ValueError("Invalid Production frontend URL")
    return value
