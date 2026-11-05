"""Isolated backend payment-to-device regression; no provider, email, or external DB calls."""

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api.access import (
    ClaimLinkRequest, RedeemClaimRequest, RegisterDeviceRequest,
    online_entitlement_status, redeem_email_link, register_claimed_device,
    request_claim_link,
)
from app.api.ecpay_return import receive_ecpay_stage_result
from app.database import Base
from app.models import AuthorizedDevice, EmailClaimSession, Entitlement, Order
from app.services.ecpay_stage import check_mac_value, get_stage_config
from app.services.entitlement_grant import (
    EntitlementGrantUnavailable, IneligibleEntitlementOrder, grant_paid_order,
)
from app.services.entitlement_reconcile import (
    _issue_recovery_permit, reconcile_order_ids, recovery_candidates,
)
from app.services.pricing import PRODUCT_CODE
from test_ecpay_checkout import STAGE_ENV


class PaymentAccessChainTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'chain.sqlite'}")
        self.addCleanup(self.engine.dispose)
        # SQLite discards DateTime(timezone=True)'s offset. Restore the Stage
        # wall-clock zone on loads so duplicate callbacks compare the same
        # provider timestamp as PostgreSQL's timezone-aware column would.
        def restore_provider_zone(order, _context):
            if order.provider_paid_at is not None and order.provider_paid_at.tzinfo is None:
                order.provider_paid_at = order.provider_paid_at.replace(tzinfo=ZoneInfo("Asia/Taipei"))

        event.listen(Order, "load", restore_provider_zone)
        self.addCleanup(event.remove, Order, "load", restore_provider_zone)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.order_id = uuid4()
        self.merchant_trade_no = "ES" + self.order_id.hex[:18]
        self.now = datetime.now(timezone.utc)
        with self.factory.begin() as session:
            session.add(Order(
                id=self.order_id, entitlement_email="buyer@example.com",
                product_code=PRODUCT_CODE, currency="TWD", amount=199,
                pricing_code="regular", status="pending", created_at=self.now,
                expires_at=self.now + timedelta(hours=2),
                email_verified_at=self.now, provider_order_id=self.merchant_trade_no,
            ))

    def callback(self, *, fields=None, sign=True, grant=None):
        with patch.dict(os.environ, STAGE_ENV):
            values = {
                "MerchantID": STAGE_ENV["ECPAY_STAGE_MERCHANT_ID"],
                "MerchantTradeNo": self.merchant_trade_no,
                "TradeAmt": "199", "RtnCode": "1",
                "TradeNo": self.order_id.hex[:20], "PaymentType": "Credit_CreditCard",
                "PaymentDate": "2024/12/31 12:26:09", "SimulatePaid": "0",
            }
            values.update(fields or {})
            values["CheckMacValue"] = (check_mac_value(values, get_stage_config())
                                       if sign else "0" * 64)

            async def receive():
                return {"type": "http.request", "body": urlencode(values).encode(),
                        "more_body": False}

            request = Request({
                "type": "http", "method": "POST", "path": "/payments/ecpay/return",
                "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            }, receive)
            actual_grant = grant or (lambda order_id: grant_paid_order(order_id, self.factory))
            with patch("app.api.ecpay_return.get_session_factory", return_value=self.factory), \
                 patch("app.api.ecpay_return.grant_paid_order", side_effect=actual_grant):
                return asyncio.run(receive_ecpay_stage_result(request))

    @staticmethod
    def http_request():
        return Request({"type": "http", "method": "POST", "client": ("198.51.100.2", 1234),
                        "path": "/access/email-links", "headers": []})

    def claim_and_activate(self):
        with patch("app.api.access.get_session_factory", return_value=self.factory), \
             patch("app.api.access.get_rate_limit_key", return_value=b"isolated-test-key-32-bytes-long-123456"), \
             patch("app.api.access.get_email_config"):
            tasks = BackgroundTasks()
            reply = request_claim_link(ClaimLinkRequest(email="buyer@example.com"),
                                       self.http_request(), tasks)
            self.assertEqual(reply, {"message": "If eligible, an access email will be sent."})
            # Inspect the queued task; never execute it or contact Resend.
            token = tasks.tasks[0].args[1]
            self.assertIsNotNone(token)
            proof = json.loads(redeem_email_link(RedeemClaimRequest(token=token),
                                                 self.http_request()).body)["claim_session"]
            device_id = uuid4()
            registered = register_claimed_device(RegisterDeviceRequest(
                claim_session=proof, device_id=device_id,
            ))
            credential = json.loads(registered.body)["device_credential"]
            status = online_entitlement_status(authorization="Bearer " + credential,
                                               x_device_id=str(device_id))
            self.assertEqual(json.loads(status.body)["status"], "active")
            with self.assertRaises(HTTPException) as replay:
                register_claimed_device(RegisterDeviceRequest(
                    claim_session=proof, device_id=uuid4(),
                ))
            self.assertEqual(replay.exception.status_code, 401)
            return device_id, credential

    def test_authenticated_payment_to_online_access_and_revocation(self):
        self.assertEqual(self.callback().body, b"1|OK")
        self.assertEqual(self.callback().body, b"1|OK")  # Same provider identity.
        with self.factory() as session:
            self.assertEqual(session.get(Order, self.order_id).status, "paid")
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement).where(
                Entitlement.source_order_id == self.order_id)), 1)
        device_id, credential = self.claim_and_activate()
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(EmailClaimSession)), 1)
        with self.factory.begin() as session:
            session.get(Order, self.order_id).status = "refunded"
        with patch("app.api.access.get_session_factory", return_value=self.factory):
            with self.assertRaises(HTTPException) as denied:
                online_entitlement_status(authorization="Bearer " + credential,
                                          x_device_id=str(device_id))
        self.assertEqual(denied.exception.status_code, 401)

    def test_paid_commit_survives_grant_failure_then_recovers(self):
        def unavailable(_order_id):
            raise EntitlementGrantUnavailable("isolated test failure")

        with self.assertRaises(HTTPException) as failure:
            self.callback(grant=unavailable)
        self.assertEqual(failure.exception.status_code, 503)
        with self.factory() as session:
            self.assertEqual(session.get(Order, self.order_id).status, "paid")
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 0)
        self.assertEqual(recovery_candidates(self.factory, order_id=self.order_id), [self.order_id])
        self.assertEqual(reconcile_order_ids([self.order_id], self.factory,
            permit=_issue_recovery_permit(self.factory, [self.order_id])).activated, 1)
        self.assertEqual(self.callback().body, b"1|OK")
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 1)
        self.claim_and_activate()

    def test_refund_pending_preserves_access_then_confirmed_refund_cannot_regrant(self):
        self.assertEqual(self.callback().body, b"1|OK")
        first_device, first_credential = self.claim_and_activate()
        with self.factory.begin() as session:
            order = session.get(Order, self.order_id)
            order.status = "refund_pending"
            identity = (order.paid_at, order.provider_trade_no, order.provider_paid_at)
        # This fixture models a pending refund, not a successful provider refund.
        self.assertEqual(grant_paid_order(self.order_id, self.factory).status, "active")
        with self.assertRaises(HTTPException) as duplicate:
            self.callback()
        self.assertEqual(duplicate.exception.status_code, 409)
        with self.factory() as session:
            order = session.get(Order, self.order_id)
            self.assertEqual(order.status, "refund_pending")
            self.assertEqual((order.paid_at, order.provider_trade_no, order.provider_paid_at), identity)
        with patch("app.api.access.get_session_factory", return_value=self.factory):
            status = online_entitlement_status(authorization="Bearer " + first_credential,
                                               x_device_id=str(first_device))
            self.assertEqual(json.loads(status.body)["status"], "active")
        # Advance only the request clock past the existing claim resend cooldown.
        with patch("app.api.access.datetime") as clock:
            clock.now.return_value = self.now + timedelta(minutes=11)
            second_device, second_credential = self.claim_and_activate()
        # Synthetic confirmed-refund fixture; no provider refund is executed.
        with self.factory.begin() as session:
            session.get(Order, self.order_id).status = "refunded"
            entitlement = session.scalar(select(Entitlement).where(
                Entitlement.source_order_id == self.order_id))
            entitlement.status = "revoked"
            entitlement.revoked_at = self.now + timedelta(minutes=12)
            entitlement.revocation_reason = "confirmed_refund"
        with self.assertRaises(IneligibleEntitlementOrder):
            grant_paid_order(self.order_id, self.factory)
        self.assertEqual(recovery_candidates(self.factory, order_id=self.order_id), [])
        result = reconcile_order_ids([self.order_id], self.factory,
            permit=_issue_recovery_permit(self.factory, [self.order_id]))
        self.assertEqual((result.attempted, result.activated, result.rejected, result.failed),
                         (1, 0, 1, 0))
        with patch("app.api.access.get_session_factory", return_value=self.factory):
            for device, credential in ((first_device, first_credential),
                                       (second_device, second_credential)):
                with self.assertRaises(HTTPException) as denied:
                    online_entitlement_status(authorization="Bearer " + credential,
                                              x_device_id=str(device))
                self.assertEqual(denied.exception.status_code, 401)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Entitlement)).status, "revoked")

    def test_refund_pending_missing_entitlement_remains_recoverable(self):
        def unavailable(_order_id):
            raise EntitlementGrantUnavailable("isolated failure")

        with self.assertRaises(HTTPException) as failure:
            self.callback(grant=unavailable)
        self.assertEqual(failure.exception.status_code, 503)
        with self.factory.begin() as session:
            session.get(Order, self.order_id).status = "refund_pending"
        self.assertEqual(recovery_candidates(self.factory, order_id=self.order_id), [self.order_id])
        result = reconcile_order_ids([self.order_id], self.factory,
            permit=_issue_recovery_permit(self.factory, [self.order_id]))
        self.assertEqual((result.attempted, result.activated, result.rejected, result.failed),
                         (1, 1, 0, 0))
        self.claim_and_activate()

    def test_invalid_and_simulated_notifications_never_authorize(self):
        cases = (
            ({}, False, 403),
            ({"MerchantID": "wrong"}, True, 400),
            ({"TradeAmt": "1"}, True, 409),
        )
        for fields, sign, expected_status in cases:
            with self.subTest(fields=fields, sign=sign):
                with self.assertRaises(HTTPException) as denied:
                    self.callback(fields=fields, sign=sign)
                self.assertEqual(denied.exception.status_code, expected_status)
        self.assertEqual(self.callback(fields={"SimulatePaid": "1"}).body, b"1|OK")
        self.assertEqual(self.callback(fields={"RtnCode": "10300066"}).body, b"1|OK")
        with self.factory() as session:
            self.assertEqual(session.get(Order, self.order_id).status, "pending")
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice)), 0)


if __name__ == "__main__":
    unittest.main()
