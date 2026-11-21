"""Isolated Claim acceptance tests. Gmail delivery is never executed."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api import access
from app.database import Base
from app.dev_claim_e2e import DEV_HOST, FRONTEND, PAID_TRADE, SERVICE_ID, TEST_EMAIL
from app.dev_claim_e2e import RUN_ENV, active_claim_run, claim_transaction, ClaimRun
from app.models import AuthorizedDevice, EmailClaimRateLimit, EmailClaimToken, Entitlement, Order
from app.services.pricing import PRODUCT_CODE
from scripts import arm_dev_claim_e2e
from test_entitlement_postgres import _assert_local_test_connection, _validated_test_url


class DevClaimTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.key = "A" * 43
        self.run = ClaimRun(hashlib.sha256(self.key.encode()).hexdigest(),
                            self.now - timedelta(minutes=1), self.now + timedelta(minutes=10))

    def test_window_target_and_cleanup(self):
        env = {"RENDER_SERVICE_ID": SERVICE_ID, "EMAIL_TRANSPORT": "gmail_api",
               "RENDER_GIT_REPO_SLUG": "arianien2026/EnSound-UP-dev", "RENDER_GIT_BRANCH": "main",
               "GMAIL_FROM_EMAIL": TEST_EMAIL, "ENSOUND_DEV_FRONTEND_URL": FRONTEND,
               "ENSOUND_DEV_DATABASE_HOST": DEV_HOST, "ENSOUND_DEV_DATABASE_NAME": "neondb"}
        url = f"postgresql+psycopg://synthetic@{DEV_HOST}/neondb?sslmode=verify-full"
        with patch.dict(os.environ, env, clear=True), \
             patch("app.dev_claim_e2e.get_database_url", return_value=url) as db:
            self.assertIsNone(active_claim_run(self.now))
            db.assert_not_called()
            manifest = json.dumps({
                "operator_sha256": self.run.key_digest,
                "starts_at": self.run.starts_at.isoformat(),
                "expires_at": self.run.expires_at.isoformat(),
            })
            with patch.dict(os.environ, {RUN_ENV: manifest}):
                self.assertEqual(active_claim_run(self.now), self.run)
                for invalid_time in (self.run.expires_at, self.run.starts_at - timedelta(seconds=1)):
                    with self.assertRaises(HTTPException):
                        active_claim_run(invalid_time)
                with patch.dict(os.environ, {"RENDER_SERVICE_ID": "other-service"}):
                    with self.assertRaises(HTTPException):
                        active_claim_run(self.now)
                with patch.dict(os.environ, {"ENSOUND_DEV_E2E_ENABLED": "1"}):
                    with self.assertRaises(HTTPException):
                        active_claim_run(self.now)
                db.return_value = url + "&options=override"
                with self.assertRaises(HTTPException):
                    active_claim_run(self.now)
            for bad in ('', '{', '{}', json.dumps({"operator_sha256": self.run.key_digest,
                     "starts_at": self.run.starts_at.isoformat(), "expires_at": self.run.expires_at.isoformat(),
                     "operator_key": "must never be installed"})):
                with patch.dict(os.environ, {RUN_ENV: bad}):
                    with self.assertRaises(HTTPException):
                        active_claim_run(self.now)

    def test_transaction_default_and_expiry(self):
        factory = MagicMock()
        session = factory.begin.return_value.__enter__.return_value
        with claim_transaction(factory, None):
            session.execute.assert_not_called()
        session.scalar.return_value = "on"
        with claim_transaction(factory, self.run):
            self.assertEqual(str(session.execute.call_args.args[0]), "SET TRANSACTION READ WRITE")
            self.assertEqual(str(session.scalar.call_args.args[0]), "SHOW default_transaction_read_only")
        session.scalar.return_value = "off"
        with self.assertRaises(RuntimeError), claim_transaction(factory, self.run):
            self.fail("Unexpected write body")
        expired = ClaimRun(self.run.key_digest, self.run.starts_at, self.now - timedelta(seconds=1))
        session.execute.reset_mock()
        with self.assertRaises(HTTPException), claim_transaction(factory, expired):
            self.fail("Unexpected write body")
        session.execute.assert_not_called()

    def test_setup_stores_only_digest_in_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "operator"
            fd = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with patch.object(arm_dev_claim_e2e.tempfile, "gettempdir", return_value=directory), \
                 patch.object(arm_dev_claim_e2e.tempfile, "mkstemp", return_value=(fd, str(key))), \
                 patch("builtins.print") as output:
                arm_dev_claim_e2e.main()
            manifest = Path(str(key) + ".json")
            secret = key.read_bytes()
            content = json.loads(manifest.read_text())
            self.assertEqual(content["operator_sha256"], hashlib.sha256(secret).hexdigest())
            self.assertNotIn(secret.decode(), manifest.read_text())
            self.assertNotIn(secret.decode(), str(output.call_args_list))
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
            self.assertIsNone(os.environ.get(RUN_ENV))

    def test_runtime_reads_deployment_environment_in_fresh_process(self):
        env = {"RENDER_SERVICE_ID": SERVICE_ID, "EMAIL_TRANSPORT": "gmail_api",
               "RENDER_GIT_REPO_SLUG": "arianien2026/EnSound-UP-dev", "RENDER_GIT_BRANCH": "main",
               "GMAIL_FROM_EMAIL": TEST_EMAIL, "ENSOUND_DEV_FRONTEND_URL": FRONTEND,
               "ENSOUND_DEV_DATABASE_HOST": DEV_HOST, "ENSOUND_DEV_DATABASE_NAME": "neondb",
               "DATABASE_URL": f"postgresql+psycopg://synthetic@{DEV_HOST}/neondb?sslmode=verify-full",
               RUN_ENV: json.dumps({"operator_sha256": self.run.key_digest,
                                    "starts_at": self.run.starts_at.isoformat(),
                                    "expires_at": self.run.expires_at.isoformat()})}
        result = subprocess.run([sys.executable, "-c", "from datetime import datetime,timezone; "
                                 "from app.dev_claim_e2e import active_claim_run; "
                                 "print('active' if active_claim_run(datetime.now(timezone.utc)) else 'inactive')"],
                                env={**os.environ, **env}, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "active")
        self.assertEqual(result.stderr, "")

    def test_background_delivery_refuses_expired_run(self):
        with patch("app.api.access.active_claim_run", return_value=None), \
             patch("app.api.access._deliver_claim") as deliver:
            access._deliver_claim_in_window(TEST_EMAIL, "synthetic-token", object(), self.run)
            deliver.assert_not_called()
            access._deliver_claim_in_window(TEST_EMAIL, "synthetic-token", object(), None)
            deliver.assert_called_once()

    def test_claim_once_and_preserve_pinned_paid_order(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)
        target, other = uuid4(), uuid4()
        entitlement_id = uuid4()
        with factory.begin() as db:
            for order_id, trade in ((target, PAID_TRADE), (other, "ESdifferent")):
                db.add(Order(
                    id=order_id, product_code=PRODUCT_CODE, entitlement_email=TEST_EMAIL,
                    amount=199, currency="TWD", pricing_code="regular", status="paid",
                    created_at=self.now, expires_at=self.now + timedelta(hours=1),
                    email_verified_at=self.now, paid_at=self.now, provider_order_id=trade,
                    provider_trade_no=order_id.hex[:20], payment_environment="ecpay_stage",
                    provider_paid_at=self.now,
                ))
                db.add(Entitlement(
                    id=entitlement_id if order_id == target else uuid4(), source_order_id=order_id,
                    product_code=PRODUCT_CODE, entitlement_email=TEST_EMAIL, status="active",
                    created_at=self.now if order_id == target else self.now + timedelta(seconds=1),
                    activated_at=self.now,
                ))

        def request(key=""):
            return Request({"type": "http", "client": ("192.0.2.14", 1234),
                            "headers": [(b"x-ensound-e2e-key", key.encode())]})

        @contextmanager
        def sqlite_transaction(session_factory, _run):
            # Real service/ORM logic runs here. PostgreSQL mode changes are
            # covered separately, since SQLite doesn't implement SET TRANSACTION.
            with session_factory.begin() as session:
                yield session

        with patch("app.api.access.get_session_factory", return_value=factory), \
             patch("app.api.access.active_claim_run", return_value=self.run), \
             patch("app.api.access.get_rate_limit_key", return_value=b"synthetic-rate-limit-key-32-bytes"), \
             patch("app.api.access.get_email_config"), \
             patch("app.api.access.claim_transaction", side_effect=sqlite_transaction):
            for key, email in (("", TEST_EMAIL), (self.key, "outside@example.com")):
                with self.assertRaises(HTTPException) as denied:
                    access.request_claim_link(access.ClaimLinkRequest(email=email), request(key), BackgroundTasks())
                self.assertEqual(denied.exception.status_code, 403)
            tasks = BackgroundTasks()
            access.request_claim_link(access.ClaimLinkRequest(email=TEST_EMAIL), request(self.key), tasks)
            self.assertEqual(len(tasks.tasks), 1)
            token = tasks.tasks[0].args[1]
            with self.assertRaises(HTTPException) as repeated:
                access.request_claim_link(access.ClaimLinkRequest(email=TEST_EMAIL),
                                          request(self.key), BackgroundTasks())
            self.assertEqual(repeated.exception.status_code, 429)
            with factory() as db:
                self.assertEqual(db.scalars(select(EmailClaimToken.entitlement_id)).all(), [entitlement_id])
            with self.assertRaises(HTTPException) as invalid:
                access.redeem_email_link(access.RedeemClaimRequest(token="Z" * 43), request())
            self.assertEqual(invalid.exception.status_code, 400)
            redeemed = access.redeem_email_link(access.RedeemClaimRequest(token=token), request())
            proof = json.loads(redeemed.body)["claim_session"]
            with self.assertRaises(HTTPException) as replay:
                access.redeem_email_link(access.RedeemClaimRequest(token=token), request())
            self.assertEqual(replay.exception.status_code, 400)
            device_id = uuid4()
            registered = access.register_claimed_device(access.RegisterDeviceRequest(
                claim_session=proof, device_id=device_id))
            credential = json.loads(registered.body)["device_credential"]
            status = access.online_entitlement_status("Bearer " + credential, str(device_id))
            self.assertEqual(json.loads(status.body)["status"], "active")
            with self.assertRaises(HTTPException) as replay_device:
                access.register_claimed_device(access.RegisterDeviceRequest(
                    claim_session=proof, device_id=uuid4()))
            self.assertEqual(replay_device.exception.status_code, 401)
        with factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Order)), 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(Entitlement)), 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(AuthorizedDevice)), 1)


class PostgresClaimGateTests(unittest.TestCase):
    """Actual PostgreSQL transaction/row-lock check; never uses a remote database."""

    @classmethod
    def setUpClass(cls):
        raw = os.environ.get("ENSOUND_3A_ISOLATED_POSTGRES_URL")
        if not raw:
            raise unittest.SkipTest("No isolated local PostgreSQL URL configured")
        cls.url = _validated_test_url(raw)
        cls.writer = create_engine(cls.url, pool_pre_ping=True)
        with cls.writer.begin() as connection:
            _assert_local_test_connection(connection)
            if connection.scalar(text("SELECT count(*) FROM pg_catalog.pg_tables "
                                      "WHERE schemaname = 'public'")):
                raise ValueError("Claim test requires an empty isolated PostgreSQL database")
            Base.metadata.create_all(connection)
            now = datetime.now(timezone.utc)
            cls.order_id, cls.entitlement_id = uuid4(), uuid4()
            connection.execute(Order.__table__.insert().values(
                id=cls.order_id, product_code=PRODUCT_CODE, entitlement_email=TEST_EMAIL,
                amount=199, currency="TWD", pricing_code="regular", status="paid",
                created_at=now, expires_at=now + timedelta(hours=1), email_verified_at=now,
                paid_at=now, provider_order_id=PAID_TRADE, provider_trade_no=cls.order_id.hex[:20],
                payment_environment="ecpay_stage", provider_paid_at=now,
            ))
            connection.execute(Entitlement.__table__.insert().values(
                id=cls.entitlement_id, source_order_id=cls.order_id, product_code=PRODUCT_CODE,
                entitlement_email=TEST_EMAIL, status="active", created_at=now, activated_at=now,
            ))
        cls.reader = create_engine(cls.url, pool_size=8, max_overflow=0, pool_timeout=10)

        @event.listens_for(cls.reader, "connect")
        def readonly_default(dbapi_connection, _record):
            # Equivalent to Render's verified per-connection read-only default.
            with dbapi_connection.cursor() as cursor:
                cursor.execute("SET default_transaction_read_only = on")
            dbapi_connection.commit()

        cls.factory = sessionmaker(bind=cls.reader)

    @classmethod
    def tearDownClass(cls):
        cls.reader.dispose()
        try:
            with cls.writer.begin() as connection:
                _assert_local_test_connection(connection)
                Base.metadata.drop_all(connection)
        finally:
            cls.writer.dispose()

    def test_read_only_commit_rollback_pool_and_concurrent_one_claim(self):
        now = datetime.now(timezone.utc)
        run = ClaimRun(hashlib.sha256(b"A" * 43).hexdigest(),
                       now - timedelta(minutes=1), now + timedelta(minutes=10))
        rate_key = b"synthetic-rate-limit-key-32-bytes"
        with self.factory() as session:
            self.assertEqual(session.scalar(text("SHOW default_transaction_read_only")), "on")
            self.assertEqual(session.scalar(text("SHOW transaction_read_only")), "on")
            with self.assertRaises(DBAPIError):
                session.add(EmailClaimRateLimit(key_hash="0" * 64, window_started_at=now,
                                                last_requested_at=now, request_count=1))
                session.flush()
            session.rollback()

        with self.assertRaisesRegex(RuntimeError, "synthetic rollback"):
            with claim_transaction(self.factory, run) as session:
                self.assertEqual(session.scalar(text("SHOW transaction_read_only")), "off")
                session.add(EmailClaimRateLimit(key_hash="1" * 64, window_started_at=now,
                                                last_requested_at=now, request_count=1))
                session.flush()
                raise RuntimeError("synthetic rollback")
        with self.factory() as session:
            self.assertIsNone(session.get(EmailClaimRateLimit, "1" * 64))
            self.assertEqual(session.scalar(text("SHOW transaction_read_only")), "on")

        start = Event()

        def request_claim():
            request = Request({"type": "http", "client": ("192.0.2.14", 1234),
                               "headers": [(b"x-ensound-e2e-key", b"A" * 43)]})
            tasks = BackgroundTasks()
            start.wait()
            try:
                access.request_claim_link(access.ClaimLinkRequest(email=TEST_EMAIL), request, tasks)
                return 202, len(tasks.tasks)
            except HTTPException as error:
                return error.status_code, len(tasks.tasks)

        with patch("app.api.access.get_session_factory", return_value=self.factory), \
             patch("app.api.access.active_claim_run", return_value=run), \
             patch("app.api.access.get_rate_limit_key", return_value=rate_key), \
             patch("app.api.access.get_email_config"):
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(request_claim) for _ in range(8)]
                start.set()
                results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results.count((202, 1)), 1, results)
        self.assertEqual(results.count((429, 0)), 7, results)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(EmailClaimToken)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(EmailClaimRateLimit)
                                            .where(EmailClaimRateLimit.request_count == 1)), 3)
            self.assertEqual(session.scalar(text("SHOW transaction_read_only")), "on")
            self.assertEqual(session.get(Entitlement, self.entitlement_id).status, "active")
            self.assertEqual(session.get(Order, self.order_id).status, "paid")

        expired = ClaimRun(run.key_digest, run.starts_at, now - timedelta(seconds=1))
        with self.assertRaises(HTTPException), claim_transaction(self.factory, expired):
            self.fail("Expired run must never enter the write transaction")
