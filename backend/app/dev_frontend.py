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
