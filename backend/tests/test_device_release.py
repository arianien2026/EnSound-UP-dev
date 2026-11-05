import hashlib
import json
import secrets
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException, Request
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.api.access import (
    ClaimLinkRequest, RedeemClaimRequest, redeem_release_link, release_claimed_device,
    release_device_list, release_page, release_script, request_release_link,
)
from app.database import Base
from app.models import AuthorizedDevice, EmailClaimSession, EmailClaimToken, Entitlement, Order
from app.services.device_access import IneligibleAccess, register_device, verify_online_access
from app.services.device_release import (
    InvalidReleaseSession, ReleaseCooldown, ReleaseUnavailable, RELEASE_COOLDOWN,
    list_release_devices, release_device,
)
from app.services.email_claim import (
    CLAIM_LIFETIME, SESSION_LIFETIME, issue_release_if_eligible, redeem_claim,
    redeem_release_claim,
)
from app.services.email_delivery import EmailConfig, send_release_email
from app.services.pricing import PRODUCT_CODE


KEY = b"isolated-release-rate-limit-key-over-32-bytes"


class DeviceReleaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'release.sqlite'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.now = datetime.now(timezone.utc)
        self.order_id, self.entitlement_id = self.eligible("buyer@example.com")

    def eligible(self, email):
        order_id, entitlement_id = uuid4(), uuid4()
        with self.factory.begin() as session:
            session.add(Order(
                id=order_id, entitlement_email=email, product_code=PRODUCT_CODE,
                currency="TWD", amount=199, pricing_code="regular", status="paid",
                created_at=self.now, expires_at=self.now + timedelta(hours=2), paid_at=self.now,
                email_verified_at=self.now, provider_order_id="ES" + order_id.hex[:18],
                provider_trade_no=order_id.hex[:20], payment_environment="ecpay_stage",
                provider_payment_type="Credit_CreditCard", provider_paid_at=self.now,
            ))
            session.add(Entitlement(
                id=entitlement_id, source_order_id=order_id, product_code=PRODUCT_CODE,
                entitlement_email=email, status="active", activated_at=self.now,
                updated_at=self.now,
            ))
        return order_id, entitlement_id

    def registered(self, entitlement_id=None):
        entitlement_id = entitlement_id or self.entitlement_id
        device_id, token = uuid4(), secrets.token_urlsafe(32)
        with self.factory.begin() as session:
            session.add(EmailClaimSession(
                entitlement_id=entitlement_id, session_hash=hashlib.sha256(token.encode()).hexdigest(),
                scope="device_claim", created_at=self.now,
                expires_at=self.now + timedelta(minutes=10),
            ))
        with self.factory.begin() as session:
            device = register_device(session, token, device_id, self.now)
        return device_id, device.credential

    def release_proof(self, entitlement_id=None, *, scope="device_release", created=None):
        token = secrets.token_urlsafe(32)
        created = created or self.now
        with self.factory.begin() as session:
            session.add(EmailClaimSession(
                entitlement_id=entitlement_id or self.entitlement_id,
                session_hash=hashlib.sha256(token.encode()).hexdigest(), scope=scope,
                created_at=created, expires_at=created + timedelta(minutes=10),
            ))
        return token

    def release(self, token, device_id, when=None):
        with self.factory.begin() as session:
            return release_device(session, token, device_id, when or self.now)

    def test_success_frees_slot_old_credential_denied_and_same_id_cannot_reclaim(self):
        devices = [self.registered() for _ in range(3)]
        token = self.release_proof()
        self.assertEqual(len(self.list_devices(token)), 3)
        self.release(token, devices[0][0])
        remaining_proof = self.release_proof()
        with self.factory() as session:
            self.assertIsNone(verify_online_access(session, *devices[0], self.now))
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice).where(
                AuthorizedDevice.revoked_at.is_(None))), 2)
            self.assertEqual(len(list_release_devices(session, remaining_proof, self.now)), 2)
        self.assertIsNotNone(self.registered())
        fresh_claim = secrets.token_urlsafe(32)
        with self.factory.begin() as session:
            session.add(EmailClaimSession(
                entitlement_id=self.entitlement_id,
                session_hash=hashlib.sha256(fresh_claim.encode()).hexdigest(),
                scope="device_claim", created_at=self.now,
                expires_at=self.now + timedelta(minutes=10),
            ))
        with self.assertRaises(IneligibleAccess):
            with self.factory.begin() as session:
                register_device(session, fresh_claim, devices[0][0], self.now)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice).where(
                AuthorizedDevice.revoked_at.is_(None))), 3)

    def list_devices(self, token):
        with self.factory.begin() as session:
            return list_release_devices(session, token, self.now)

    def test_cooldown_replay_and_boundary(self):
        first, second = self.registered(), self.registered()
        used = self.release_proof()
        self.release(used, first[0])
        with self.assertRaises(InvalidReleaseSession):
            self.release(used, second[0])
        waiting = self.release_proof(created=self.now + RELEASE_COOLDOWN - timedelta(minutes=1))
        with self.assertRaises(ReleaseCooldown):
            self.release(waiting, second[0], self.now + RELEASE_COOLDOWN - timedelta(seconds=1))
        with self.factory() as session:
            self.assertIsNone(session.scalar(select(EmailClaimSession).where(
                EmailClaimSession.session_hash == hashlib.sha256(waiting.encode()).hexdigest()
            )).consumed_at)
        self.release(waiting, second[0], self.now + RELEASE_COOLDOWN)
        with self.factory() as session:
            self.assertEqual(session.get(Entitlement, self.entitlement_id).last_device_released_at,
                             (self.now + RELEASE_COOLDOWN).replace(tzinfo=None))

    def test_wrong_scope_expiry_cross_entitlement_and_invalid_target(self):
        device_id, _ = self.registered()
        other_order, other_entitlement = self.eligible("other@example.com")
        del other_order
        other_proof = self.release_proof(other_entitlement)
        for proof, expected in (
            (self.release_proof(scope="device_claim"), InvalidReleaseSession),
            (self.release_proof(created=self.now - timedelta(minutes=10)), InvalidReleaseSession),
            (other_proof, ReleaseUnavailable),
        ):
            with self.assertRaises(expected):
                self.release(proof, device_id)
        with self.assertRaises(ReleaseUnavailable):
            self.release(self.release_proof(), uuid4())
        with self.factory() as session:
            self.assertIsNone(session.get(Entitlement, self.entitlement_id).last_device_released_at)
            self.assertIsNone(session.get(AuthorizedDevice, session.scalar(select(AuthorizedDevice.id))).revoked_at)

    def test_refund_unverified_and_revoked_entitlement_denied(self):
        device_id, _ = self.registered()
        for kind in ("unverified", "refunded", "revoked"):
            with self.subTest(kind=kind):
                proof = self.release_proof()
                with self.factory.begin() as session:
                    order, entitlement = session.get(Order, self.order_id), session.get(Entitlement, self.entitlement_id)
                    if kind == "unverified": order.email_verified_at = None
                    if kind == "refunded": order.status = "refunded"
                    if kind == "revoked":
                        entitlement.status, entitlement.revoked_at = "revoked", self.now
                        entitlement.revocation_reason = "confirmed refund"
                with self.assertRaises(ReleaseUnavailable):
                    self.release(proof, device_id)
                with self.factory.begin() as session:
                    order, entitlement = session.get(Order, self.order_id), session.get(Entitlement, self.entitlement_id)
                    order.email_verified_at, order.status = self.now, "paid"
                    entitlement.status, entitlement.revoked_at, entitlement.revocation_reason = "active", None, None

    def test_atomic_rollback_on_failed_commit(self):
        device_id, credential = self.registered()
        proof = self.release_proof()

        def fail(_session: Session):
            raise OperationalError("isolated commit failure", {}, Exception("failed"))

        event.listen(Session, "before_commit", fail)
        try:
            with self.assertRaises(OperationalError):
                self.release(proof, device_id)
        finally:
            event.remove(Session, "before_commit", fail)
        with self.factory() as session:
            self.assertIsNone(session.get(Entitlement, self.entitlement_id).last_device_released_at)
            self.assertIsNone(session.scalar(select(AuthorizedDevice)).revoked_at)
            self.assertIsNone(session.scalar(select(EmailClaimSession).where(
                EmailClaimSession.session_hash == hashlib.sha256(proof.encode()).hexdigest()
            )).consumed_at)
            self.assertIsNotNone(verify_online_access(session, device_id, credential, self.now))

    def test_release_email_proof_hash_expiry_replay_and_wrong_purpose(self):
        with self.factory.begin() as session:
            token = issue_release_if_eligible(session, "buyer@example.com", "198.51.100.1", self.now, KEY)
        with self.factory() as session:
            record = session.scalar(select(EmailClaimToken))
            self.assertEqual(record.purpose, "device_release")
            self.assertEqual(record.token_hash, hashlib.sha256(token.encode()).hexdigest())
            self.assertEqual(record.expires_at - record.created_at, CLAIM_LIFETIME)
        with self.factory.begin() as session:
            self.assertIsNone(redeem_claim(session, token, self.now))
            self.assertIsNone(redeem_release_claim(session, token, self.now + CLAIM_LIFETIME))
            proof = redeem_release_claim(session, token, self.now + timedelta(seconds=1))
            wrong_purpose = secrets.token_urlsafe(32)
            session.add(EmailClaimToken(
                entitlement_id=self.entitlement_id,
                token_hash=hashlib.sha256(wrong_purpose.encode()).hexdigest(),
                purpose="device_claim", created_at=self.now,
                expires_at=self.now + CLAIM_LIFETIME,
            ))
        with self.factory.begin() as session:
            self.assertIsNone(redeem_release_claim(session, token, self.now + timedelta(seconds=2)))
            self.assertIsNone(redeem_release_claim(session, wrong_purpose, self.now))
        with self.factory() as session:
            row = session.scalar(select(EmailClaimSession).where(EmailClaimSession.scope == "device_release"))
            self.assertEqual(row.session_hash, hashlib.sha256(proof.encode()).hexdigest())
            self.assertEqual(row.expires_at - row.created_at, SESSION_LIFETIME)
        with self.factory.begin() as session:
            self.assertIsNone(issue_release_if_eligible(session, "buyer@example.com", "198.51.100.1", self.now, KEY))
            self.assertIsNone(issue_release_if_eligible(session, "unknown@example.com", "198.51.100.1", self.now, KEY))

    def test_api_minimal_owner_view_and_generic_request(self):
        device_id, credential = self.registered()
        config = EmailConfig("isolated-fake", "sender@example.com", "https://dev.example.com")
        request = Request({"type": "http", "client": ("198.51.100.1", 1234),
                           "method": "POST", "path": "/access/device-release/email-links", "headers": []})
        with patch("app.api.access.get_session_factory", return_value=self.factory), patch(
            "app.api.access.get_rate_limit_key", return_value=KEY
        ), patch("app.api.access.get_email_config", return_value=config):
            known_tasks, unknown_tasks = BackgroundTasks(), BackgroundTasks()
            known = request_release_link(ClaimLinkRequest(email="buyer@example.com"), request, known_tasks)
            unknown = request_release_link(ClaimLinkRequest(email="unknown@example.com"), request, unknown_tasks)
            self.assertEqual(known, unknown)
            self.assertIsNone(unknown_tasks.tasks[0].args[1])
            token = known_tasks.tasks[0].args[1]
            self.assertEqual(len(token), 43)
            result = redeem_release_link(RedeemClaimRequest(token=token), request)
            proof = json.loads(result.body)["release_session"]
            self.assertEqual(json.loads(result.body)["scope"], "device_release")
            listed = release_device_list(authorization="Bearer " + proof)
            self.assertEqual(json.loads(listed.body), {"devices": [{
                "device_id": str(device_id), "registered_at": self.now.isoformat()
            }]})
            for invalid in (None, "Bearer " + credential, "Bearer " + self.release_proof(scope="device_claim")):
                with self.assertRaises(HTTPException) as caught:
                    release_device_list(authorization=invalid)
                self.assertEqual(caught.exception.status_code, 401)
            done = release_claimed_device(device_id=device_id, authorization="Bearer " + proof)
            self.assertEqual(json.loads(done.body)["status"], "released")
            self.assertEqual(done.headers["cache-control"], "no-store")
            with self.assertRaises(HTTPException) as caught:
                release_claimed_device(device_id=device_id, authorization="Bearer " + proof)
            self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(release_page().headers["cache-control"], "no-store")
        self.assertIn("history.replaceState", release_script().body.decode())
        self.assertIn("addEventListener('click'", release_script().body.decode())

    def test_resend_email_uses_fragment_and_no_secret_response(self):
        config = EmailConfig("isolated-fake", "sender@example.com", "https://dev.example.com")

        class Accepted:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self, *_): return b'{"id":"accepted-in-test"}'

        with patch("app.services.email_delivery.urlopen", return_value=Accepted()) as urlopen:
            send_release_email("buyer@example.com", "secret-token", config)
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertIn("/access/release#token=secret-token", payload["text"])
        self.assertEqual(payload["to"], ["buyer@example.com"])


if __name__ == "__main__":
    unittest.main()
