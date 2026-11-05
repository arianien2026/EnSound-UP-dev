"""Opt-in integration check against a fresh, dedicated local PostgreSQL DB only.

Set ENSOUND_3A_ISOLATED_POSTGRES_URL to a PostgreSQL/psycopg URL for a
*pristine* localhost database named ensound_3a_isolated_test, using the
ensound_isolated_test role. Never point a localhost tunnel at a remote DB.
This test never uses DATABASE_URL and skips when no URL is supplied.
"""

import asyncio
import hashlib
import ipaddress
import os
import secrets
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
from urllib.parse import urlencode
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.database import Base
from app.api.ecpay_return import receive_ecpay_stage_result
from app.models import AuthorizedDevice, EmailClaimSession, EmailClaimToken, Entitlement, Order
from app.services.email_claim import EMAIL_MIN_INTERVAL, _count_request, issue_claim_if_eligible, redeem_claim
from app.services.device_access import DeviceLimitReached, register_device, verify_online_access
from app.services.device_release import ReleaseCooldown, release_device
from app.services.ecpay_stage import check_mac_value, get_stage_config
from app.services.entitlement_grant import EntitlementGrantUnavailable, grant_paid_order
from app.services.entitlement_reconcile import _issue_recovery_permit, recovery_candidates, reconcile_order_ids
from app.services.pricing import PRODUCT_CODE
from test_ecpay_checkout import STAGE_ENV


TEST_DATABASE = "ensound_3a_isolated_test"
TEST_ROLE = "ensound_isolated_test"
MARKER_TABLE = "ensound_fixture_marker"
TEST_PORT = 55432


def _validated_test_url(raw_url):
    url = make_url(raw_url)
    if (url.drivername != "postgresql+psycopg" or
        url.host not in ("localhost", "127.0.0.1") or
        url.database != TEST_DATABASE or url.username != TEST_ROLE or
        url.query or not url.password or url.port != TEST_PORT):
        raise ValueError("Refusing non-isolated PostgreSQL integration target")
    return url


def _assert_local_test_connection(connection):
    database, role, address, schema, port = connection.execute(text(
        "SELECT current_database(), current_user, inet_server_addr()::text, current_schema(), inet_server_port()"
    )).one()
    try:
        server = ipaddress.ip_interface(address)
        local_server = (server.ip in (ipaddress.IPv4Address("127.0.0.1"),
                                      ipaddress.IPv6Address("::1")) and
                        server.network.prefixlen == server.max_prefixlen)
    except (ValueError, TypeError):
        local_server = False
    if (database != TEST_DATABASE or role != TEST_ROLE or
        not local_server or schema != "public" or port != TEST_PORT):
        raise ValueError("Refusing non-local or non-test PostgreSQL connection")


class IsolatedPostgresGrantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw_url = os.environ.get("ENSOUND_3A_ISOLATED_POSTGRES_URL")
        if not raw_url:
            raise unittest.SkipTest("No isolated local PostgreSQL URL configured")
        url = _validated_test_url(raw_url)
        cls.engine = create_engine(url)
        cls.marker = secrets.token_hex(32)
        cls.marker_created = False
        try:
            with cls.engine.begin() as connection:
                _assert_local_test_connection(connection)
                if connection.scalar(text(
                    "SELECT count(*) FROM pg_catalog.pg_tables "
                    "WHERE schemaname NOT LIKE 'pg%' "
                    "AND schemaname != 'information_schema'"
                )):
                    raise ValueError("Isolated PostgreSQL integration database must be empty")
                connection.execute(text(f"CREATE TABLE {MARKER_TABLE} (run_id text NOT NULL)"))
                connection.execute(text(f"INSERT INTO {MARKER_TABLE} (run_id) VALUES (:run_id)"),
                                   {"run_id": cls.marker})
            cls.marker_created = True
            cls.config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            with patch("app.database.get_database_url", return_value=raw_url):
                command.upgrade(cls.config, "head")
            cls.factory = sessionmaker(bind=cls.engine)
        except BaseException:
            try:
                if cls.marker_created:
                    cls._clean_test_tables()
            finally:
                cls.engine.dispose()
            raise

    @classmethod
    def _clean_test_tables(cls):
        # No Alembic downgrade: a successful device release intentionally
        # prevents removal of its 30-day cooldown history in real databases.
        with cls.engine.begin() as connection:
            _assert_local_test_connection(connection)
            found = set(inspect(connection).get_table_names())
            expected = set(Base.metadata.tables) | {"alembic_version", MARKER_TABLE}
            if not found <= expected or MARKER_TABLE not in found:
                raise ValueError("Unexpected tables in isolated PostgreSQL database")
            rows = connection.execute(text(f"SELECT run_id FROM {MARKER_TABLE}")).scalars().all()
            if rows != [cls.marker]:
                raise ValueError("Isolated PostgreSQL fixture identity mismatch")
            Base.metadata.drop_all(bind=connection)
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
            connection.execute(text(f"DROP TABLE {MARKER_TABLE}"))

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.marker_created:
                cls._clean_test_tables()
        finally:
            cls.engine.dispose()


    def test_migration_constraints_and_two_concurrent_grants(self):
        inspector = inspect(self.engine)
        self.assertEqual(
            {"uq_entitlements_source_order_id"},
            {entry["name"] for entry in inspector.get_unique_constraints("entitlements")},
        )
        self.assertTrue({"ck_entitlements_status", "ck_entitlements_activation",
                         "ck_entitlements_grant_failure", "ck_entitlements_revocation"} <=
                        {entry["name"] for entry in inspector.get_check_constraints("entitlements")})
        now = datetime.now(timezone.utc)
        order_id = uuid4()
        with self.factory.begin() as session:
            session.add(Order(
                id=order_id, entitlement_email="pg-test@example.com", product_code=PRODUCT_CODE,
                currency="TWD", amount=199, pricing_code="regular", status="paid",
                created_at=now, expires_at=now + timedelta(hours=2), paid_at=now,
                email_verified_at=now, provider_order_id="ES" + order_id.hex[:18],
                provider_trade_no=order_id.hex[:20], payment_environment="ecpay_stage",
                provider_payment_type="Credit_CreditCard", provider_paid_at=now,
            ))
        start = Event()

        def attempt():
            start.wait()
            return grant_paid_order(order_id, self.factory)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            start.set()
            first, second = [future.result(timeout=20) for future in futures]
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.status, "active")
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement).where(
                Entitlement.source_order_id == order_id)), 1)

    def test_concurrent_authenticated_callbacks_commit_one_payment_and_one_active_grant(self):
        now, order_id = datetime.now(timezone.utc), uuid4()
        trade_no = "ES" + order_id.hex[:18]
        with self.factory.begin() as session:
            session.add(Order(
                id=order_id, entitlement_email="callback-pg@example.com",
                product_code=PRODUCT_CODE, currency="TWD", amount=199,
                pricing_code="regular", status="pending", created_at=now,
                expires_at=now + timedelta(hours=2), email_verified_at=now,
                provider_order_id=trade_no,
            ))
        with patch.dict(os.environ, STAGE_ENV):
            fields = {
                "MerchantID": STAGE_ENV["ECPAY_STAGE_MERCHANT_ID"],
                "MerchantTradeNo": trade_no, "TradeAmt": "199", "RtnCode": "1",
                "TradeNo": order_id.hex[:20], "PaymentType": "Credit_CreditCard",
                "PaymentDate": "2024/12/31 12:26:09", "SimulatePaid": "0",
            }
            fields["CheckMacValue"] = check_mac_value(fields, get_stage_config())
            body = urlencode(fields).encode("utf-8")
            start = Event()

            def attempt():
                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}

                request = Request({
                    "type": "http", "method": "POST", "path": "/payments/ecpay/return",
                    "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
                }, receive)
                start.wait()
                return asyncio.run(receive_ecpay_stage_result(request)).body

            with (patch("app.api.ecpay_return.get_session_factory", return_value=self.factory),
                  patch("app.api.ecpay_return.grant_paid_order",
                        side_effect=lambda id: grant_paid_order(id, self.factory))):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(attempt) for _ in range(2)]
                    start.set()
                    responses = [future.result(timeout=20) for future in futures]
        self.assertEqual(responses, [b"1|OK", b"1|OK"])
        with self.factory() as session:
            order = session.get(Order, order_id)
            self.assertEqual(order.status, "paid")
            self.assertEqual(order.provider_trade_no, order_id.hex[:20])
            entitlements = session.execute(select(Entitlement).where(
                Entitlement.source_order_id == order_id)).scalars().all()
            self.assertEqual(len(entitlements), 1)
            self.assertEqual(entitlements[0].status, "active")

    def test_callback_grant_failure_and_operator_recovery_are_durable(self):
        now, order_id = datetime.now(timezone.utc), uuid4()
        trade_no = "ES" + order_id.hex[:18]
        with self.factory.begin() as session:
            session.add(Order(
                id=order_id, entitlement_email="recovery-pg@example.com",
                product_code=PRODUCT_CODE, currency="TWD", amount=199,
                pricing_code="regular", status="pending", created_at=now,
                expires_at=now + timedelta(hours=2), email_verified_at=now,
                provider_order_id=trade_no,
            ))

        with patch.dict(os.environ, STAGE_ENV):
            fields = {
                "MerchantID": STAGE_ENV["ECPAY_STAGE_MERCHANT_ID"],
                "MerchantTradeNo": trade_no, "TradeAmt": "199", "RtnCode": "1",
                "TradeNo": order_id.hex[:20], "PaymentType": "Credit_CreditCard",
                "PaymentDate": "2024/12/31 12:26:09", "SimulatePaid": "0",
            }
            fields["CheckMacValue"] = check_mac_value(fields, get_stage_config())
            body = urlencode(fields).encode("utf-8")

            def callback():
                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}

                request = Request({
                    "type": "http", "method": "POST", "path": "/payments/ecpay/return",
                    "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
                }, receive)
                return asyncio.run(receive_ecpay_stage_result(request))

            with patch("app.api.ecpay_return.get_session_factory", return_value=self.factory), \
                 patch("app.services.entitlement_grant.get_session_factory", return_value=self.factory):
                with patch("app.api.ecpay_return.grant_paid_order",
                           side_effect=EntitlementGrantUnavailable("test failure")):
                    with self.assertRaises(HTTPException) as failure:
                        callback()
                self.assertEqual(failure.exception.status_code, 503)
                with self.factory() as session:
                    order = session.get(Order, order_id)
                    self.assertEqual(order.status, "paid")
                    self.assertEqual(order.provider_trade_no, order_id.hex[:20])
                    self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement).where(
                        Entitlement.source_order_id == order_id)), 0)
                self.assertEqual(recovery_candidates(self.factory, order_id=order_id), [order_id])
                permit = _issue_recovery_permit(self.factory, [order_id])
                outcome = reconcile_order_ids([order_id], self.factory, permit=permit)
                self.assertEqual((outcome.activated, outcome.failed), (1, 0))
                self.assertEqual(callback().body, b"1|OK")

        with self.factory() as session:
            entitlement = session.scalar(select(Entitlement).where(
                Entitlement.source_order_id == order_id))
            self.assertEqual(entitlement.status, "active")
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement).where(
                Entitlement.source_order_id == order_id)), 1)
        self.assertEqual(recovery_candidates(self.factory, order_id=order_id), [])

    def test_recovery_excludes_refunded_and_revoked_records(self):
        now = datetime.now(timezone.utc)
        refunded_id, revoked_id = uuid4(), uuid4()
        for order_id, status in ((refunded_id, "refunded"), (revoked_id, "paid")):
            with self.factory.begin() as session:
                session.add(Order(
                    id=order_id, entitlement_email="blocked-pg@example.com",
                    product_code=PRODUCT_CODE, currency="TWD", amount=199,
                    pricing_code="regular", status=status, created_at=now,
                    expires_at=now + timedelta(hours=2), paid_at=now,
                    email_verified_at=now, provider_order_id="ES" + order_id.hex[:18],
                    provider_trade_no=order_id.hex[:20], payment_environment="ecpay_stage",
                    provider_payment_type="Credit_CreditCard", provider_paid_at=now,
                ))
        with self.factory.begin() as session:
            session.add(Entitlement(
                source_order_id=revoked_id, product_code=PRODUCT_CODE,
                entitlement_email="blocked-pg@example.com", status="revoked",
                activated_at=now, revoked_at=now, revocation_reason="confirmed_refund",
                updated_at=now,
            ))
        self.assertEqual(recovery_candidates(self.factory, order_id=refunded_id), [])
        self.assertEqual(recovery_candidates(self.factory, order_id=revoked_id), [])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement).where(
                Entitlement.source_order_id == refunded_id)), 0)
            self.assertEqual(session.scalar(select(Entitlement.status).where(
                Entitlement.source_order_id == revoked_id)), "revoked")

    def test_invalid_and_simulated_callbacks_never_grant(self):
        now, order_id = datetime.now(timezone.utc), uuid4()
        trade_no = "ES" + order_id.hex[:18]
        with self.factory.begin() as session:
            session.add(Order(
                id=order_id, entitlement_email="invalid-pg@example.com",
                product_code=PRODUCT_CODE, currency="TWD", amount=199,
                pricing_code="regular", status="pending", created_at=now,
                expires_at=now + timedelta(hours=2), email_verified_at=now,
                provider_order_id=trade_no,
            ))

        with patch.dict(os.environ, STAGE_ENV), patch(
            "app.api.ecpay_return.get_session_factory", return_value=self.factory
        ), patch("app.api.ecpay_return.grant_paid_order") as grant:
            fields = {
                "MerchantID": STAGE_ENV["ECPAY_STAGE_MERCHANT_ID"],
                "MerchantTradeNo": trade_no, "TradeAmt": "199", "RtnCode": "1",
                "TradeNo": order_id.hex[:20], "PaymentType": "Credit_CreditCard",
                "PaymentDate": "2024/12/31 12:26:09", "SimulatePaid": "0",
            }

            def send(fields):
                async def receive():
                    return {"type": "http.request", "body": urlencode(fields).encode(),
                            "more_body": False}

                request = Request({
                    "type": "http", "method": "POST", "path": "/payments/ecpay/return",
                    "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
                }, receive)
                return asyncio.run(receive_ecpay_stage_result(request))

            for changed, status in (({"MerchantID": "wrong"}, 400),
                                    ({"TradeAmt": "1"}, 409)):
                signed = {**fields, **changed}
                signed["CheckMacValue"] = check_mac_value(signed, get_stage_config())
                with self.assertRaises(HTTPException) as rejection:
                    send(signed)
                self.assertEqual(rejection.exception.status_code, status)

            bad_mac = {**fields, "CheckMacValue": "0" * 64}
            with self.assertRaises(HTTPException) as rejection:
                send(bad_mac)
            self.assertEqual(rejection.exception.status_code, 403)

            for changed in ({"SimulatePaid": "1"}, {"RtnCode": "10300066"}):
                signed = {**fields, **changed}
                signed["CheckMacValue"] = check_mac_value(signed, get_stage_config())
                self.assertEqual(send(signed).body, b"1|OK")
            grant.assert_not_called()

        with self.factory() as session:
            self.assertEqual(session.get(Order, order_id).status, "pending")
            self.assertEqual(session.scalar(select(func.count()).select_from(Entitlement).where(
                Entitlement.source_order_id == order_id)), 0)

    def test_concurrent_email_claim_redemption_is_one_time(self):
        now = datetime.now(timezone.utc)
        order_id = uuid4()
        with self.factory.begin() as session:
            session.add(Order(
                id=order_id, entitlement_email="claim-pg@example.com", product_code=PRODUCT_CODE,
                currency="TWD", amount=199, pricing_code="regular", status="paid",
                created_at=now, expires_at=now + timedelta(hours=2), paid_at=now,
                email_verified_at=now, provider_order_id="ES" + order_id.hex[:18],
                provider_trade_no=order_id.hex[:20], payment_environment="ecpay_stage",
                provider_payment_type="Credit_CreditCard", provider_paid_at=now,
            ))
            session.flush()
            session.add(Entitlement(
                source_order_id=order_id, product_code=PRODUCT_CODE,
                entitlement_email="claim-pg@example.com", status="active",
                activated_at=now, updated_at=now,
            ))
        with self.factory.begin() as session:
            token = issue_claim_if_eligible(session, "claim-pg@example.com",
                                            "127.0.0.1", now, b"isolated-test-rate-limit-key-32-bytes")
        self.assertIsNotNone(token)
        barrier = Event()

        def attempt():
            barrier.wait()
            with self.factory.begin() as session:
                return redeem_claim(session, token, now + timedelta(seconds=1))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            barrier.set()
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sum(result is not None for result in results), 1)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(EmailClaimSession)), 1)
            self.assertIsNotNone(session.scalar(select(EmailClaimToken).where(
                EmailClaimToken.entitlement_id == session.scalar(select(Entitlement.id).where(
                    Entitlement.source_order_id == order_id))
            )).consumed_at)

    def test_concurrent_first_request_respects_email_cooldown(self):
        now = datetime.now(timezone.utc)
        key = "a" * 64
        start = Event()

        def attempt():
            start.wait()
            with self.factory.begin() as session:
                return _count_request(session, key, now, 3, EMAIL_MIN_INTERVAL)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            start.set()
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(results), [False, True])

    def test_two_concurrent_claims_cannot_fill_one_remaining_slot_twice(self):
        now, order_id, entitlement_id = datetime.now(timezone.utc), uuid4(), uuid4()
        claims = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
        with self.factory.begin() as session:
            session.add(Order(
                id=order_id, entitlement_email="device-pg@example.com", product_code=PRODUCT_CODE,
                currency="TWD", amount=199, pricing_code="regular", status="paid",
                created_at=now, expires_at=now + timedelta(hours=2), paid_at=now,
                email_verified_at=now, provider_order_id="ES" + order_id.hex[:18],
                provider_trade_no=order_id.hex[:20], payment_environment="ecpay_stage",
                provider_payment_type="Credit_CreditCard", provider_paid_at=now,
            ))
            session.flush()
            session.add(Entitlement(
                id=entitlement_id, source_order_id=order_id, product_code=PRODUCT_CODE,
                entitlement_email="device-pg@example.com", status="active",
                activated_at=now, updated_at=now,
            ))
            session.flush()
            for _ in range(2):
                session.add(AuthorizedDevice(
                    entitlement_id=entitlement_id, device_id=uuid4(),
                    credential_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                    registered_at=now, updated_at=now,
                    credential_expires_at=now + timedelta(days=30),
                ))
            for token in claims:
                session.add(EmailClaimSession(
                    entitlement_id=entitlement_id,
                    session_hash=hashlib.sha256(token.encode()).hexdigest(),
                    scope="device_claim", created_at=now,
                    expires_at=now + timedelta(minutes=10),
                ))
        start = Event()

        def attempt(token):
            start.wait()
            try:
                with self.factory.begin() as session:
                    register_device(session, token, uuid4(), now + timedelta(seconds=1))
                return "registered"
            except DeviceLimitReached:
                return "limited"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt, token) for token in claims]
            start.set()
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(results), ["limited", "registered"])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice).where(
                AuthorizedDevice.entitlement_id == entitlement_id)), 3)
            self.assertEqual(session.scalar(select(func.count()).select_from(EmailClaimSession).where(
                EmailClaimSession.entitlement_id == entitlement_id,
                EmailClaimSession.consumed_at.is_not(None))), 1)

    def test_concurrent_release_cooldown_and_registration_limit(self):
        now, order_id, entitlement_id = datetime.now(timezone.utc), uuid4(), uuid4()
        devices = [(uuid4(), secrets.token_urlsafe(32)) for _ in range(3)]
        releases = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
        claim = secrets.token_urlsafe(32)
        with self.factory.begin() as session:
            session.add(Order(
                id=order_id, entitlement_email="release-pg@example.com", product_code=PRODUCT_CODE,
                currency="TWD", amount=199, pricing_code="regular", status="paid",
                created_at=now, expires_at=now + timedelta(hours=2), paid_at=now,
                email_verified_at=now, provider_order_id="ES" + order_id.hex[:18],
                provider_trade_no=order_id.hex[:20], payment_environment="ecpay_stage",
                provider_payment_type="Credit_CreditCard", provider_paid_at=now,
            ))
            session.flush()
            session.add(Entitlement(
                id=entitlement_id, source_order_id=order_id, product_code=PRODUCT_CODE,
                entitlement_email="release-pg@example.com", status="active",
                activated_at=now, updated_at=now,
            ))
            session.flush()
            for device_id, credential in devices:
                session.add(AuthorizedDevice(
                    entitlement_id=entitlement_id, device_id=device_id,
                    credential_hash=hashlib.sha256(credential.encode()).hexdigest(),
                    registered_at=now, updated_at=now,
                    credential_expires_at=now + timedelta(days=30),
                ))
            for proof in releases:
                session.add(EmailClaimSession(
                    entitlement_id=entitlement_id,
                    session_hash=hashlib.sha256(proof.encode()).hexdigest(),
                    scope="device_release", created_at=now,
                    expires_at=now + timedelta(minutes=10),
                ))
            session.add(EmailClaimSession(
                entitlement_id=entitlement_id,
                session_hash=hashlib.sha256(claim.encode()).hexdigest(),
                scope="device_claim", created_at=now,
                expires_at=now + timedelta(minutes=10),
            ))
        start = Event()

        def release_attempt(index):
            start.wait()
            try:
                with self.factory.begin() as session:
                    release_device(session, releases[index], devices[index][0], now)
                return "released"
            except ReleaseCooldown:
                return "cooldown"

        def register_attempt():
            start.wait()
            try:
                with self.factory.begin() as session:
                    register_device(session, claim, uuid4(), now + timedelta(seconds=1))
                return "registered"
            except DeviceLimitReached:
                return "limited"

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(release_attempt, index) for index in range(2)]
            registration = pool.submit(register_attempt)
            start.set()
            self.assertEqual(sorted(future.result(timeout=20) for future in futures),
                             ["cooldown", "released"])
            registration_result = registration.result(timeout=20)
        self.assertIn(registration_result, ("limited", "registered"))
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice).where(
                AuthorizedDevice.entitlement_id == entitlement_id,
                AuthorizedDevice.revoked_at.is_(None))), 2 if registration_result == "limited" else 3)
            self.assertIsNotNone(session.get(Entitlement, entitlement_id).last_device_released_at)
            for device_id, credential in devices:
                if session.scalar(select(AuthorizedDevice.revoked_at).where(
                    AuthorizedDevice.entitlement_id == entitlement_id,
                    AuthorizedDevice.device_id == device_id)) is not None:
                    self.assertIsNone(verify_online_access(session, device_id, credential, now))
        if registration_result == "limited":
            with self.factory.begin() as session:
                register_device(session, claim, uuid4(), now + timedelta(seconds=2))
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizedDevice).where(
                AuthorizedDevice.entitlement_id == entitlement_id,
                AuthorizedDevice.revoked_at.is_(None))), 3)


