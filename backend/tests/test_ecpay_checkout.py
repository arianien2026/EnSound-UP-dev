import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.checkout import CheckoutRequest, create_stage_checkout
from app.models import Order
from app.services.ecpay_stage import StageConfig, check_mac_value, get_stage_config
from app.services.pricing import PRODUCT_CODE


STAGE_ENV = {
    "ECPAY_STAGE_MERCHANT_ID": "test123",
    "ECPAY_STAGE_HASH_KEY": "K" * 16,
    "ECPAY_STAGE_HASH_IV": "I" * 16,
    "ECPAY_STAGE_RETURN_URL": "https://dev.example.test/ecpay/return",
}


def verified_order(**changes) -> Order:
    now = datetime.now(timezone.utc)
    values = dict(
        id=uuid4(), product_code=PRODUCT_CODE, amount=199, currency="TWD",
        status="pending", expires_at=now + timedelta(hours=1),
        email_verified_at=now, provider_order_id=None,
    )
    values.update(changes)
    return Order(**values)


class FakeSession:
    def __init__(self, order):
        self.order = order
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement):
        return self

    def scalar_one_or_none(self):
        return self.order

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


class ECPayStageCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, STAGE_ENV, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def checkout(self, order):
        session = FakeSession(order)
        with patch("app.api.checkout.get_session_factory", return_value=lambda: session):
            result = create_stage_checkout(order.id if order else uuid4())
        return result, session

    def test_verified_order_uses_stored_twd_integer_and_persists_unique_trade_number(self):
        order = verified_order()
        result, session = self.checkout(order)
        self.assertTrue(session.committed)
        self.assertEqual(result.checkout_url, "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5")
        fields = result.fields
        self.assertEqual(fields["TotalAmount"], "199")
        self.assertEqual(fields["PaymentType"], "aio")
        self.assertEqual(fields["ChoosePayment"], "ALL")
        self.assertEqual(fields["EncryptType"], "1")
        self.assertEqual(fields["ReturnURL"], STAGE_ENV["ECPAY_STAGE_RETURN_URL"])
        self.assertRegex(fields["MerchantTradeDate"], r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertRegex(fields["MerchantTradeNo"], r"^[A-Za-z0-9]{1,20}$")
        self.assertEqual(fields["MerchantTradeNo"], order.provider_order_id)
        self.assertNotEqual(fields["MerchantTradeNo"], self.checkout(verified_order())[0].fields["MerchantTradeNo"])
        self.assertEqual(fields["MerchantTradeNo"], self.checkout(order)[0].fields["MerchantTradeNo"])
        self.assertEqual(fields["CheckMacValue"], check_mac_value(
            {key: value for key, value in fields.items() if key != "CheckMacValue"}, get_stage_config()
        ))
        self.assertNotIn(STAGE_ENV["ECPAY_STAGE_HASH_KEY"], repr(result.model_dump()))
        self.assertNotIn(STAGE_ENV["ECPAY_STAGE_HASH_IV"], repr(result.model_dump()))
        self.assertEqual(order.status, "pending")
        self.assertIsNone(order.paid_at)

    def test_ineligible_orders_fail_closed(self):
        for name, order in (
            ("missing", None),
            ("unverified", verified_order(email_verified_at=None)),
            ("expired", verified_order(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))),
            ("paid", verified_order(status="paid")),
            ("cancelled", verified_order(status="cancelled")),
        ):
            with self.subTest(name=name), self.assertRaises(HTTPException) as caught:
                self.checkout(order)
            self.assertEqual(caught.exception.status_code, 409)
            if order:
                self.assertIsNone(order.provider_order_id)

    def test_client_price_and_payment_overrides_are_rejected(self):
        for key in ("amount", "currency", "product_code", "pricing_code", "MerchantID", "HashKey", "HashIV", "ReturnURL"):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                CheckoutRequest.model_validate({key: "1"})
        self.assertEqual(self.checkout(verified_order(amount=99))[0].fields["TotalAmount"], "99")
        for changes in ({"amount": 1.5}, {"amount": True}, {"amount": 0},
                        {"currency": "USD"}, {"product_code": "other"}):
            with self.subTest(changes=changes), self.assertRaises(HTTPException):
                self.checkout(verified_order(**changes))

    def test_missing_or_invalid_stage_configuration_fails_before_database_access(self):
        for overrides in ({}, {**STAGE_ENV, "ECPAY_STAGE_RETURN_URL": "http://localhost:8000/return"},
                          {**STAGE_ENV, "ECPAY_STAGE_HASH_KEY": "short"},
                          {**STAGE_ENV, "ECPAY_STAGE_MERCHANT_ID": "bad merchant"}):
            with self.subTest(overrides=overrides), patch.dict(os.environ, overrides, clear=True), patch(
                "app.api.checkout.get_session_factory"
            ) as factory, self.assertRaises(HTTPException) as caught:
                create_stage_checkout(uuid4())
            self.assertEqual(caught.exception.status_code, 503)
            self.assertEqual(caught.exception.detail, "Checkout unavailable")
            factory.assert_not_called()

    def test_official_all_in_one_checksum_example(self):
        # ECPay's published All-in-One checksum example, not private credentials:
        # https://developers.ecpay.com.tw/2902/
        published = StageConfig("3002607", "pwFHCqoQZGmho4w6", "EkRm7iFT261dpevs", "https://www.ecpay.com.tw/receive.php")
        fields = {
            "TradeDesc": "促銷方案", "PaymentType": "aio",
            "MerchantTradeDate": "2023/03/12 15:30:23", "MerchantTradeNo": "ecpay20230312153023",
            "MerchantID": "3002607", "ReturnURL": "https://www.ecpay.com.tw/receive.php",
            "ItemName": "Apple iphone 15", "TotalAmount": "30000",
            "ChoosePayment": "ALL", "EncryptType": "1",
        }
        self.assertEqual(check_mac_value(fields, published),
                         "6C51C9E6888DE861FD62FB1DD17029FC742634498FD813DC43D4243B5685B840")


if __name__ == "__main__":
    unittest.main()
