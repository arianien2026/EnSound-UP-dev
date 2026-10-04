import hashlib
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.api.orders import VerifyEmailRequest, verification_page, verification_script, verify_email
from app.models import Order
from app.services.email_delivery import EmailDeliveryError, get_email_config, send_verification_email
from app.services.email_verification import (
    VERIFICATION_LIFETIME,
    consume_verification_token,
    is_payment_eligible,
    issue_verification_token,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def pending_order() -> Order:
    return Order(status="pending", expires_at=NOW + timedelta(hours=2), email_verified_at=None)


class FakeSession:
    def __init__(self, order: Order):
        self.order = order
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement):
        digest = statement.whereclause.right.value
        match = self.order if self.order.email_verification_token_hash == digest else None
        return FakeResult(match)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


class FakeResult:
    def __init__(self, order):
        self.order = order

    def scalar_one_or_none(self):
        return self.order


class VerificationTests(unittest.TestCase):
    def test_bilingual_verification_page_states(self) -> None:
        page = verification_page()
        script = verification_script()
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertEqual(page.headers["referrer-policy"], "no-referrer")
        self.assertEqual(page.headers["x-content-type-options"], "nosniff")
        self.assertIn("content-security-policy", page.headers)
        self.assertIn('id="result-zh">正在驗證 Email...', page.body.decode())
        self.assertIn('id="result-en" lang="en">Verifying your email...', page.body.decode())
        self.assertEqual(script.headers["cache-control"], "no-store")
        source = script.body.decode()
        for line in (
            "Email 驗證完成",
            "您的 Email 已確認，可以返回 EnSound UP。",
            "Email confirmed. You may return to EnSound UP.",
            "驗證連結無效或已過期",
            "請重新取得 Email 驗證連結。",
            "This verification link is invalid or has expired.",
            "目前無法完成驗證",
            "請稍後再試。",
            "Verification is unavailable. Please try again later.",
        ):
            with self.subTest(line=line):
                self.assertIn(line, source)

    def test_server_issues_random_token_storing_only_digest(self) -> None:
        order = pending_order()
        first = issue_verification_token(order, NOW)
        second = issue_verification_token(pending_order(), NOW)
        self.assertNotEqual(first, second)
        self.assertEqual(order.email_verification_token_hash, hashlib.sha256(first.encode()).hexdigest())
        self.assertNotEqual(order.email_verification_token_hash, first)
        self.assertEqual(order.email_verification_expires_at - NOW, VERIFICATION_LIFETIME)

    def test_valid_token_is_one_time_and_sets_server_timestamp(self) -> None:
        order = pending_order()
        token = issue_verification_token(order, NOW)
        session = FakeSession(order)
        self.assertTrue(consume_verification_token(session, token, NOW + timedelta(minutes=1)))
        self.assertEqual(order.email_verified_at, NOW + timedelta(minutes=1))
        self.assertIsNone(order.email_verification_token_hash)
        self.assertIsNone(order.email_verification_expires_at)
        self.assertFalse(consume_verification_token(session, token, NOW + timedelta(minutes=2)))
        self.assertEqual(order.status, "pending")
        self.assertIsNone(order.paid_at)

    def test_wrong_expired_and_wrong_status_rejected(self) -> None:
        for variation in ("wrong", "token_expired", "order_expired", "paid"):
            with self.subTest(variation=variation):
                order = pending_order()
                token = issue_verification_token(order, NOW)
                now = NOW + timedelta(minutes=1)
                if variation == "wrong":
                    token = issue_verification_token(pending_order(), NOW)
                elif variation == "token_expired":
                    now = NOW + VERIFICATION_LIFETIME
                elif variation == "order_expired":
                    order.expires_at = now
                else:
                    order.status = "paid"
                self.assertFalse(consume_verification_token(FakeSession(order), token, now))
                self.assertIsNone(order.email_verified_at)

    def test_payment_eligibility_requires_verified_live_pending_order(self) -> None:
        order = pending_order()
        self.assertFalse(is_payment_eligible(None, NOW))
        self.assertFalse(is_payment_eligible(order, NOW))
        order.email_verified_at = NOW
        self.assertTrue(is_payment_eligible(order, NOW))
        self.assertFalse(is_payment_eligible(order, order.expires_at))
        order.status = "paid"
        self.assertFalse(is_payment_eligible(order, NOW))

    def test_verify_endpoint_commits_then_rejects_reuse(self) -> None:
        now = datetime.now(timezone.utc)
        order = Order(status="pending", expires_at=now + timedelta(hours=2))
        token = issue_verification_token(order, now)
        session = FakeSession(order)
        with patch("app.api.orders.get_session_factory", return_value=lambda: session):
            self.assertEqual(verify_email(VerifyEmailRequest(token=token)), {"status": "email_verified"})
            with self.assertRaises(HTTPException) as caught:
                verify_email(VerifyEmailRequest(token=token))
        self.assertTrue(session.committed)
        self.assertEqual(caught.exception.status_code, 400)


class DeliveryTests(unittest.TestCase):
    def test_missing_api_key_fails_safely(self) -> None:
        with patch.dict(os.environ, {
            "RESEND_FROM_EMAIL": "onboarding@resend.dev",
            "EMAIL_VERIFY_BASE_URL": "http://127.0.0.1:8000",
        }, clear=True):
            with self.assertRaises(EmailDeliveryError):
                get_email_config()

    def test_resend_request_uses_body_and_fragment_without_network(self) -> None:
        with patch.dict(os.environ, {
            "RESEND_API_KEY": "fake-for-test-only",
            "RESEND_FROM_EMAIL": "onboarding@resend.dev",
            "EMAIL_VERIFY_BASE_URL": "http://127.0.0.1:8000",
        }, clear=True):
            config = get_email_config()

        class Accepted:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, *_args):
                return b'{"id":"mock-accepted"}'

        with patch("app.services.email_delivery.urlopen", return_value=Accepted()) as urlopen:
            token = issue_verification_token(pending_order(), NOW)
            send_verification_email("learner@example.com", token, config)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_header("User-agent"), "EnSound-UP/1.0")
        self.assertEqual(payload["to"], ["learner@example.com"])
        self.assertIn("/orders/verify-email#token=" + token, payload["text"])
        self.assertEqual(request.get_method(), "POST")


if __name__ == "__main__":
    unittest.main()
