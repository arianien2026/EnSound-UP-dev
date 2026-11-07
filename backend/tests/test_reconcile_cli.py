"""Pure mocked CLI guards; no database connection or external request."""

import io
import os
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.reconcile_cli import CONFIRMATION, main, _validate_target
from app.services.entitlement_reconcile import (
    ReconciliationResult, RecoveryPermit, _issue_recovery_permit, reconcile_order_ids,
)


class ReconciliationCliTests(unittest.TestCase):
    def setUp(self):
        self.order_id = uuid4()
        self.factory = MagicMock()
        self.factory.return_value.__enter__.return_value.execute.return_value.scalar_one.return_value = "ensound_dev"
        self.factory.return_value.__enter__.return_value.execute.return_value.scalar_one.side_effect = ["ensound_dev", "public"]
        target = patch.dict(os.environ, {
            "ENSOUND_RECONCILE_TARGET": "development",
            "ENSOUND_RECONCILE_ALLOWED_HOST": "dev.example.invalid",
            "ENSOUND_RECONCILE_ALLOWED_PORT": "5432",
            "ENSOUND_RECONCILE_ALLOWED_DATABASE": "ensound_dev",
            "ENSOUND_RECONCILE_ALLOWED_SCHEMA": "public",
            "DATABASE_URL": "postgresql+psycopg://example:example@dev.example.invalid:5432/ensound_dev",
        })
        target.start()
        self.addCleanup(target.stop)

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with (patch("app.reconcile_cli.get_session_factory", return_value=self.factory) as factory_call,
              patch("app.reconcile_cli.recovery_candidates", return_value=[self.order_id]) as scan,
              patch("app.reconcile_cli.reconcile_order_ids",
                    return_value=ReconciliationResult(1, 1, 0, 0)) as grant,
              redirect_stdout(out), redirect_stderr(err)):
            try:
                code = main(["--expected-database", "ensound_dev", *args])
            except SystemExit as exc:
                code = exc.code
        self.last_factory_call = factory_call
        return code, out.getvalue(), scan, grant

    def test_dry_run_lists_candidate_without_grant(self):
        code, output, scan, grant = self.invoke("--order-id", str(self.order_id))
        self.assertEqual(code, 0)
        self.assertIn("mode=dry-run", output)
        self.assertIn(str(self.order_id), output)
        scan.assert_called_once()
        grant.assert_not_called()

    def test_execution_requires_exact_confirmation(self):
        code, _, scan, grant = self.invoke("--batch-limit", "2", "--execute")
        self.assertEqual(code, 2)
        scan.assert_not_called()
        grant.assert_not_called()

    def test_explicit_execution_is_bounded(self):
        code, output, scan, grant = self.invoke(
            "--batch-limit", "2", "--execute", "--confirm", CONFIRMATION
        )
        self.assertEqual(code, 0)
        self.assertIn("outcome=activated", output)
        self.assertEqual(scan.call_args.kwargs["limit"], 2)
        grant.assert_called_once()
        args, kwargs = grant.call_args
        self.assertEqual(args, ([self.order_id], self.factory))
        self.assertIn("permit", kwargs)
        self.assertIsNotNone(kwargs["permit"])

    def test_wrong_database_stops_before_scan(self):
        self.factory.return_value.__enter__.return_value.execute.return_value.scalar_one.side_effect = ["other", "public"]
        code, output, scan, grant = self.invoke("--batch-limit", "2", "--execute",
                                                "--confirm", CONFIRMATION)
        self.assertEqual(code, 2)
        self.assertIn("STOP", output)
        scan.assert_not_called()
        grant.assert_not_called()

    def test_missing_development_target_stops_before_connection(self):
        with patch.dict(os.environ, {"ENSOUND_RECONCILE_TARGET": "production"}):
            code, _, scan, grant = self.invoke("--order-id", str(self.order_id))
        self.assertEqual(code, 2)
        self.last_factory_call.assert_not_called()
        scan.assert_not_called()
        grant.assert_not_called()


    def assert_target_rejected(self, changes):
        with patch.dict(os.environ, changes):
            code, output, scan, grant = self.invoke("--order-id", str(self.order_id))
        self.assertEqual(code, 2)
        self.assertIn("STOP", output)
        self.last_factory_call.assert_not_called()
        scan.assert_not_called()
        grant.assert_not_called()

    def test_missing_trusted_target_rejected_before_connection(self):
        self.assert_target_rejected({"ENSOUND_RECONCILE_ALLOWED_HOST": ""})

    def test_wrong_host_rejected_before_connection(self):
        self.assert_target_rejected({"DATABASE_URL": "postgresql+psycopg://u:p@wrong.example.invalid:5432/ensound_dev"})

    def test_wrong_port_rejected_before_connection(self):
        self.assert_target_rejected({"DATABASE_URL": "postgresql+psycopg://u:p@dev.example.invalid:55432/ensound_dev"})

    def test_missing_port_rejected_before_connection(self):
        self.assert_target_rejected({"DATABASE_URL": "postgresql+psycopg://u:p@dev.example.invalid/ensound_dev"})

    def test_wrong_url_database_rejected_before_connection(self):
        self.assert_target_rejected({"DATABASE_URL": "postgresql+psycopg://u:p@dev.example.invalid:5432/other"})

    def test_malformed_url_rejected_before_connection(self):
        self.assert_target_rejected({"DATABASE_URL": "postgresql://bad%%%"})

    def test_unexpected_driver_rejected_before_connection(self):
        self.assert_target_rejected({"DATABASE_URL": "sqlite:///ensound_dev"})

    def test_url_schema_options_rejected_before_connection(self):
        self.assert_target_rejected({"DATABASE_URL": "postgresql+psycopg://u:p@dev.example.invalid:5432/ensound_dev?options=-csearch_path%3Dother"})

    def test_wrong_trusted_schema_rejected_before_connection(self):
        self.assert_target_rejected({"ENSOUND_RECONCILE_ALLOWED_SCHEMA": "other"})

    def test_invalid_trusted_port_rejected_before_connection(self):
        self.assert_target_rejected({"ENSOUND_RECONCILE_ALLOWED_PORT": "invalid"})

    def test_database_name_mismatch_in_allowlist_rejected(self):
        self.assert_target_rejected({"ENSOUND_RECONCILE_ALLOWED_DATABASE": "other"})

    def test_server_schema_mismatch_rejected_before_scan(self):
        self.factory.return_value.__enter__.return_value.execute.return_value.scalar_one.side_effect = ["ensound_dev", "other"]
        code, output, scan, grant = self.invoke("--order-id", str(self.order_id))
        self.assertEqual(code, 2)
        self.assertIn("STOP", output)
        scan.assert_not_called()
        grant.assert_not_called()

    def test_allowed_dry_run_does_not_grant(self):
        code, output, scan, grant = self.invoke("--order-id", str(self.order_id))
        self.assertEqual(code, 0)
        self.assertIn("mode=dry-run", output)
        grant.assert_not_called()


