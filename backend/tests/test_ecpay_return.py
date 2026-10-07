import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from app.api.ecpay_return import receive_ecpay_stage_result
from app.models import Order
from app.services.ecpay_stage import check_mac_value, get_stage_config
from app.services.pricing import PRODUCT_CODE
from test_ecpay_checkout import STAGE_ENV


def pending_order(**changes):
    now = datetime.now(timezone.utc)
    values = dict(
        id=uuid4(), product_code=PRODUCT_CODE, currency="TWD", amount=199,
        status="pending", expires_at=now + timedelta(hours=1),
        email_verified_at=now, provider_order_id="ES" + uuid4().hex[:18],
    )
    values.update(changes)
    return Order(**values)


class FakeSession:
    def __init__(self, order, *, fail_commit=False):
        self.order = order
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = fail_commit

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement):
        self.statement = statement
        return self

    def scalar_one_or_none(self):
        return self.order

    def commit(self):
        if self.fail_commit:
            raise OperationalError("commit failed", {}, Exception("internal"))
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ECPayReturnTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, STAGE_ENV, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def fields(self, order, **changes):
        fields = dict(
            MerchantID=STAGE_ENV["ECPAY_STAGE_MERCHANT_ID"],
            MerchantTradeNo=order.provider_order_id,
            TradeAmt=str(order.amount), RtnCode="1", RtnMsg="Success",
            TradeNo="2412311225437371", PaymentType="Credit_CreditCard",
            PaymentDate="2024/12/31 12:26:09", SimulatePaid="0",
            CustomField1="", CustomField2="",
        )
        fields.update(changes)
        fields["CheckMacValue"] = check_mac_value(fields, get_stage_config())
        return fields

    def send(self, order, fields=None, *, body=None, content_type="application/x-www-form-urlencoded",
             fail_commit=False):
        session = FakeSession(order, fail_commit=fail_commit)
        if body is None:
            body = urlencode(fields if fields is not None else self.fields(order)).encode("utf-8")

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request({"type": "http", "method": "POST", "path": "/payments/ecpay/return",
                           "headers": [(b"content-type", content_type.encode("ascii"))]}, receive)
        with patch("app.api.ecpay_return.get_session_factory", return_value=lambda: session):
            try:
                return asyncio.run(receive_ecpay_stage_result(request)), session
            except HTTPException as error:
                return error, session

    def assert_error(self, result, status):
        self.assertIsInstance(result, HTTPException)
        self.assertEqual(result.status_code, status)
        self.assertNotIn("internal", str(result.detail))

    def test_authenticated_success_updates_only_order_payment_state_and_acks_exactly(self):
        order = pending_order()
        result, session = self.send(order)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body, b"1|OK")
        self.assertTrue(result.headers["content-type"].startswith("text/plain"))
        self.assertEqual(session.commits, 1)
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.provider_trade_no, "2412311225437371")
        self.assertEqual(order.payment_environment, "ecpay_stage")
        self.assertEqual(order.provider_payment_type, "Credit_CreditCard")
        self.assertEqual(order.provider_paid_at.utcoffset(), timedelta(hours=8))
        self.assertIsNotNone(order.paid_at)
        self.assertFalse(hasattr(order, "entitlement"))

    def test_late_authenticated_success_preserves_original_expiry_without_entitlement(self):
        expiry = datetime.now(timezone.utc) - timedelta(hours=3)
        order = pending_order(status="expired", expires_at=expiry)
        result, session = self.send(order)
        self.assertEqual(result.body, b"1|OK")
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.expires_at, expiry)
        self.assertEqual(session.commits, 1)
        self.assertFalse(hasattr(order, "entitlement"))

        pending = pending_order(expires_at=expiry)
        result, _ = self.send(pending)
        self.assertEqual(result.body, b"1|OK")
        self.assertEqual(pending.expires_at, expiry)

    def test_repeat_success_is_idempotent_but_conflicting_trade_identity_rejected(self):
        order = pending_order()
        fields = self.fields(order)
        self.send(order, fields)
        paid_at = order.paid_at
        result, session = self.send(order, fields)
        self.assertEqual(result.body, b"1|OK")
        self.assertEqual(session.commits, 0)
        self.assertEqual(order.paid_at, paid_at)
        for change in ({"TradeNo": "DIFFERENT"}, {"PaymentType": "ATM_ATM"},
                       {"PaymentDate": "2024/12/31 12:26:10"}):
            with self.subTest(change=change):
                result, session = self.send(order, self.fields(order, **change))
                self.assert_error(result, 409)
                self.assertEqual(session.commits, 0)

    def test_bad_mac_or_merchant_or_amount_never_acknowledged(self):
        for change, status in (({"MerchantID": "other"}, 400), ({"TradeAmt": "1"}, 409)):
            order = pending_order()
            result, session = self.send(order, self.fields(order, **change))
            self.assert_error(result, status)
            self.assertEqual(session.commits, 0)
            self.assertEqual(order.status, "pending")
        order = pending_order()
        fields = self.fields(order)
        fields["TradeAmt"] = "1"  # Signed payload tampering.
        result, session = self.send(order, fields)
        self.assert_error(result, 403)
        self.assertEqual(session.commits, 0)
        result, _ = self.send(None, self.fields(order))
        self.assert_error(result, 409)

    def test_unverified_wrong_product_and_bad_states_rejected(self):
        for changes in ({"email_verified_at": None}, {"product_code": "other"},
                        {"currency": "USD"}, {"status": "cancelled"},
                        {"status": "refund_pending"}):
            with self.subTest(changes=changes):
                order = pending_order(**changes)
                result, session = self.send(order)
                self.assert_error(result, 409)
                self.assertEqual(session.commits, 0)

    def test_simulation_and_failed_result_do_not_mark_paid(self):
        for changes in ({"SimulatePaid": "1"}, {"RtnCode": "10300066"}):
            order = pending_order()
            result, session = self.send(order, self.fields(order, **changes))
            self.assertEqual(result.body, b"1|OK")
            self.assertEqual(order.status, "pending")
            self.assertIsNone(order.paid_at)
            self.assertEqual(session.commits, 0)

    def test_missing_configuration_and_malformed_form_fail_closed(self):
        order = pending_order()
        fields = self.fields(order)
        for body, content_type in ((b"not_form", "application/x-www-form-urlencoded"),
                                   (b"{}", "application/json"),
                                   (urlencode(fields).encode() + b"&TradeAmt=199", "application/x-www-form-urlencoded"),
                                   (b"CheckMacValue=%XX", "application/x-www-form-urlencoded"),
                                   (b"x=" + b"a" * 17000, "application/x-www-form-urlencoded")):
            result, _ = self.send(order, body=body, content_type=content_type)
            self.assert_error(result, 400)
        with patch.dict(os.environ, {}, clear=True):
            result, _ = self.send(order, fields)
        self.assert_error(result, 503)

    def test_commit_failure_does_not_ack_and_rolls_back(self):
        order = pending_order()
        result, session = self.send(order, fail_commit=True)
        self.assert_error(result, 503)
        self.assertEqual(session.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
