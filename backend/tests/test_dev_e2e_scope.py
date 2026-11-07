"""Exercise the operator-scoped HTTP chain with SQLite and blocked outbound I/O."""
import io
import os
import secrets
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select

from app.dev_e2e import DEV_CALLBACK, DEV_HOST, configured_scope
from app.models import Order, Entitlement, AuthorizedDevice
from scripts import check_neon_readonly
import test_ecpay_http_chain as existing


class ControlledE2EHttpTests(existing.ECPayHttpChainTests):
    def setUp(self):
        self.run_id = "E2E-" + uuid4().hex[:16]
        self.email = self.run_id.lower() + "@example.com"
        super().setUp()
        self.key = secrets.token_urlsafe(32)
        self.enterContext(patch.dict(os.environ, {
            "ENSOUND_DEV_E2E_ENABLED": "1", "ENSOUND_DEV_E2E_KEY": self.key,
            "ENSOUND_DEV_E2E_RUN_ID": self.run_id, "ENSOUND_DEV_E2E_EMAIL": self.email,
            "ENSOUND_DEV_E2E_EXPIRES_AT": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "ENSOUND_DEV_DATABASE_HOST": DEV_HOST, "ENSOUND_DEV_DATABASE_NAME": "neondb",
            "ECPAY_STAGE_RETURN_URL": DEV_CALLBACK,
            "ECPAY_STAGE_MERCHANT_ID": "3002607",
        }))
        self.enterContext(patch("app.dev_e2e.get_database_url", return_value=(
            f"postgresql+psycopg://synthetic:dummy@{DEV_HOST}/neondb?sslmode=verify-full&sslrootcert=system")))
        self.client.headers["X-EnSound-E2E-Key"] = self.key

    def test_unkeyed_or_wrong_mailbox_requests_write_nothing(self):
        self.assertEqual(self.client.post("/orders", json={"email": self.email},
                         headers={"X-EnSound-E2E-Key": ""}).status_code, 403)
        self.assertEqual(self.client.post("/orders", json={"email": "other@example.com"}).status_code, 403)
        self.assertEqual(self.client.post("/access/email-links", json={"email": "other@example.com"}).status_code, 403)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Order)), 0)
        self.delivery.assert_not_called()

    def test_provider_callback_does_not_require_operator_key(self):
        _, fields = self.verified_checkout()
        self.client.headers.pop("X-EnSound-E2E-Key")
        self.assertEqual(self.callback(fields).text, "1|OK")

    def test_callback_outside_mailbox_scope_cannot_mark_paid(self):
        order_id, fields = self.verified_checkout()
        with self.factory.begin() as session:
            session.get(Order, order_id).entitlement_email = "not-this-run@example.com"
        self.assertEqual(self.callback(fields).status_code, 403)
        with self.factory() as session:
            self.assertEqual(session.get(Order, order_id).status, "pending")
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 0)

    def test_expired_or_wrong_target_scope_fails_closed(self):
        for changes in (
            {"ENSOUND_DEV_E2E_EXPIRES_AT": "2000-01-01T00:00:00+00:00"},
            {"ENSOUND_DEV_DATABASE_HOST": "other.invalid"},
            {"ECPAY_STAGE_RETURN_URL": "https://other.invalid/callback"},
            {"ECPAY_STAGE_MERCHANT_ID": "9999999"},
            {"ENSOUND_DEV_E2E_ENABLED": "true"},
        ):
            with self.subTest(keys=list(changes)), patch.dict(os.environ, changes):
                self.assertEqual(self.client.post("/orders", json={"email": self.email}).status_code, 503)
        self.delivery.assert_not_called()

    def test_startup_requires_explicit_valid_scope_and_never_enables_writes(self):
        with patch("sys.argv", ["probe", "--expected-host", DEV_HOST, "--controlled-e2e"]), \
             patch.object(check_neon_readonly, "probe", return_value={"status": "PASS"}) as run, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(check_neon_readonly.main(), 0)
            self.assertFalse(run.call_args.kwargs["require_read_only_default"])
        with patch.dict(os.environ, {"ENSOUND_DEV_E2E_KEY": ""}), \
             patch("sys.argv", ["probe", "--expected-host", DEV_HOST, "--controlled-e2e"]), \
             patch.object(check_neon_readonly, "probe") as run, redirect_stdout(io.StringIO()):
            self.assertEqual(check_neon_readonly.main(), 2)
            run.assert_not_called()
        with patch("sys.argv", ["probe", "--expected-host", DEV_HOST,
                                 "--controlled-e2e", "--require-read-only-default"]), \
             patch.object(check_neon_readonly, "probe") as run, redirect_stdout(io.StringIO()):
            self.assertEqual(check_neon_readonly.main(), 2)
            run.assert_not_called()

    def test_expiry_stops_callback_before_any_payment_write(self):
        order_id, fields = self.verified_checkout()
        with patch.dict(os.environ, {"ENSOUND_DEV_E2E_EXPIRES_AT": "2000-01-01T00:00:00+00:00"}):
            reply = self.callback(fields)
            self.assertEqual(reply.status_code, 503)
            self.assertNotIn(self.key, reply.text)
            self.assertEqual(self.client.get("/health/db").status_code, 200)
        with self.factory() as session:
            self.assertEqual(session.get(Order, order_id).status, "pending")
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 0)

    def test_three_device_limit_over_http_and_revocation(self):
        order_id, fields = self.verified_checkout()
        self.assertEqual(self.callback(fields).text, "1|OK")
        headers = []
        now = datetime.now(timezone.utc)
        # Respect the existing real cooldown and hourly quota by advancing only
        # the test clock. No rate-limit behavior is disabled.
        for i in range(3):
            with patch("app.api.access.datetime") as clock:
                clock.now.return_value = now + timedelta(hours=i)
                headers.append(self.activate()[0])
        with patch("app.api.access.datetime") as clock:
            clock.now.return_value = now + timedelta(hours=3)
            self.assertEqual(self.client.post("/access/email-links", json={"email": self.email}).status_code, 202)
            token = self.delivered_token()
            proof = self.client.post("/access/email-links/redeem", json={"token": token}).json()["claim_session"]
            self.assertEqual(self.client.post("/access/devices", json={"claim_session": proof,
                             "device_id": str(uuid4())}).status_code, 409)
        with self.factory.begin() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice)), 3)
            session.get(Order, order_id).status = "refund_pending"
        for item in headers:
            self.assertEqual(self.client.get("/access/status", headers=item).status_code, 200)
        # Synthetic confirmed refund only: never an ECPay refund API request.
        with self.factory.begin() as session:
            session.get(Order, order_id).status = "refunded"
            entitlement = session.scalar(select(Entitlement))
            entitlement.status = "revoked"
            entitlement.revoked_at = now
            entitlement.revocation_reason = "synthetic_confirmed_refund"
        for item in headers:
            self.assertEqual(self.client.get("/access/status", headers=item).status_code, 401)

    def test_unverified_order_signed_callback_stays_pending(self):
        order_id, _ = self.new_order()
        with self.factory.begin() as session:
            session.get(Order, order_id).provider_order_id = "ES" + order_id.hex[:18]
        fields = {"MerchantID": "3002607", "MerchantTradeNo": "ES" + order_id.hex[:18], "TotalAmount": "199"}
        self.assertEqual(self.callback(fields).status_code, 409)
        with self.factory() as session:
            self.assertEqual(session.get(Order, order_id).status, "pending")
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 0)
