"""Synthetic HTTP checkout and signed notifications. No provider or mail calls."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select

from app.models import Entitlement, Order
from app.services.ecpay_stage import (
    PRODUCTION_CALLBACK, PRODUCTION_CHECKOUT_URL, STAGE_CALLBACK,
    check_mac_value, get_payment_config,
)
import test_ecpay_http_chain as http_fixture

PROD_ENV = {
    "ECPAY_PAYMENT_ENV": "production",
    "ECPAY_PRODUCTION_CHECKOUT_ENABLED": "1",
    "ECPAY_PRODUCTION_MERCHANT_ID": "9876543210",
    "ECPAY_PRODUCTION_HASH_KEY": "SyntheticKey1234",
    "ECPAY_PRODUCTION_HASH_IV": "SyntheticIV12345",
    "ECPAY_PRODUCTION_RETURN_URL": "https://payment.example.test" + PRODUCTION_CALLBACK,
    "ENSOUND_PRODUCTION_FRONTEND_URL": "https://buyer.example.test/",
    "EMAIL_VERIFY_BASE_URL": "https://payment.example.test",
}


class ProductionPaymentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = http_fixture.ECPayHttpChainTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.client = self.fixture.client
        stage_keys = ["ECPAY_STAGE_" + key for key in ("MERCHANT_ID", "HASH_KEY", "HASH_IV", "RETURN_URL")]
        self.enterContext(patch.dict(os.environ, {**dict.fromkeys(stage_keys, ""), **PROD_ENV}))

    def checkout(self):
        payload = {"email": self.fixture.email, "idempotency_key": str(uuid4())}
        order = self.client.post("/orders", json=payload)
        self.assertEqual(order.status_code, 201)
        self.assertEqual(self.client.post("/orders", json=payload).json(), order.json())
        self.assertEqual(self.fixture.delivery.call_count, 1)
        self.assertEqual(self.client.post("/orders", json=payload | {"email": "other@example.com"}).status_code, 409)
        endpoint = f"/orders/{order.json()['order_id']}/checkout"
        self.assertEqual(self.client.post(endpoint, json={}).status_code, 409)
        self.assertEqual(self.client.post("/orders/verify-email", json={"token": self.fixture.delivered_token()}).status_code, 200)
        result = self.client.post(endpoint, json={}).json()
        self.assertEqual(self.client.post(endpoint, json={}).json(), result)
        self.assertEqual(result["checkout_url"], PRODUCTION_CHECKOUT_URL)
        self.assertEqual(result["fields"]["ReturnURL"], PROD_ENV["ECPAY_PRODUCTION_RETURN_URL"])
        self.assertEqual(result["fields"]["ClientBackURL"], PROD_ENV["ENSOUND_PRODUCTION_FRONTEND_URL"])
        self.assertFalse({"HashKey", "HashIV"} & result["fields"].keys())
        return result["fields"]

    def callback(self, fields, path=PRODUCTION_CALLBACK, forged=False, **changes):
        payload = {
            "MerchantID": fields["MerchantID"], "MerchantTradeNo": fields["MerchantTradeNo"],
            "TradeAmt": fields["TotalAmount"], "RtnCode": "1", "TradeNo": "SyntheticProd01",
            "PaymentType": "Credit_CreditCard", "PaymentDate": "2026/11/03 12:00:00",
            "SimulatePaid": "0",
        } | changes
        payload["CheckMacValue"] = "0" * 64 if forged else check_mac_value(payload, get_payment_config())
        return self.client.post(path, data=payload)

    def test_production_configuration_and_callback_acceptance(self):
        self.assertEqual(self.client.post("/orders", json={"email": self.fixture.email}).status_code, 422)
        fields = self.checkout()
        self.assertEqual(self.callback(fields, path=STAGE_CALLBACK).status_code, 404)
        self.assertEqual(self.callback(fields, forged=True).status_code, 403)
        for changes, expected in (({"MerchantID": "111111"}, 400), ({"Currency": "USD"}, 400),
                                  ({"TradeAmt": "1"}, 409), ({"MerchantTradeNo": "UnknownOrder"}, 409)):
            self.assertEqual(self.callback(fields, **changes).status_code, expected)
        self.assertEqual(self.callback(fields, SimulatePaid="1").text, "1|OK")
        with self.fixture.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 0)
        with patch.dict(os.environ, {"ECPAY_PRODUCTION_CHECKOUT_ENABLED": "0"}):
            self.assertEqual(self.client.post("/orders", json={"email": self.fixture.email}).status_code, 503)
            with self.fixture.factory() as session:
                order_id = session.scalar(select(Order.id))
            self.assertEqual(self.client.post(f"/orders/{order_id}/checkout", json={}).status_code, 503)
            # Paused sales still honor authentic payments already submitted.
            self.assertEqual(self.callback(fields).text, "1|OK")
        self.assertEqual(self.callback(fields).text, "1|OK")
        self.assertEqual(self.callback(fields, TradeNo="DifferentTrade").status_code, 409)
        with self.fixture.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 1)
            order = session.scalar(select(Order))
            self.assertEqual((order.status, order.payment_environment), ("paid", "ecpay_production"))
        with patch.dict(os.environ, {"ECPAY_PAYMENT_ENV": "stage"}):
            self.assertEqual(self.client.post(PRODUCTION_CALLBACK, data={}).status_code, 404)

    def test_stage_order_rejected_even_with_a_valid_production_signature(self):
        fields = self.checkout()
        with self.fixture.factory.begin() as session:
            order = session.scalar(select(Order))
            order.payment_environment = "ecpay_stage"
            order.checkout_merchant_id = None
        self.assertEqual(self.callback(fields).status_code, 409)
        with self.fixture.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement)), 0)

    def test_production_is_fail_closed_on_development_target_or_mixed_credentials(self):
        for config in ({"RENDER_SERVICE_ID": "srv-db33dsm7bikc73bhukug"},
                       {"ENSOUND_DEV_DATABASE_HOST": "dev.example.test"},
                       {"ECPAY_STAGE_MERCHANT_ID": "3002607"},
                       {"ECPAY_PRODUCTION_MERCHANT_ID": "3002607"},
                       {"ECPAY_PRODUCTION_RETURN_URL": "https://payment.example.test" + STAGE_CALLBACK}):
            with self.subTest(config=config), patch.dict(os.environ, config), self.assertRaises(ValueError):
                get_payment_config()


    def test_database_lock_requires_separate_production_target_and_tls(self):
        from sqlalchemy.engine import make_url
        from app.database import get_database_url
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:fixture@pg.example.test/commerce"}):
            with self.assertRaises(ValueError):
                get_database_url()
            with patch.dict(os.environ, {"ENSOUND_PRODUCTION_DATABASE_HOST": "pg.example.test",
                                         "ENSOUND_PRODUCTION_DATABASE_NAME": "commerce"}):
                self.assertEqual(make_url(get_database_url()).query["sslmode"], "verify-full")
                with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:fixture@other.example.test/commerce"}):
                    with self.assertRaises(ValueError):
                        get_database_url()


    def test_production_verification_links_cannot_use_another_origin(self):
        from app.services.email_delivery import EmailDeliveryError, get_email_config
        self.assertEqual(get_email_config().base_url, "https://payment.example.test")
        with patch.dict(os.environ, {"EMAIL_VERIFY_BASE_URL": "https://wrong.example.test"}):
            with self.assertRaises(EmailDeliveryError):
                get_email_config()
