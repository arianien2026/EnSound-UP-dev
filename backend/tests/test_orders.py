import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.api.orders import CreateOrderRequest, create_order
from app.services.pricing import PRODUCT_CODE, PROMO_PRICE, REGULAR_PRICE, get_price


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(days=7)


class PricingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.promo = patch.dict(os.environ, {
            "LAUNCH_PROMO_START_AT": START.isoformat(),
            "LAUNCH_PROMO_END_AT": END.isoformat(),
        })
        self.promo.start()
        self.addCleanup(self.promo.stop)

    def test_promotion_boundaries(self) -> None:
        for when, expected in (
            (START - timedelta(microseconds=1), REGULAR_PRICE),
            (START, PROMO_PRICE),
            (START + timedelta(days=3), PROMO_PRICE),
            (END - timedelta(microseconds=1), PROMO_PRICE),
            (END, REGULAR_PRICE),
            (END + timedelta(seconds=1), REGULAR_PRICE),
        ):
            with self.subTest(when=when):
                self.assertEqual(get_price(now=when), expected)

    def test_missing_and_invalid_config_is_regular(self) -> None:
        for values in (
            {},
            {"LAUNCH_PROMO_START_AT": "not-a-date", "LAUNCH_PROMO_END_AT": END.isoformat()},
            {"LAUNCH_PROMO_START_AT": "2026-01-01T00:00:00", "LAUNCH_PROMO_END_AT": END.isoformat()},
            {"LAUNCH_PROMO_START_AT": START.isoformat(), "LAUNCH_PROMO_END_AT": START.isoformat()},
            {"LAUNCH_PROMO_START_AT": START.isoformat(), "LAUNCH_PROMO_END_AT": (END + timedelta(days=1)).isoformat()},
        ):
            with self.subTest(values=values), patch.dict(
                os.environ, values, clear=True
            ):
                self.assertEqual(get_price(now=START), REGULAR_PRICE)

    def test_unaware_test_time_rejected(self) -> None:
        with self.assertRaises(ValueError):
            get_price(now=datetime(2026, 1, 1))


class RequestTests(unittest.TestCase):
    def test_trims_and_normalizes_domain_without_rewriting_mailbox(self) -> None:
        request = CreateOrderRequest(email="  First.Last+tag@EXAMPLE.COM  ")
        self.assertEqual(request.email, "First.Last+tag@example.com")

    def test_invalid_email_and_client_controlled_fields_rejected(self) -> None:
        for payload in (
            {"email": "not an email"},
            {"email": "learner@example.com", "amount": 1},
            {"email": "learner@example.com", "product_code": "another"},
            {"email": "learner@example.com", "pricing_code": "launch_promo"},
            {"email": "learner@example.com", "status": "paid"},
            {"email": "learner@example.com", "expires_at": END.isoformat()},
            {"email": "learner@example.com", "paid_at": END.isoformat()},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                CreateOrderRequest.model_validate(payload)


class FakeSession:
    def __init__(self, fail_commit: bool = False) -> None:
        self.order = None
        self.committed = False
        self.rolled_back = False
        self.fail_commit = fail_commit

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def add(self, order):
        self.order = order

    def flush(self):
        self.order.id = uuid4()

    def commit(self):
        if self.fail_commit:
            raise OperationalError("INSERT", {}, Exception("database unavailable"))
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class OrderCreationTests(unittest.TestCase):
    def test_order_is_server_owned_pending_and_expires_in_two_hours(self) -> None:
        session = FakeSession()
        with patch("app.api.orders.get_session_factory", return_value=lambda: session):
            result = create_order(CreateOrderRequest(email=" Learner@EXAMPLE.COM "))
        self.assertTrue(session.committed)
        self.assertEqual(result.order_id, session.order.id)
        self.assertEqual(result.entitlement_email, "Learner@example.com")
        self.assertEqual(result.product_code, PRODUCT_CODE)
        self.assertEqual((result.amount, result.currency, result.pricing_code), (199, "TWD", "regular"))
        self.assertEqual(result.status, "pending")
        self.assertEqual(result.expires_at - result.created_at, timedelta(hours=2))
        self.assertEqual(session.order.paid_at, None)
        self.assertEqual(session.order.provider_order_id, None)

    def test_failed_commit_rolls_back_without_returning_order(self) -> None:
        session = FakeSession(fail_commit=True)
        with patch("app.api.orders.get_session_factory", return_value=lambda: session):
            with self.assertRaises(HTTPException) as caught:
                create_order(CreateOrderRequest(email="learner@example.com"))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, "Order service unavailable")
        self.assertTrue(session.rolled_back)


if __name__ == "__main__":
    unittest.main()