class NeonRecoveryTargetTests(unittest.TestCase):
    def allowed(self, query="", port=""):
        host = "ep-synthetic.c-14.us-east-1.aws.neon.tech"
        with patch.dict(os.environ, {
            "DATABASE_URL": f"postgresql://synthetic:dummy@{host}{port}/neondb{query}",
            "ENSOUND_RECONCILE_ALLOWED_HOST": host,
            "ENSOUND_RECONCILE_ALLOWED_PORT": "5432",
            "ENSOUND_RECONCILE_ALLOWED_DATABASE": "neondb",
            "ENSOUND_RECONCILE_ALLOWED_SCHEMA": "public",
        }, clear=True):
            return _validate_target("neondb")

    def test_normalized_neon_tls_with_default_or_explicit_port_is_permitted(self):
        self.assertTrue(self.allowed())
        self.assertTrue(self.allowed("?sslmode=verify-full&sslrootcert=system", ":5432"))

    def test_arbitrary_options_and_wrong_port_still_rejected(self):
        for query in ("?options=-csearch_path%3Dother", "?host=other.invalid", "?application_name=extra"):
            with self.subTest(query=query):
                self.assertFalse(self.allowed(query))
        self.assertFalse(self.allowed(port=":55432"))


class RecoveryPermitGuardTests(unittest.TestCase):
    def setUp(self):
        self.factory = MagicMock()
        self.order_id = uuid4()

    def test_missing_permit_fails_before_grant_or_session(self):
        with patch("app.services.entitlement_reconcile.grant_paid_order") as grant:
            with self.assertRaises(PermissionError):
                reconcile_order_ids([self.order_id], self.factory)
            self.factory.assert_not_called()
            grant.assert_not_called()

    def test_direct_permit_construction_is_rejected(self):
        with self.assertRaises(PermissionError):
            RecoveryPermit(self.factory, (self.order_id,))

    def test_wrong_factory_and_scope_rejected_before_grant(self):
        permit = _issue_recovery_permit(self.factory, [self.order_id])
        with patch("app.services.entitlement_reconcile.grant_paid_order") as grant:
            with self.assertRaises(PermissionError):
                reconcile_order_ids([self.order_id], MagicMock(), permit=permit)
            with self.assertRaises(PermissionError):
                reconcile_order_ids([uuid4()], self.factory, permit=permit)
            grant.assert_not_called()

    def test_replay_rejected_before_second_grant(self):
        permit = _issue_recovery_permit(self.factory, [self.order_id])
        fake = MagicMock(source_order_id=self.order_id, status="active")
        with patch("app.services.entitlement_reconcile.grant_paid_order", return_value=fake) as grant:
            self.assertEqual(reconcile_order_ids([self.order_id], self.factory, permit=permit).activated, 1)
            with self.assertRaises(PermissionError):
                reconcile_order_ids([self.order_id], self.factory, permit=permit)
            grant.assert_called_once()

    def test_oversized_batch_and_invalid_issuance_rejected(self):
        ids = [uuid4() for _ in range(101)]
        with self.assertRaises(ValueError):
            _issue_recovery_permit(self.factory, ids)
        with self.assertRaises(ValueError):
            reconcile_order_ids(ids, self.factory)
        with self.assertRaises(ValueError):
            _issue_recovery_permit(self.factory, [self.order_id, self.order_id])


class DisabledBatchRecoveryTests(unittest.TestCase):
    def test_legacy_batch_recovery_cannot_create_default_session_or_grant(self):
        from app.services.entitlement_reconcile import reconcile_paid_orders

        with (patch("app.database.get_session_factory") as factory,
              patch("app.services.entitlement_reconcile.grant_paid_order") as grant):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                reconcile_paid_orders()
            factory.assert_not_called()
            grant.assert_not_called()

    def test_legacy_batch_recovery_rejects_supplied_factory(self):
        from app.services.entitlement_reconcile import reconcile_paid_orders

        factory = MagicMock()
        with patch("app.services.entitlement_reconcile.grant_paid_order") as grant:
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                reconcile_paid_orders(factory=factory, batch_size=1)
            factory.assert_not_called()
            grant.assert_not_called()


if __name__ == "__main__":
    unittest.main()
