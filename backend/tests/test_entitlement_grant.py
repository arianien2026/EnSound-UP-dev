import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Entitlement, Order
from app.services.entitlement_grant import (
    EntitlementGrantUnavailable,
    IneligibleEntitlementOrder,
    grant_paid_order,
)
from app.services.entitlement_reconcile import (
    ReconciliationResult, _issue_recovery_permit, recovery_candidates, reconcile_order_ids,
)
from app.services.pricing import PRODUCT_CODE


class EntitlementGrantTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Path(directory.name) / "entitlements.sqlite"
        self.engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 10})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        now = datetime.now(timezone.utc)
        self.order_id = uuid4()
        with self.factory.begin() as session:
            session.add(Order(
                id=self.order_id, entitlement_email="learner@example.com",
                product_code=PRODUCT_CODE, currency="TWD", amount=199,
                pricing_code="regular", status="paid", created_at=now,
                expires_at=now + timedelta(hours=2), paid_at=now,
                email_verified_at=now, provider_order_id="ES" + self.order_id.hex[:18],
                provider_trade_no="1234567890123456", payment_environment="ecpay_stage",
                provider_payment_type="Credit_CreditCard", provider_paid_at=now,
            ))

    def reconcile_test_orders(self, *, batch_size=100):
        """Exercise the bounded recovery primitives against this test's isolated SQLite."""
        candidates = recovery_candidates(self.factory, limit=batch_size)
        if not candidates:
            return ReconciliationResult(0, 0, 0, 0)
        return reconcile_order_ids(candidates, self.factory,
                                   permit=_issue_recovery_permit(self.factory, candidates))

    def entitlement_rows(self):
        with self.factory() as session:
            return session.execute(select(Entitlement)).scalars().all()

    def test_eligible_order_grants_once_and_repeat_returns_same_identity(self):
        first = grant_paid_order(self.order_id, self.factory)
        second = grant_paid_order(self.order_id, self.factory)
        self.assertEqual(first, second)
        self.assertEqual(first.status, "active")
        rows = self.entitlement_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source_order_id, self.order_id)
        self.assertEqual(rows[0].entitlement_email, "learner@example.com")
        self.assertEqual(rows[0].product_code, PRODUCT_CODE)
        self.assertIsNotNone(rows[0].activated_at)

    def test_internal_recovery_finds_missing_pending_and_failed_entitlements(self):
        first = self.reconcile_test_orders(batch_size=1)
        self.assertEqual((first.attempted, first.activated, first.failed), (1, 1, 0))
        self.assertEqual(self.reconcile_test_orders().attempted, 0)
        with self.factory.begin() as session:
            entitlement = session.execute(select(Entitlement)).scalar_one()
            entitlement.status = "failed"
            entitlement.activated_at = None
            entitlement.grant_failed_at = datetime.now(timezone.utc)
        recovered = self.reconcile_test_orders()
        self.assertEqual((recovered.attempted, recovered.activated), (1, 1))
        self.assertEqual(len(self.entitlement_rows()), 1)
        self.assertEqual(self.entitlement_rows()[0].status, "active")

    def test_internal_recovery_promotes_prepared_pending_entitlement(self):
        with self.factory.begin() as session:
            session.add(Entitlement(
                source_order_id=self.order_id, product_code=PRODUCT_CODE,
                entitlement_email="learner@example.com", status="pending",
                updated_at=datetime.now(timezone.utc),
            ))
        result = self.reconcile_test_orders()
        self.assertEqual((result.attempted, result.activated), (1, 1))
        self.assertEqual(self.entitlement_rows()[0].status, "active")

    def test_internal_recovery_skips_refunded_and_revoked_orders(self):
        with self.factory.begin() as session:
            session.get(Order, self.order_id).status = "refunded"
        self.assertEqual(self.reconcile_test_orders().attempted, 0)
        self.assertEqual(self.entitlement_rows(), [])
        with self.factory.begin() as session:
            session.get(Order, self.order_id).status = "paid"
        grant_paid_order(self.order_id, self.factory)
        with self.factory.begin() as session:
            entitlement = session.execute(select(Entitlement)).scalar_one()
            entitlement.status = "revoked"
            entitlement.revoked_at = datetime.now(timezone.utc)
            entitlement.revocation_reason = "confirmed_refund"
        self.assertEqual(self.reconcile_test_orders().attempted, 0)
        self.assertEqual(self.entitlement_rows()[0].status, "revoked")

    def test_internal_recovery_continues_after_one_grant_failure(self):
        second_id = uuid4()
        now = datetime.now(timezone.utc)
        with self.factory.begin() as session:
            session.add(Order(
                id=second_id, entitlement_email="second@example.com", product_code=PRODUCT_CODE,
                currency="TWD", amount=199, pricing_code="regular", status="paid",
                created_at=now, expires_at=now + timedelta(hours=2), paid_at=now,
                email_verified_at=now, provider_order_id="ES" + second_id.hex[:18],
                provider_trade_no=second_id.hex[:20], payment_environment="ecpay_stage",
                provider_payment_type="Credit_CreditCard", provider_paid_at=now,
            ))
        actual_grant = grant_paid_order

        def grant_with_failure(order_id, factory):
            if order_id == self.order_id:
                raise EntitlementGrantUnavailable()
            return actual_grant(order_id, factory)

        with patch("app.services.entitlement_reconcile.grant_paid_order", side_effect=grant_with_failure):
            outcome = self.reconcile_test_orders(batch_size=2)
        self.assertEqual((outcome.attempted, outcome.activated, outcome.failed), (2, 1, 1))
        self.assertEqual(self.reconcile_test_orders().activated, 1)
        self.assertEqual(len(self.entitlement_rows()), 2)

    def test_ineligible_order_never_creates_entitlement(self):
        cases = (
            {"status": "pending"}, {"status": "refunded"},
            {"email_verified_at": None},
            {"provider_trade_no": None}, {"payment_environment": None},
            {"paid_at": None},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.factory.begin() as session:
                    order = session.get(Order, self.order_id)
                    originals = {key: getattr(order, key) for key in changes}
                    for key, value in changes.items():
                        setattr(order, key, value)
                with self.assertRaises(IneligibleEntitlementOrder):
                    grant_paid_order(self.order_id, self.factory)
                self.assertEqual(len(self.entitlement_rows()), 0)
                with self.factory.begin() as session:
                    order = session.get(Order, self.order_id)
                    for key, value in originals.items():
                        setattr(order, key, value)

    def test_refunded_order_cannot_be_regranted(self):
        grant_paid_order(self.order_id, self.factory)
        with self.factory.begin() as session:
            session.get(Order, self.order_id).status = "refunded"
        with self.assertRaises(IneligibleEntitlementOrder):
            grant_paid_order(self.order_id, self.factory)
        self.assertEqual(len(self.entitlement_rows()), 1)  # Revocation is a later task.

    def test_refund_request_does_not_cancel_verified_paid_rights(self):
        with self.factory.begin() as session:
            session.get(Order, self.order_id).status = "refund_pending"
        result = grant_paid_order(self.order_id, self.factory)
        self.assertEqual(result.status, "active")
        self.assertEqual(grant_paid_order(self.order_id, self.factory).id, result.id)

    def test_database_rejects_duplicate_source_order(self):
        grant_paid_order(self.order_id, self.factory)
        with self.assertRaises(IntegrityError):
            with self.factory.begin() as session:
                session.add(Entitlement(
                    source_order_id=self.order_id, product_code=PRODUCT_CODE,
                    entitlement_email="learner@example.com", status="pending",
                    updated_at=datetime.now(timezone.utc),
                ))
        self.assertEqual(len(self.entitlement_rows()), 1)

    def test_database_rejects_inconsistent_activation_and_failure_state(self):
        for status in ("active", "failed"):
            with self.subTest(status=status), self.assertRaises(IntegrityError):
                with self.factory.begin() as session:
                    session.add(Entitlement(
                        source_order_id=self.order_id, product_code=PRODUCT_CODE,
                        entitlement_email="learner@example.com", status=status,
                        updated_at=datetime.now(timezone.utc),
                    ))
        self.assertEqual(len(self.entitlement_rows()), 0)

    def test_refund_completed_between_transactions_blocks_activation(self):
        outer = self

        class RefundAfterPreparation:
            def __init__(self):
                self.count = 0

            @contextmanager
            def begin(self):
                with outer.factory.begin() as session:
                    yield session
                self.count += 1
                if self.count == 1:
                    with outer.factory.begin() as session:
                        session.get(Order, outer.order_id).status = "refunded"

        with self.assertRaises(IneligibleEntitlementOrder):
            grant_paid_order(self.order_id, RefundAfterPreparation())
        rows = self.entitlement_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "pending")
        self.assertIsNone(rows[0].activated_at)

    def test_activation_commit_failure_rolls_back_and_is_recoverable(self):
        count = 0

        def fail_second_commit(_session: Session):
            nonlocal count
            count += 1
            if count == 2:
                raise OperationalError("isolated test commit failure", {}, Exception("failed"))

        event.listen(Session, "before_commit", fail_second_commit)
        try:
            with self.assertRaises(EntitlementGrantUnavailable):
                grant_paid_order(self.order_id, self.factory)
        finally:
            event.remove(Session, "before_commit", fail_second_commit)
        rows = self.entitlement_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "failed")
        self.assertIsNone(rows[0].activated_at)
        self.assertEqual(grant_paid_order(self.order_id, self.factory).id, rows[0].id)

    def test_concurrent_attempts_never_create_duplicate_entitlements(self):
        start = Event()

        def attempt():
            start.wait()
            try:
                return grant_paid_order(self.order_id, self.factory)
            except EntitlementGrantUnavailable:
                # SQLite has no SELECT FOR UPDATE; PostgreSQL row locks serialize
                # attempts. A transient local lock failure can be retried safely.
                return grant_paid_order(self.order_id, self.factory)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            start.set()
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0].id, results[1].id)
        self.assertEqual(len(self.entitlement_rows()), 1)

    def test_activation_failure_is_auditable_and_does_not_claim_success(self):
        fail_once = True

        def fail_activation(_connection, _cursor, statement, _parameters, _context, _executemany):
            nonlocal fail_once
            if fail_once and statement.startswith("UPDATE entitlements"):
                fail_once = False
                raise OperationalError("activation failed", {}, Exception("isolated test failure"))

        event.listen(self.engine, "before_cursor_execute", fail_activation)
        try:
            with self.assertRaises(EntitlementGrantUnavailable):
                grant_paid_order(self.order_id, self.factory)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_activation)
        rows = self.entitlement_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "failed")
        self.assertIsNotNone(rows[0].grant_failed_at)
        self.assertIsNone(rows[0].activated_at)
        result = grant_paid_order(self.order_id, self.factory)
        self.assertEqual(result.id, rows[0].id)
        self.assertEqual(result.status, "active")
        self.assertEqual(len(self.entitlement_rows()), 1)

    def test_database_unavailable_yields_no_false_success(self):
        self.engine.dispose()
        Path(self.engine.url.database).unlink()
        with self.assertRaises(EntitlementGrantUnavailable):
            grant_paid_order(self.order_id, self.factory)


if __name__ == "__main__":
    unittest.main()
