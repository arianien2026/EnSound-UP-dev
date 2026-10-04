import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from email_validator import EmailNotValidError, validate_email


class EmailDeliveryError(Exception):
    pass


@dataclass(frozen=True)
class EmailConfig:
    api_key: str
    sender: str
    base_url: str


def get_email_config() -> EmailConfig:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    sender = os.environ.get("RESEND_FROM_EMAIL", "").strip()
    base_url = os.environ.get("EMAIL_VERIFY_BASE_URL", "").strip().rstrip("/")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
        validate_email(sender, check_deliverability=False)
    except (EmailNotValidError, ValueError):
        raise EmailDeliveryError("Email delivery unavailable") from None
    if (
        not api_key
        or "\n" in api_key
        or "\r" in api_key
        or not parsed.hostname
        or (port is not None and port < 1)
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
        )
    ):
        raise EmailDeliveryError("Email delivery unavailable")
    return EmailConfig(api_key=api_key, sender=sender, base_url=base_url)


def send_verification_email(recipient: str, token: str, config: EmailConfig) -> None:
    # The fragment is not sent in HTTP requests or normal access logs. The page POSTs it in a body.
    link = f"{config.base_url}/orders/verify-email#token={quote(token)}"
    payload = {
        "from": config.sender,
        "to": [recipient],
        "subject": "EnSound UP - Confirm your email",
        "text": (
            "Confirm this email address for your EnSound UP purchase/access process.\n"
            f"Open this link within 30 minutes: {link}\n"
            "If you did not request this, you can ignore this message."
        ),
    }
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "EnSound-UP/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.load(response)
        if not isinstance(result, dict) or not result.get("id"):
            raise EmailDeliveryError("Email delivery unavailable")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, EmailDeliveryError):
        raise EmailDeliveryError("Email delivery unavailable") from None