class IsolatedPostgresFixtureSafetyTests(unittest.TestCase):
    def test_rejects_non_test_url_and_connection_options(self):
        good = f"postgresql+psycopg://{TEST_ROLE}:fake@127.0.0.1:{TEST_PORT}/{TEST_DATABASE}"
        self.assertEqual(_validated_test_url(good).database, TEST_DATABASE)
        for bad in (
            good.replace("127.0.0.1", "render.example.com"),
            good.replace(TEST_DATABASE, "development"),
            good.replace(TEST_ROLE, "postgres"),
            good + "?host=render.example.com",
            good.replace("postgresql+psycopg", "postgresql"),
            good.replace(f":{TEST_PORT}/", "/"),
            good.replace(f":{TEST_PORT}/", ":55433/"),
        ):
            with self.subTest(case=bad.rsplit("@", 1)[-1]), self.assertRaises(ValueError):
                _validated_test_url(bad)

    def test_rejects_non_local_or_wrong_database_identity(self):
        connection = Mock()
        for identity in (
            ("development", TEST_ROLE, "127.0.0.1", "public", TEST_PORT),
            (TEST_DATABASE, "postgres", "127.0.0.1", "public", TEST_PORT),
            (TEST_DATABASE, TEST_ROLE, "10.0.0.1", "public", TEST_PORT),
            (TEST_DATABASE, TEST_ROLE, "127.0.0.1", "other", TEST_PORT),
            (TEST_DATABASE, TEST_ROLE, "127.0.0.1", "public", 55433),
        ):
            connection.execute.return_value.one.return_value = identity
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                _assert_local_test_connection(connection)
        for address in ("127.0.0.1", "127.0.0.1/32", "::1", "::1/128"):
            connection.execute.return_value.one.return_value = (
                TEST_DATABASE, TEST_ROLE, address, "public", TEST_PORT
            )
            with self.subTest(address=address):
                _assert_local_test_connection(connection)
        for address in (None, "", "not-an-address", "127.0.0.1/24",
                        "::1/64", "127.0.0.2/32", "10.0.0.1/32", "2001:db8::1/128"):
            connection.execute.return_value.one.return_value = (
                TEST_DATABASE, TEST_ROLE, address, "public", TEST_PORT
            )
            with self.subTest(address=address), self.assertRaises(ValueError):
                _assert_local_test_connection(connection)


if __name__ == "__main__":
    unittest.main()
