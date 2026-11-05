import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException, Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.api.access import (
    ClaimLinkRequest, RedeemClaimRequest, _deliver_claim, claim_page, claim_script,
    request_claim_link, redeem_email_link,
)
from app.models import EmailClaimRateLimit, EmailClaimSession, EmailClaimToken, Entitlement, Order
from app.services.email_claim import (
    CLAIM_LIFETIME, SESSION_LIFETIME, EMAIL_MIN_INTERVAL, _count_request, _rate_key,
    get_rate_limit_key,
    issue_claim_if_eligible, redeem_claim,
)
from app.services.email_delivery import EmailConfig, EmailDeliveryError, send_claim_email
from app.services.pricing import PRODUCT_CODE


NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
KEY = b"local-isolated-test-secret-of-adequate-length"


class EmailClaimTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'claim.sqlite'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.patches = [
            patch("app.api.access.get_session_factory", return_value=self.factory),
            patch("app.api.access.get_rate_limit_key", return_value=KEY),
            patch("app.api.access.get_email_config"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def eligible(self, email="buyer@example.com"):
        order_id, entitlement_id = uuid4(), uuid4()
        with self.factory.begin() as session:
            session.add(Order(
                id=order_id, entitlement_email=email, product_code=PRODUCT_CODE,
                currency="TWD", amount=199, pricing_code="regular", status="paid",
                created_at=NOW, expires_at=NOW + timedelta(hours=2), paid_at=NOW,
                email_verified_at=NOW, provider_order_id="ES" + order_id.hex[:18],
                provider_trade_no=order_id.hex[:20], payment_environment="ecpay_stage",
                provider_payment_type="Credit_CreditCard", provider_paid_at=NOW,
            ))
            session.add(Entitlement(
                id=entitlement_id, source_order_id=order_id, product_code=PRODUCT_CODE,
                entitlement_email=email, status="active", activated_at=NOW, updated_at=NOW,
            ))
        return order_id, entitlement_id

    def issue(self, email="buyer@example.com", now=NOW):
        with self.factory.begin() as session:
            return issue_claim_if_eligible(session, email, "198.51.100.1", now, KEY)

    def request(self, email):
        tasks = BackgroundTasks()
        return request_claim_link(ClaimLinkRequest(email=email), self.http_request(), tasks), tasks

    def http_request(self):
        return Request({"type": "http", "client": ("198.51.100.1", 1234),
                        "method": "POST", "path": "/access/email-links", "headers": []})

    def test_generic_http_response_for_known_and_unknown_email(self):
        self.eligible()
        with patch("app.api.access._deliver_claim") as deliver:
            unknown, unknown_tasks = self.request("absent@example.com")
            known, known_tasks = self.request("buyer@example.com")
        self.assertEqual(unknown, known)
        self.assertNotIn("entitlement", str(known))
        self.assertEqual(len(unknown_tasks.tasks), 1)
        self.assertEqual(len(known_tasks.tasks), 1)
        self.assertIsNone(unknown_tasks.tasks[0].args[1])
        self.assertEqual(len(known_tasks.tasks[0].args[1]), 43)
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(EmailClaimToken)).all()), 1)
            self.assertEqual(len(session.scalars(select(EmailClaimRateLimit)).all()), 3)

    def test_digest_only_and_scope_limited_short_session(self):
        self.eligible()
        token = self.issue()
        self.assertEqual(len(token), 43)
        with self.factory() as session:
            record = session.scalar(select(EmailClaimToken))
            self.assertEqual(record.token_hash, hashlib.sha256(token.encode()).hexdigest())
            self.assertNotEqual(record.token_hash, token)
            self.assertEqual(record.purpose, "device_claim")
            self.assertEqual(record.expires_at - record.created_at, CLAIM_LIFETIME)
        with self.factory.begin() as session:
            scoped_session = redeem_claim(session, token, NOW + timedelta(minutes=1))
        self.assertEqual(len(scoped_session), 43)
        with self.factory() as session:
            record = session.scalar(select(EmailClaimSession))
            self.assertEqual(record.session_hash, hashlib.sha256(scoped_session.encode()).hexdigest())
            self.assertEqual(record.scope, "device_claim")
            self.assertEqual(record.expires_at - record.created_at, SESSION_LIFETIME)
            self.assertIsNone(record.consumed_at)
        self.assertEqual(set(Base.metadata.tables), {
            "orders", "entitlements", "email_claim_tokens", "email_claim_sessions",
            "email_claim_rate_limits", "authorized_devices",
        })

    def test_invalid_expired_replayed_and_refunded_claims(self):
        order_id, _ = self.eligible()
        token = self.issue()
        with self.factory.begin() as session:
            self.assertIsNone(redeem_claim(session, "x" * 43, NOW))
            self.assertIsNone(redeem_claim(session, token, NOW + CLAIM_LIFETIME))
            self.assertIsNotNone(redeem_claim(session, token, NOW + timedelta(minutes=1)))
            self.assertIsNone(redeem_claim(session, token, NOW + timedelta(minutes=2)))
        later = self.issue(now=NOW + timedelta(minutes=11))
        with self.factory.begin() as session:
            session.get(Order, order_id).status = "refunded"
        with self.factory.begin() as session:
            self.assertIsNone(redeem_claim(session, later, NOW + timedelta(minutes=12)))
        self.assertIsNone(self.issue(now=NOW + timedelta(minutes=22)))

    def test_refund_pending_claim_stays_eligible_and_one_time(self):
        order_id, entitlement_id = self.eligible()
        with self.factory.begin() as session:
            session.get(Order, order_id).status = "refund_pending"
        token = self.issue()
        self.assertIsNotNone(token)
        with self.factory.begin() as session:
            self.assertIsNotNone(redeem_claim(session, token, NOW + timedelta(minutes=1)))
            self.assertIsNone(redeem_claim(session, token, NOW + timedelta(minutes=2)))
        with self.factory() as session:
            self.assertEqual(session.get(Order, order_id).status, "refund_pending")
            self.assertEqual(session.get(Entitlement, entitlement_id).status, "active")
            self.assertIsNone(session.get(Entitlement, entitlement_id).revoked_at)

    def test_revoked_entitlement_blocks_existing_token_and_new_claim(self):
        order_id, entitlement_id = self.eligible()
        token = self.issue()
        self.assertIsNotNone(token)
        with self.factory.begin() as session:
            entitlement = session.get(Entitlement, entitlement_id)
            entitlement.status = "revoked"
            entitlement.revoked_at = NOW + timedelta(seconds=30)
            entitlement.revocation_reason = "confirmed_refund"
        with self.factory.begin() as session:
            self.assertIsNone(redeem_claim(session, token, NOW + timedelta(minutes=1)))
        self.assertIsNone(self.issue(now=NOW + timedelta(minutes=11)))
        with self.factory() as session:
            self.assertEqual(session.get(Order, order_id).status, "paid")
            self.assertEqual(session.get(Entitlement, entitlement_id).status, "revoked")
            self.assertEqual(len(session.scalars(select(EmailClaimSession)).all()), 0)

    def test_rate_limit_and_resend_are_shared_across_requests(self):
        self.eligible()
        first = self.issue()
        self.assertIsNone(self.issue(now=NOW))
        self.assertIsNone(self.issue(now=NOW + timedelta(minutes=9)))
        key = _rate_key(KEY, "email", "buyer@example.com")
        with self.factory() as session:
            row = session.get(EmailClaimRateLimit, key)
            self.assertEqual(row.request_count, 1)
            self.assertEqual(row.last_requested_at, NOW.replace(tzinfo=None))
        second = self.issue(now=NOW + timedelta(minutes=10))
        self.assertNotEqual(first, second)
        self.assertIsNotNone(second)
        self.assertIsNotNone(self.issue(now=NOW + timedelta(minutes=20)))
        self.assertIsNone(self.issue(now=NOW + timedelta(minutes=30)))  # three sends/hour
        with self.factory() as session:
            row = session.get(EmailClaimRateLimit, key)
            self.assertEqual(row.request_count, 3)
            self.assertEqual(row.last_requested_at, (NOW + timedelta(minutes=20)).replace(tzinfo=None))
        self.assertIsNotNone(self.issue(now=NOW + timedelta(minutes=61)))  # window reset
        self.assertIsNone(self.issue(now=NOW + timedelta(minutes=62)))

    def test_hour_boundary_does_not_erase_recent_resend_cooldown(self):
        key = "f" * 64

        def check(minutes):
            with self.factory.begin() as session:
                return _count_request(session, key, NOW + timedelta(minutes=minutes), 3,
                                      EMAIL_MIN_INTERVAL)

        self.assertTrue(check(0))
        self.assertTrue(check(20))
        self.assertTrue(check(59))
        self.assertFalse(check(60))  # New hourly window, but last send was one minute ago.
        with self.factory() as session:
            row = session.get(EmailClaimRateLimit, key)
            self.assertEqual(row.request_count, 0)
            self.assertEqual(row.window_started_at, (NOW + timedelta(minutes=60)).replace(tzinfo=None))
            self.assertEqual(row.last_requested_at, (NOW + timedelta(minutes=59)).replace(tzinfo=None))
        self.assertTrue(check(69))
        with self.factory() as session:
            self.assertEqual(session.get(EmailClaimRateLimit, key).request_count, 1)

    def test_get_page_does_not_consume_link_and_post_redeems_once(self):
        self.eligible()
        token = self.issue(now=datetime.now(timezone.utc))
        page = claim_page()
        script = claim_script()
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertEqual(page.headers["referrer-policy"], "no-referrer")
        self.assertIn("history.replaceState", script.body.decode())
        self.assertIn("addEventListener('click'", script.body.decode())
        self.assertIn("#access_claim=", script.body.decode())
        self.assertIn("button.disabled = false;", script.body.decode())
        self.assertNotIn("{once: true}", script.body.decode())
        with self.factory() as session:
            self.assertIsNone(session.scalar(select(EmailClaimToken)).consumed_at)
        response = redeem_email_link(RedeemClaimRequest(token=token), self.http_request())
        data = json.loads(response.body)
        self.assertEqual(set(data), {"claim_session", "scope", "expires_in"})
        self.assertEqual(data["scope"], "device_claim")
        self.assertEqual(response.headers["cache-control"], "no-store")
        with self.assertRaises(HTTPException) as caught:
            redeem_email_link(RedeemClaimRequest(token=token), self.http_request())
        self.assertEqual(caught.exception.status_code, 400)

    def test_request_schema_and_key_configuration(self):
        from pydantic import ValidationError
        for body in ({"email": "bad"}, {"email": "a@example.com", "paid": True}):
            with self.assertRaises(ValidationError):
                ClaimLinkRequest.model_validate(body)
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                get_rate_limit_key()

    def test_claim_delivery_uses_resend_and_no_plaintext_token_in_logs(self):
        config = EmailConfig("isolated-fake", "sender@example.com", "https://dev.example.com")

        class Accepted:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, *_args):
                return b'{"id":"accepted-in-test"}'

        with patch("app.services.email_delivery.urlopen", return_value=Accepted()) as urlopen:
            send_claim_email("buyer@example.com", "token-for-test", config)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertIn("/access/claim#token=token-for-test", payload["text"])
        self.assertEqual(payload["to"], ["buyer@example.com"])
        self.assertEqual(request.get_header("User-agent"), "EnSound-UP/1.0")
        with patch("app.api.access.send_claim_email", side_effect=EmailDeliveryError), patch(
            "app.api.access.logging.warning"
        ) as warning:
            _deliver_claim("buyer@example.com", "sensitive-token", config)
        self.assertNotIn("sensitive-token", str(warning.call_args))

    def test_ineligible_entitlement_does_not_send_and_config_failure_is_safe(self):
        order_id, _ = self.eligible()
        with self.factory.begin() as session:
            session.get(Order, order_id).status = "refunded"
        response, tasks = self.request("buyer@example.com")
        self.assertEqual(response, {"message": "If eligible, an access email will be sent."})
        self.assertIsNone(tasks.tasks[0].args[1])
        with patch("app.api.access.get_email_config", side_effect=EmailDeliveryError):
            with self.assertRaises(HTTPException) as caught:
                self.request("buyer@example.com")
        self.assertEqual(caught.exception.status_code, 503)

    def test_redeem_attempts_are_rate_limited_without_issuing_sessions(self):
        invalid = RedeemClaimRequest(token="A" * 43)
        for _ in range(60):
            with self.assertRaises(HTTPException) as caught:
                redeem_email_link(invalid, self.http_request())
            self.assertEqual(caught.exception.status_code, 400)
        with self.assertRaises(HTTPException) as caught:
            redeem_email_link(invalid, self.http_request())
        self.assertEqual(caught.exception.status_code, 429)
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(EmailClaimSession)).all()), 0)

    def test_db_failure_cannot_return_a_claim_session(self):
        self.eligible()
        token = self.issue(now=datetime.now(timezone.utc))
        self.engine.dispose()
        Path(self.engine.url.database).unlink()
        with self.assertRaises(HTTPException) as caught:
            redeem_email_link(RedeemClaimRequest(token=token), self.http_request())
        self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
