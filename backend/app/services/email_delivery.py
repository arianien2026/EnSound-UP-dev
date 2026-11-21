import base64
from email.message import EmailMessage
from email.policy import SMTP
import json
import logging
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlencode
from urllib.request import Request, urlopen

from email_validator import EmailNotValidError, validate_email


class EmailDeliveryError(Exception):
    pass


@dataclass(frozen=True, repr=False)
class EmailConfig:
    api_key: str
    sender: str
    base_url: str
    transport: str = "resend"
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""


def get_email_config() -> EmailConfig:
    transport = os.environ.get("EMAIL_TRANSPORT", "resend")
    if transport not in {"resend", "gmail_api"}:
        raise EmailDeliveryError("Email delivery unavailable")
    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "").strip()
    api_key = os.environ.get("RESEND_API_KEY", "").strip() if transport == "resend" else ""
    sender = os.environ.get("RESEND_FROM_EMAIL", "").strip() if transport == "resend" else ""
    if transport == "gmail_api":
        api_key = ""  # Never load or send a Resend credential to Google.
        sender = os.environ.get("GMAIL_FROM_EMAIL", "").strip()
        if (not sender.endswith("@gmail.com") or not client_id.endswith(".apps.googleusercontent.com")
                or not client_secret or not refresh_token
                or any(c in client_id + client_secret + refresh_token for c in "\r\n")):
            raise EmailDeliveryError("Email delivery unavailable")
    base_url = os.environ.get("EMAIL_VERIFY_BASE_URL", "").strip().rstrip("/")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
        validate_email(sender, check_deliverability=False)
    except (EmailNotValidError, ValueError):
        raise EmailDeliveryError("Email delivery unavailable") from None
    if (
        (transport == "resend" and not api_key)
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
    if os.environ.get("ECPAY_PAYMENT_ENV", "stage") == "production":
        from app.services.ecpay_stage import get_payment_config
        try:
            callback = urlsplit(get_payment_config().return_url)
        except ValueError:
            raise EmailDeliveryError("Email delivery unavailable") from None
        if (parsed.scheme, parsed.netloc, parsed.path) != (callback.scheme, callback.netloc, ""):
            raise EmailDeliveryError("Email delivery unavailable")
    return EmailConfig(api_key, sender, base_url, transport, client_id, client_secret, refresh_token)


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
    _deliver(payload, config)


def send_claim_email(recipient: str, token: str, config: EmailConfig) -> None:
    link = f"{config.base_url}/access/claim#token={quote(token)}"
    _deliver({
        "from": config.sender,
        "to": [recipient],
        "subject": "EnSound UP - Confirm your access request",
        "text": (
            "Use this link to confirm your EnSound UP access request.\n"
            f"Open it within 15 minutes: {link}\n"
            "If you did not request this, you can ignore this message."
        ),
    }, config)


def send_release_email(recipient: str, token: str, config: EmailConfig) -> None:
    link = f"{config.base_url}/access/release#token={quote(token)}"
    _deliver({
        "from": config.sender,
        "to": [recipient],
        "subject": "EnSound UP - Confirm device release",
        "text": (
            "Use this link to confirm a device release request for EnSound UP.\n"
            f"Open it within 15 minutes: {link}\n"
            "If you did not request this, you can ignore this message."
        ),
    }, config)


def _deliver(payload: dict, config: EmailConfig) -> None:
    try:
        recipient = payload["to"][0]
        if len(payload["to"]) != 1 or any(c in recipient + config.sender for c in "\r\n"):
            raise ValueError()
        validate_email(recipient, check_deliverability=False)
        validate_email(config.sender, check_deliverability=False)
    except (ValueError, EmailNotValidError, KeyError, IndexError, TypeError):
        raise EmailDeliveryError("Email delivery unavailable") from None
    if config.transport == "gmail_api":
        _deliver_gmail(payload, config)
        return
    if config.transport != "resend":
        raise EmailDeliveryError("Email delivery unavailable")
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


_GOOGLE_ERROR_CODES = {
    "refresh": {"invalid_grant", "invalid_client", "unauthorized_client", "invalid_scope",
                "temporarily_unavailable", "invalid_request"},
    "send": {"authError", "insufficientPermissions", "forbidden", "failedPrecondition",
             "rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "backendError", "badRequest",
             "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "SERVICE_DISABLED", "UNAUTHENTICATED",
             "PERMISSION_DENIED", "FAILED_PRECONDITION", "RESOURCE_EXHAUSTED"},
}


def _google_error_reason(error: HTTPError, stage: str) -> str:
    try:
        with error:
            response = json.loads(error.read(4096))
        detail = response.get("error") if isinstance(response, dict) else None
        if stage == "send" and isinstance(detail, dict):
            for field in ("details", "errors"):
                entries = detail.get(field)
                if isinstance(entries, list):
                    for entry in entries:
                        reason = entry.get("reason") if isinstance(entry, dict) else None
                        if isinstance(reason, str) and reason in _GOOGLE_ERROR_CODES[stage]:
                            return reason
            detail = detail.get("status")
        if isinstance(detail, str) and detail in _GOOGLE_ERROR_CODES[stage]:
            return detail
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return "http_error"


def _log_gmail_failure(stage: str, reason: str, status: int | None = None) -> None:
    logging.getLogger(__name__).warning("Gmail API failure stage=%s reason=%s http_status=%s",
                                       stage, reason, status if status is not None else "none")


def _google_json(request: Request, stage: str):
    try:
        with urlopen(request, timeout=10) as response:
            return json.load(response)
    except HTTPError as error:
        _log_gmail_failure(stage, _google_error_reason(error, stage), error.code)
    except TimeoutError:
        _log_gmail_failure(stage, "timeout")
    except OSError:
        _log_gmail_failure(stage, "network_error")
    except (ValueError, TypeError):
        _log_gmail_failure(stage, "invalid_response")
    raise EmailDeliveryError("Email delivery unavailable") from None


def refresh_gmail_access_token(config: EmailConfig) -> str:
    """Request a send-only grant without calling Gmail or logging credentials."""
    if config.transport != "gmail_api":
        raise EmailDeliveryError("Email delivery unavailable")
    refresh = Request("https://oauth2.googleapis.com/token", data=urlencode({
        "client_id": config.client_id, "client_secret": config.client_secret,
        "refresh_token": config.refresh_token, "grant_type": "refresh_token",
    }).encode("ascii"), headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    grant = _google_json(refresh, "refresh")
    access = grant.get("access_token") if isinstance(grant, dict) else None
    token_type = grant.get("token_type") if isinstance(grant, dict) else None
    if (not isinstance(access, str) or not access or any(c in access for c in "\r\n")
            or not isinstance(token_type, str) or token_type.lower() != "bearer"):
        _log_gmail_failure("refresh", "invalid_grant_response")
        raise EmailDeliveryError("Email delivery unavailable")
    if "scope" in grant:
        scope = grant["scope"]
        if not isinstance(scope, str) or scope.split() != ["https://www.googleapis.com/auth/gmail.send"]:
            _log_gmail_failure("refresh", "unexpected_scope")
            raise EmailDeliveryError("Email delivery unavailable")
    return access


def _deliver_gmail(payload: dict, config: EmailConfig) -> None:
    # Do not retry an ambiguous send or fall back to another provider.
    access = refresh_gmail_access_token(config)
    try:
        message = EmailMessage(policy=SMTP)
        message["From"] = config.sender
        message["To"] = payload["to"][0]
        message["Subject"] = payload["subject"]
        message.set_content(payload["text"])
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        request = Request("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                          data=json.dumps({"raw": raw}).encode("utf-8"),
                          headers={"Authorization": "Bearer " + access,
                                   "Content-Type": "application/json"}, method="POST")
    except (ValueError, TypeError, AttributeError):
        _log_gmail_failure("send", "invalid_message")
        raise EmailDeliveryError("Email delivery unavailable") from None
    result = _google_json(request, "send")
    if not isinstance(result, dict) or not isinstance(result.get("id"), str) or not result["id"]:
        _log_gmail_failure("send", "invalid_response")
        raise EmailDeliveryError("Email delivery unavailable")
