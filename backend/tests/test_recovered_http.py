"""Real ASGI routing + temporary SQLite; no Neon, provider or email calls.

These tests validate recovered router/engine/session integration. They do not
validate PostgreSQL locks, migrations, certificates, or payment authenticity.
"""
import hashlib
import secrets
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.main import app
from app.models import AuthorizedDevice, EmailClaimSession, Entitlement, Order
from app.services.pricing import PRODUCT_CODE


class RecoveredHttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'http.sqlite'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.enterContext(patch("app.main.get_engine", return_value=self.engine))
        self.enterContext(patch("app.api.access.get_session_factory", return_value=self.factory))
        self.client = self.enterContext(TestClient(app))
        self.now = datetime.now(timezone.utc)
        self.order_id, self.entitlement_id = uuid4(), uuid4()

    def eligible_fixture(self):
        with self.factory.begin() as session:
            session.add(Order(
                id=self.order_id, entitlement_email="synthetic@example.com",
                product_code=PRODUCT_CODE, currency="TWD", amount=199,
                pricing_code="regular", status="paid", created_at=self.now,
                expires_at=self.now + timedelta(hours=2), paid_at=self.now,
                email_verified_at=self.now, provider_order_id="ES" + self.order_id.hex[:18],
                provider_trade_no=self.order_id.hex[:20], payment_environment="ecpay_stage",
                provider_payment_type="Credit_CreditCard", provider_paid_at=self.now,
            ))
            session.add(Entitlement(
                id=self.entitlement_id, source_order_id=self.order_id,
                product_code=PRODUCT_CODE, entitlement_email="synthetic@example.com",
                status="active", activated_at=self.now, updated_at=self.now,
            ))

    def proof(self, scope="device_claim"):
        token = secrets.token_urlsafe(32)
        with self.factory.begin() as session:
            session.add(EmailClaimSession(
                entitlement_id=self.entitlement_id,
                session_hash=hashlib.sha256(token.encode()).hexdigest(), scope=scope,
                created_at=self.now, expires_at=self.now + timedelta(minutes=10),
            ))
        return token

    def register(self, proof=None):
        device_id = str(uuid4())
        response = self.client.post("/access/devices", json={
            "claim_session": proof or self.proof(), "device_id": device_id,
        })
        return device_id, response

    def access(self, device_id, credential):
        return self.client.get("/access/status", headers={
            "Authorization": "Bearer " + credential, "X-Device-Id": device_id,
        })

    def test_startup_health_and_database_session_cleanup(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/health/db").status_code, 200)
        self.assertEqual(self.engine.pool.checkedout(), 0)
        with self.factory() as session:
            for model in Base.registry.mappers:
                self.assertEqual(session.scalar(select(func.count()).select_from(model.class_)), 0)
        self.assertEqual(self.engine.pool.checkedout(), 0)

    def test_http_three_device_limit_release_and_cooldown(self):
        self.eligible_fixture()
        registered = [self.register() for _ in range(3)]
        for device_id, response in registered:
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(self.access(device_id, response.json()["device_credential"]).status_code, 200)
        fourth_proof = self.proof()
        self.assertEqual(self.register(fourth_proof)[1].status_code, 409)
        first_id, first_response = registered[0]
        release = self.client.post("/access/device-release/devices/" + first_id,
            headers={"Authorization": "Bearer " + self.proof("device_release")})
        self.assertEqual(release.status_code, 200)
        self.assertEqual(self.access(first_id, first_response.json()["device_credential"]).status_code, 401)
        cooldown = self.client.post("/access/device-release/devices/" + registered[1][0],
            headers={"Authorization": "Bearer " + self.proof("device_release")})
        self.assertEqual(cooldown.status_code, 409)
        self.assertEqual(self.register(fourth_proof)[1].status_code, 201)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice)
                .where(AuthorizedDevice.revoked_at.is_(None))), 3)
        self.assertEqual(self.engine.pool.checkedout(), 0)

    def test_http_full_access_rejects_completed_refund_and_forged_proof(self):
        self.eligible_fixture()
        device_id, response = self.register()
        self.assertEqual(response.status_code, 201)
        credential = response.json()["device_credential"]
        self.assertEqual(self.access(device_id, credential).status_code, 200)
        self.assertEqual(self.access(device_id, secrets.token_urlsafe(32)).status_code, 401)
        # Local synthetic confirmed-refund fixture; no refund/provider request.
        with self.factory.begin() as session:
            session.get(Order, self.order_id).status = "refunded"
            entitlement = session.get(Entitlement, self.entitlement_id)
            entitlement.status, entitlement.revoked_at = "revoked", self.now
            entitlement.revocation_reason = "synthetic_confirmed_refund"
        self.assertEqual(self.access(device_id, credential).status_code, 401)
        self.assertEqual(self.engine.pool.checkedout(), 0)
