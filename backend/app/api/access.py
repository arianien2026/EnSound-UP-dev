"""Email claim proof only; no endpoint in this module grants learner access."""

import logging
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, UUID4, field_validator
from sqlalchemy.exc import SQLAlchemyError

from app.api.orders import _PAGE_HEADERS
from app.database import get_session_factory
from app.dev_frontend import dev_frontend_url
from app.services.email_claim import (
    allow_redemption_attempt, get_rate_limit_key, issue_claim_if_eligible,
    issue_release_if_eligible, redeem_claim, redeem_release_claim,
)
from app.services.email_delivery import (
    EmailConfig, EmailDeliveryError, get_email_config, send_claim_email, send_release_email,
)
from app.services.device_access import (
    DeviceLimitReached, IneligibleAccess, InvalidClaimSession, register_device,
    verify_online_access,
)
from app.services.device_release import (
    InvalidReleaseSession, ReleaseCooldown, ReleaseUnavailable,
    list_release_devices, release_device,
)


router = APIRouter()


class ClaimLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def trim_email(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class RedeemClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


class RegisterDeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_session: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")
    device_id: UUID4


def _deliver_claim(email: str, token: str | None, config: EmailConfig) -> None:
    if token is None:
        return
    try:
        send_claim_email(email, token, config)
    except EmailDeliveryError:
        logging.warning("Email claim delivery unavailable")


def _deliver_release(email: str, token: str | None, config: EmailConfig) -> None:
    if token is None:
        return
    try:
        send_release_email(email, token, config)
    except EmailDeliveryError:
        logging.warning("Device release delivery unavailable")


def _release_session(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Release authorization unavailable")
    credential = authorization[len("Bearer "):]
    if (len(credential) != 43 or any(ch not in
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for ch in credential)):
        raise HTTPException(status_code=401, detail="Release authorization unavailable")
    return credential


@router.post("/access/device-release/email-links", status_code=202)
def request_release_link(body: ClaimLinkRequest, request: Request,
                         background_tasks: BackgroundTasks) -> dict[str, str]:
    try:
        config = get_email_config()
        with get_session_factory().begin() as session:
            token = issue_release_if_eligible(
                session, str(body.email), request.client.host if request.client else "unknown",
                datetime.now(timezone.utc), get_rate_limit_key(),
            )
    except (SQLAlchemyError, RuntimeError, ValueError, EmailDeliveryError):
        raise HTTPException(status_code=503, detail="Device release service unavailable") from None
    background_tasks.add_task(_deliver_release, str(body.email), token, config)
    return {"message": "If eligible, a device release email will be sent."}


@router.post("/access/device-release/email-links/redeem")
def redeem_release_link(body: RedeemClaimRequest, request: Request) -> JSONResponse:
    try:
        with get_session_factory().begin() as session:
            now = datetime.now(timezone.utc)
            allowed = allow_redemption_attempt(
                session, request.client.host if request.client else "unknown", now,
                get_rate_limit_key(),
            )
            proof = redeem_release_claim(session, body.token, now) if allowed else None
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Device release service unavailable") from None
    if not allowed:
        raise HTTPException(status_code=429, detail="Try again later")
    if proof is None:
        raise HTTPException(status_code=400, detail="Invalid or expired release link")
    return JSONResponse({"release_session": proof, "scope": "device_release", "expires_in": 600},
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/access/device-release/devices")
def release_device_list(authorization: str | None = Header(default=None)) -> JSONResponse:
    credential = _release_session(authorization)
    try:
        with get_session_factory().begin() as session:
            devices = list_release_devices(session, credential, datetime.now(timezone.utc))
    except InvalidReleaseSession:
        raise HTTPException(status_code=401, detail="Release authorization unavailable") from None
    except ReleaseUnavailable:
        raise HTTPException(status_code=403, detail="Access unavailable") from None
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Device release unavailable") from None
    return JSONResponse({"devices": [
        {"device_id": str(row.device_id), "registered_at": row.registered_at.isoformat()}
        for row in devices
    ]}, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/access/device-release/devices/{device_id}")
def release_claimed_device(device_id: UUID4,
                           authorization: str | None = Header(default=None)) -> JSONResponse:
    credential = _release_session(authorization)
    try:
        with get_session_factory().begin() as session:
            released_at = release_device(session, credential, device_id, datetime.now(timezone.utc))
    except InvalidReleaseSession:
        raise HTTPException(status_code=401, detail="Release authorization unavailable") from None
    except ReleaseCooldown:
        raise HTTPException(status_code=409, detail="Device release cooldown active") from None
    except ReleaseUnavailable:
        raise HTTPException(status_code=403, detail="Access unavailable") from None
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Device release unavailable") from None
    return JSONResponse({"status": "released", "released_at": released_at.isoformat()},
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/access/email-links", status_code=202)
def request_claim_link(body: ClaimLinkRequest, request: Request,
                       background_tasks: BackgroundTasks) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    try:
        config = get_email_config()
        secret = get_rate_limit_key()
        with get_session_factory().begin() as session:
            token = issue_claim_if_eligible(
                session, str(body.email), request.client.host if request.client else "unknown",
                now, secret,
            )
    except (SQLAlchemyError, RuntimeError, ValueError, EmailDeliveryError):
        raise HTTPException(status_code=503, detail="Email claim service unavailable") from None
    background_tasks.add_task(_deliver_claim, str(body.email), token, config)
    return {"message": "If eligible, an access email will be sent."}


@router.post("/access/email-links/redeem")
def redeem_email_link(body: RedeemClaimRequest, request: Request) -> JSONResponse:
    try:
        with get_session_factory().begin() as session:
            now = datetime.now(timezone.utc)
            allowed = allow_redemption_attempt(
                session, request.client.host if request.client else "unknown", now,
                get_rate_limit_key(),
            )
            claim_session = redeem_claim(session, body.token, now) if allowed else None
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Email claim service unavailable") from None
    if not allowed:
        raise HTTPException(status_code=429, detail="Try again later")
    if claim_session is None:
        raise HTTPException(status_code=400, detail="Invalid or expired access link")
    # This is a scoped, ten-minute session credential, not an access entitlement.
    return JSONResponse(
        {"claim_session": claim_session, "scope": "device_claim", "expires_in": 600},
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/access/devices", status_code=201)
def register_claimed_device(body: RegisterDeviceRequest) -> JSONResponse:
    try:
        with get_session_factory().begin() as session:
            device = register_device(session, body.claim_session, body.device_id,
                                     datetime.now(timezone.utc))
    except InvalidClaimSession:
        raise HTTPException(status_code=401, detail="Invalid or expired claim session") from None
    except IneligibleAccess:
        raise HTTPException(status_code=403, detail="Access unavailable") from None
    except DeviceLimitReached:
        raise HTTPException(status_code=409, detail="Device limit reached") from None
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Device registration unavailable") from None
    return JSONResponse({
        "device_credential": device.credential,
        "credential_expires_at": device.credential_expires_at.isoformat(),
    }, status_code=201, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/access/status")
def online_entitlement_status(
    authorization: str | None = Header(default=None),
    x_device_id: str | None = Header(default=None),
) -> JSONResponse:
    if not authorization or not authorization.startswith("Bearer ") or not x_device_id:
        raise HTTPException(status_code=401, detail="Access unavailable")
    credential = authorization[len("Bearer "):]
    try:
        device_id = UUID(x_device_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Access unavailable") from None
    if (device_id.version != 4 or len(credential) != 43 or
            any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
                for ch in credential)):
        raise HTTPException(status_code=401, detail="Access unavailable")
    try:
        with get_session_factory()() as session:
            status = verify_online_access(session, device_id, credential,
                                          datetime.now(timezone.utc))
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Access verification unavailable") from None
    if status is None:
        raise HTTPException(status_code=401, detail="Access unavailable")
    return JSONResponse({
        "status": "active", "product_code": status.product_code,
        "credential_expires_at": status.credential_expires_at.isoformat(),
    }, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


_CLAIM_PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>EnSound UP access confirmation</title></head><body><main><h1>EnSound UP</h1>
<p id="status">請確認 Email 後返回 EnSound UP 啟用此裝置。 / Confirm email to activate this device.</p>
<button id="confirm" type="button" disabled>確認並繼續 / Confirm and continue</button></main>
<script src="/access/claim.js" defer></script></body></html>"""

_CLAIM_SCRIPT = """(() => {
  const appUrl = __FRONTEND_URL__;
  const token = new URLSearchParams(location.hash.slice(1)).get('token');
  history.replaceState(null, '', location.pathname);
  const button = document.getElementById('confirm');
  const status = document.getElementById('status');
  if (!token) { status.textContent = '連結無效或已過期 / Invalid or expired link'; return; }
  if (!appUrl) { status.textContent = '目前無法啟用裝置 / Device activation unavailable'; return; }
  button.disabled = false;
  let claimSession = null;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      if (!claimSession) {
        const result = await fetch('/access/email-links/redeem', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({token}), cache: 'no-store'
        });
        if (result.status === 400) {
          status.textContent = '連結無效或已過期 / Invalid or expired link';
          return;
        }
        if (!result.ok) throw Error();
        const data = await result.json();
        if (data.scope !== 'device_claim' || !/^[A-Za-z0-9_-]{43}$/.test(data.claim_session)) throw Error();
        claimSession = data.claim_session;
      }
      location.replace(appUrl + '#access_claim=' + encodeURIComponent(claimSession));
    } catch (_) {
      status.textContent = '目前無法啟用裝置，請稍後再試 / Activation unavailable; please retry';
      button.disabled = false;
    }
  });
})();"""


@router.get("/access/claim", response_class=HTMLResponse)
def claim_page() -> HTMLResponse:
    return HTMLResponse(_CLAIM_PAGE, headers=_PAGE_HEADERS)


@router.get("/access/claim.js")
def claim_script() -> Response:
    try:
        destination = dev_frontend_url()
    except ValueError:
        destination = None
    return Response(_CLAIM_SCRIPT.replace("__FRONTEND_URL__", json.dumps(destination)),
                    media_type="application/javascript", headers=_PAGE_HEADERS)


_RELEASE_PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>EnSound UP device release</title></head><body><main><h1>EnSound UP</h1>
<p id="status">確認 Email 後可選擇要釋放的裝置。 / Confirm your email to select a device.</p>
<button id="confirm" type="button" disabled>確認 Email / Confirm email</button>
<ul id="devices"></ul></main><script src="/access/release.js" defer></script></body></html>"""

_RELEASE_SCRIPT = """(() => {
  const token = new URLSearchParams(location.hash.slice(1)).get('token');
  history.replaceState(null, '', location.pathname);
  const button = document.getElementById('confirm');
  const status = document.getElementById('status');
  const list = document.getElementById('devices');
  if (!token) { status.textContent = '連結無效或已過期 / Invalid or expired link'; return; }
  button.disabled = false;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const reply = await fetch('/access/device-release/email-links/redeem', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token}), cache: 'no-store'
      });
      if (!reply.ok) throw new Error('invalid');
      const {release_session: proof} = await reply.json();
      const headers = {Authorization: `Bearer ${proof}`};
      const devices = await fetch('/access/device-release/devices', {
        headers, cache: 'no-store'
      });
      if (!devices.ok) throw new Error('unavailable');
      const data = await devices.json();
      status.textContent = data.devices.length ? '選擇要釋放的裝置 / Select a device to release'
        : '沒有可釋放的裝置 / No active devices';
      for (const item of data.devices) {
        const row = document.createElement('li');
        const action = document.createElement('button');
        action.type = 'button';
        action.textContent = `釋放 / Release · ${item.registered_at} · ${item.device_id.slice(-8)}`;
        action.addEventListener('click', async () => {
          action.disabled = true;
          try {
            const released = await fetch(`/access/device-release/devices/${item.device_id}`, {
              method: 'POST', headers, cache: 'no-store'
            });
            if (!released.ok) {
              status.textContent = released.status === 409
                ? '30 天內只能釋放一台裝置 / One release per 30 days'
                : '無法釋放裝置 / Device release unavailable';
              action.disabled = false;
              return;
            }
            list.replaceChildren();
            status.textContent = '裝置已釋放 / Device released';
          } catch (_) {
            action.disabled = false;
            status.textContent = '目前無法釋放裝置 / Device release unavailable';
          }
        });
        row.appendChild(action);
        list.appendChild(row);
      }
    } catch (_) { status.textContent = '連結無效或暫時無法使用 / Link unavailable'; }
  }, {once: true});
})();"""


@router.get("/access/release", response_class=HTMLResponse)
def release_page() -> HTMLResponse:
    return HTMLResponse(_RELEASE_PAGE, headers=_PAGE_HEADERS)


@router.get("/access/release.js")
def release_script() -> Response:
    return Response(_RELEASE_SCRIPT, media_type="application/javascript", headers=_PAGE_HEADERS)
