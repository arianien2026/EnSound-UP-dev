"""Full recovered ECPay Stage chain through real ASGI routes and local SQLite.

Only outbound email delivery is mocked on the success path. Checkout URLs are
never followed; callback payloads are synthetic and posted only to TestClient.
This does not test PostgreSQL concurrency, Neon connectivity, or live ECPay.
"""
import os
import re
import tempfile
import unittest
from datetime import timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import DateTime, create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.main import app
from app.models import AuthorizedDevice, Entitlement, Order
from app.services.ecpay_stage import STAGE_CHECKOUT_URL, check_mac_value, get_stage_config
from app.services.entitlement_grant import (
    EntitlementGrantUnavailable, IneligibleEntitlementOrder, grant_paid_order,
)
from test_ecpay_checkout import STAGE_ENV


class ECPayHttpChainTests(unittest.TestCase):
    email = "synthetic@example.com"
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'ecpay-http.sqlite'}")
        self.addCleanup(self.engine.dispose)
        # SQLite drops timezone offsets. Restore the known fixture zones on load;
        # PostgreSQL's DateTime(timezone=True) columns already preserve instants.
        def restore_order_zones(order, _context):
            for column in Order.__table__.c:
                value = getattr(order, column.name)
                if isinstance(column.type, DateTime) and value is not None and value.tzinfo is None:
                    zone = ZoneInfo("Asia/Taipei") if column.name == "provider_paid_at" else timezone.utc
                    setattr(order, column.name, value.replace(tzinfo=zone))
        event.listen(Order, "load", restore_order_zones)
        self.addCleanup(event.remove, Order, "load", restore_order_zones)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.enterContext(patch.dict(os.environ, {
            **STAGE_ENV, "RESEND_API_KEY": "synthetic-email-key",
            "RESEND_FROM_EMAIL": "support@example.com",
            "EMAIL_VERIFY_BASE_URL": "https://development.example.test",
            "CLAIM_RATE_LIMIT_KEY": "synthetic-http-chain-key-at-least-32-characters",
        }, clear=True))
        for module in ("orders", "checkout", "ecpay_return", "access"):
            self.enterContext(patch(f"app.api.{module}.get_session_factory", return_value=self.factory))
        self.enterContext(patch("app.services.entitlement_grant.get_session_factory", return_value=self.factory))
        self.enterContext(patch("app.main.get_engine", return_value=self.engine))
        self.delivery = self.enterContext(patch("app.services.email_delivery._deliver"))
        self.client = self.enterContext(TestClient(app))

    def delivered_token(self):
        text = self.delivery.call_args.args[0]["text"]
        return re.search(r"#token=([A-Za-z0-9_-]{43})", text).group(1)

    def new_order(self):
        response = self.client.post("/orders", json={"email": self.email})
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual((data["amount"], data["status"]), (199, "pending"))
        self.assertNotIn("device_credential", data)
        return UUID(data["order_id"]), self.delivered_token()

    def verified_checkout(self):
        order_id, token = self.new_order()
        verified = self.client.post("/orders/verify-email", json={"token": token})
        self.assertEqual(verified.status_code, 200, verified.text)
        response = self.client.post(f"/orders/{order_id}/checkout", json={})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["checkout_url"], STAGE_CHECKOUT_URL)
        self.assertEqual(data["fields"]["TotalAmount"], "199")
        return order_id, data["fields"]

    def callback(self, fields, **changes):
        payload = {
            "MerchantID": fields["MerchantID"], "MerchantTradeNo": fields["MerchantTradeNo"],
            "TradeAmt": fields["TotalAmount"], "RtnCode": "1", "TradeNo": "SyntheticTrade01",
            "PaymentType": "Credit_CreditCard", "PaymentDate": "2026/11/03 12:00:00",
            "SimulatePaid": "0", **changes,
        }
        payload["CheckMacValue"] = check_mac_value(payload, get_stage_config())
        return self.client.post("/payments/ecpay/return", data=payload)

    def activate(self):
        response = self.client.post("/access/email-links", json={"email": self.email})
        self.assertEqual(response.status_code, 202, response.text)
        token = self.delivered_token()
        redeemed = self.client.post("/access/email-links/redeem", json={"token": token})
        self.assertEqual(redeemed.status_code, 200, redeemed.text)
        proof = redeemed.json()["claim_session"]
        device_id = str(uuid4())
        registered = self.client.post("/access/devices", json={"claim_session": proof, "device_id": device_id})
        self.assertEqual(registered.status_code, 201, registered.text)
        self.assertEqual(registered.headers["Cache-Control"], "no-store")
        headers = {"Authorization": "Bearer " + registered.json()["device_credential"], "X-Device-Id": device_id}
        access = self.client.get("/access/status", headers=headers)
        self.assertEqual(access.status_code, 200, access.text)
        self.assertEqual(access.json()["status"], "active")
        return headers, token, proof

    def test_order_email_checkout_callback_claim_device_full_access(self):
        order_id, token = self.new_order()
        self.assertEqual(self.client.post(f"/orders/{order_id}/checkout", json={}).status_code, 409)
        self.assertEqual(self.client.post("/orders/verify-email", json={"token": token}).status_code, 200)
        self.assertEqual(self.client.post("/orders/verify-email", json={"token": token}).status_code, 400)
        for payload in ({"amount": 1}, {"status": "paid"}, {"ReturnURL": "https://wrong.example.test"}):
            self.assertEqual(self.client.post(f"/orders/{order_id}/checkout", json=payload).status_code, 422)
        checkout = self.client.post(f"/orders/{order_id}/checkout", json={})
        self.assertEqual(checkout.status_code, 200, checkout.text)
        data = checkout.json()
        self.assertEqual(data["checkout_url"], STAGE_CHECKOUT_URL)
        fields = data["fields"]
        self.assertEqual(fields["TotalAmount"], "199")
        self.assertEqual(self.callback(fields).text, "1|OK")
        self.assertEqual(self.callback(fields).text, "1|OK")
        with self.factory() as session:
            self.assertEqual(session.get(Order, order_id).status, "paid")
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 1)
            self.assertEqual(session.scalar(select(Entitlement.status)), "active")
        self.assertEqual(self.client.get("/access/status?full=true", headers={"X-Full-Access": "true"}).status_code, 401)
        _, claim_token, proof = self.activate()
        self.assertEqual(self.client.post("/access/email-links/redeem", json={"token": claim_token}).status_code, 400)
        self.assertEqual(self.client.post("/access/devices", json={"claim_session": proof, "device_id": str(uuid4())}).status_code, 401)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice)), 1)
        self.assertEqual(self.engine.pool.checkedout(), 0)

    def test_invalid_signature_amount_and_simulated_result_never_issue_access(self):
        order_id, fields = self.verified_checkout()
        self.assertEqual(self.callback(fields, TradeAmt="1").status_code, 409)
        bad = self.client.post("/payments/ecpay/return", data={
            "MerchantID": fields["MerchantID"], "MerchantTradeNo": fields["MerchantTradeNo"],
            "TradeAmt": "199", "RtnCode": "1", "CheckMacValue": "0" * 64,
        })
        self.assertEqual(bad.status_code, 403)
        self.assertEqual(self.callback(fields, SimulatePaid="1").text, "1|OK")
        self.assertEqual(self.callback(fields, RtnCode="10300066").text, "1|OK")
        with self.factory() as session:
            self.assertEqual(session.get(Order, order_id).status, "pending")
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 0)
        self.assertEqual(self.client.get("/access/status").status_code, 401)

    def test_interrupted_grant_keeps_paid_then_authenticated_retry_recovers(self):
        order_id, fields = self.verified_checkout()
        with patch("app.api.ecpay_return.grant_paid_order", side_effect=EntitlementGrantUnavailable()):
            self.assertEqual(self.callback(fields).status_code, 503)
        with self.factory() as session:
            self.assertEqual(session.get(Order, order_id).status, "paid")
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 0)
        self.assertEqual(self.callback(fields).text, "1|OK")
        self.activate()
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 1)

    def test_pending_refund_preserves_access_completed_refund_denies_access(self):
        order_id, fields = self.verified_checkout()
        self.assertEqual(self.callback(fields).text, "1|OK")
        headers, _, _ = self.activate()
        with self.factory.begin() as session:
            session.get(Order, order_id).status = "refund_pending"
        self.assertEqual(self.client.get("/access/status", headers=headers).status_code, 200)
        self.assertEqual(grant_paid_order(order_id, self.factory).status, "active")
        # Local confirmed-refund state only; no provider refund request.
        with self.factory.begin() as session:
            session.get(Order, order_id).status = "refunded"
            entitlement = session.scalar(select(Entitlement))
            entitlement.status = "revoked"
            entitlement.revoked_at = entitlement.activated_at
            entitlement.revocation_reason = "synthetic_confirmed_refund"
        self.assertEqual(self.client.get("/access/status", headers=headers).status_code, 401)
        with self.assertRaises(IneligibleEntitlementOrder):
            grant_paid_order(order_id, self.factory)
