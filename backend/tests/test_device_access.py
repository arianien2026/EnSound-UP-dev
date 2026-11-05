import hashlib
import json
import secrets
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.api.access import RegisterDeviceRequest, online_entitlement_status, register_claimed_device
from app.database import Base
from app.models import AuthorizedDevice, EmailClaimSession, Entitlement, Order
from app.services.device_access import (
    DeviceLimitReached, IneligibleAccess, InvalidClaimSession, ONLINE_CREDENTIAL_LIFETIME,
    register_device, verify_online_access,
)
from app.services.pricing import PRODUCT_CODE


class DeviceAccessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'devices.sqlite'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.now = datetime.now(timezone.utc)
        self.order_id, self.entitlement_id = uuid4(), uuid4()
        with self.factory.begin() as session:
            session.add(Order(
                id=self.order_id, entitlement_email="buyer@example.com", product_code=PRODUCT_CODE,
                currency="TWD", amount=199, pricing_code="regular", status="paid",
                created_at=self.now, expires_at=self.now + timedelta(hours=2), paid_at=self.now,
                email_verified_at=self.now, provider_order_id="ES" + self.order_id.hex[:18],
                provider_trade_no=self.order_id.hex[:20], payment_environment="ecpay_stage",
                provider_payment_type="Credit_CreditCard", provider_paid_at=self.now,
            ))
            session.add(Entitlement(
                id=self.entitlement_id, source_order_id=self.order_id, product_code=PRODUCT_CODE,
                entitlement_email="buyer@example.com", status="active",
                activated_at=self.now, updated_at=self.now,
            ))

    def claim(self, expires=None):
        token = secrets.token_urlsafe(32)
        with self.factory.begin() as session:
            session.add(EmailClaimSession(
                entitlement_id=self.entitlement_id,
                session_hash=hashlib.sha256(token.encode()).hexdigest(),
                scope="device_claim", created_at=self.now,
                expires_at=expires or self.now + timedelta(minutes=10),
            ))
        return token

    def register(self, token, device_id=None, now=None):
        device_id = device_id or uuid4()
        with self.factory.begin() as session:
            result = register_device(session, token, device_id, now or self.now)
        return device_id, result

    def test_three_devices_allowed_fourth_denied_without_consuming_claim(self):
        credentials = [self.register(self.claim()) for _ in range(3)]
        self.assertEqual(len({item[0] for item in credentials}), 3)
        fourth_claim = self.claim()
        with self.assertRaises(DeviceLimitReached):
            self.register(fourth_claim)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice)), 3)
            self.assertIsNone(session.scalar(select(EmailClaimSession).where(
                EmailClaimSession.session_hash == hashlib.sha256(fourth_claim.encode()).hexdigest()
            )).consumed_at)
            for device_id, result in credentials:
                status = verify_online_access(session, device_id, result.credential, self.now)
                self.assertEqual(status.product_code, PRODUCT_CODE)

    def test_claim_is_one_time_and_credential_is_stored_as_digest(self):
        token = self.claim()
        device_id, issued = self.register(token)
        self.assertEqual(len(issued.credential), 43)
        self.assertEqual(issued.credential_expires_at - self.now, ONLINE_CREDENTIAL_LIFETIME)
        with self.assertRaises(InvalidClaimSession):
            self.register(token, device_id)
        with self.factory() as session:
            device = session.scalar(select(AuthorizedDevice))
            self.assertEqual(device.credential_hash, hashlib.sha256(issued.credential.encode()).hexdigest())
            self.assertNotEqual(device.credential_hash, issued.credential)
            self.assertIsNotNone(session.scalar(select(EmailClaimSession)).consumed_at)
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice)), 1)

    def test_fresh_claim_same_device_rotates_secret_without_extra_slot(self):
        device_id = uuid4()
        _, first = self.register(self.claim(), device_id)
        _, second = self.register(self.claim(), device_id)
        self.assertNotEqual(first.credential, second.credential)
        with self.factory() as session:
            self.assertIsNone(verify_online_access(session, device_id, first.credential, self.now))
            self.assertIsNotNone(verify_online_access(session, device_id, second.credential, self.now))
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice)), 1)

    def test_invalid_expired_scoped_claim_and_wrong_device_denied(self):
        with self.assertRaises(InvalidClaimSession):
            self.register(secrets.token_urlsafe(32))
        with self.assertRaises(InvalidClaimSession):
            self.register(self.claim(expires=self.now), now=self.now)
        device_id, issued = self.register(self.claim())
        with self.factory() as session:
            self.assertIsNone(verify_online_access(session, uuid4(), issued.credential, self.now))
            self.assertIsNone(verify_online_access(session, device_id, secrets.token_urlsafe(32), self.now))
            self.assertIsNone(verify_online_access(session, device_id, issued.credential,
                                                  self.now + ONLINE_CREDENTIAL_LIFETIME))

    def test_refund_unverified_or_revoked_entitlement_denied(self):
        device_id = uuid4()
        for location, value in (("order_status", "refunded"), ("order_status", "pending"),
                                ("email_verified_at", None),
                                ("provider_trade_no", None), ("entitlement_status", "revoked")):
            with self.subTest(location=location):
                _, issued = self.register(self.claim(), device_id)
                with self.factory.begin() as session:
                    order = session.get(Order, self.order_id)
                    entitlement = session.get(Entitlement, self.entitlement_id)
                    if location == "entitlement_status":
                        entitlement.status = "revoked"
                        entitlement.revoked_at = self.now
                        entitlement.revocation_reason = "confirmed refund test"
                    else:
                        setattr(order, "status" if location == "order_status" else location, value)
                with self.factory() as session:
                    self.assertIsNone(verify_online_access(session, device_id, issued.credential, self.now))
                with self.assertRaises(IneligibleAccess):
                    self.register(self.claim())
                with self.factory.begin() as session:
                    order = session.get(Order, self.order_id)
                    entitlement = session.get(Entitlement, self.entitlement_id)
                    order.status = "paid"
                    order.email_verified_at = self.now
                    order.provider_trade_no = self.order_id.hex[:20]
                    entitlement.status = "active"
                    entitlement.revoked_at = None
                    entitlement.revocation_reason = None

    def test_revoked_device_denied_and_cannot_be_renewed_by_same_id(self):
        device_id, issued = self.register(self.claim())
        with self.factory.begin() as session:
            session.scalar(select(AuthorizedDevice)).revoked_at = self.now
        with self.factory() as session:
            self.assertIsNone(verify_online_access(session, device_id, issued.credential, self.now))
        with self.assertRaises(IneligibleAccess):
            self.register(self.claim(), device_id)

    def test_failed_commit_does_not_consume_claim_or_issue_usable_credential(self):
        claim = self.claim()

        def fail_commit(_session: Session):
            raise OperationalError("isolated commit failure", {}, Exception("failed"))

        event.listen(Session, "before_commit", fail_commit)
        try:
            with self.assertRaises(OperationalError):
                self.register(claim)
        finally:
            event.remove(Session, "before_commit", fail_commit)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice)), 0)
            record = session.scalar(select(EmailClaimSession).where(
                EmailClaimSession.session_hash == hashlib.sha256(claim.encode()).hexdigest()
            ))
            self.assertIsNone(record.consumed_at)

    def test_api_returns_minimal_status_and_rejects_forgery(self):
        token, device_id = self.claim(), uuid4()
        with patch("app.api.access.get_session_factory", return_value=self.factory):
            registered = register_claimed_device(RegisterDeviceRequest(
                claim_session=token, device_id=device_id,
            ))
            self.assertEqual(registered.status_code, 201)
            self.assertEqual(registered.headers["cache-control"], "no-store")
            data = json.loads(registered.body)
            self.assertEqual(set(data), {"device_credential", "credential_expires_at"})
            status = online_entitlement_status(
                authorization="Bearer " + data["device_credential"],
                x_device_id=str(device_id),
            )
            self.assertEqual(set(json.loads(status.body)),
                             {"status", "product_code", "credential_expires_at"})
            self.assertEqual(status.headers["cache-control"], "no-store")
            for authorization, identifier in (
                ("Bearer " + data["device_credential"], str(uuid4())),
                ("Bearer " + secrets.token_urlsafe(32), str(device_id)),
                (None, str(device_id)),
                ("Bearer " + data["device_credential"], "invalid"),
            ):
                with self.subTest(identifier=identifier), self.assertRaises(HTTPException) as caught:
                    online_entitlement_status(authorization=authorization, x_device_id=identifier)
                self.assertEqual(caught.exception.status_code, 401)
        with self.assertRaises(ValidationError):
            RegisterDeviceRequest(claim_session=token, device_id="not-a-uuid")


if __name__ == "__main__":
    unittest.main()
