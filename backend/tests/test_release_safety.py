"""Release guards: no real database/network connection or remote mutation."""
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from psycopg.pq import PGconn

from scripts import check_neon_readonly


HOST = "ep-synthetic-release.c-14.us-east-1.aws.neon.tech"


class ReleaseSafetyTests(unittest.TestCase):
    def driver(self, host=HOST, database="neondb", ssl=True, mode="verify-full"):
        return SimpleNamespace(
            info=SimpleNamespace(host=host, dbname=database,
                                 get_parameters=lambda: {"sslmode": mode}),
            pgconn=SimpleNamespace(ssl_in_use=ssl),
        )

    def test_tls_probe_uses_supported_psycopg_api(self):
        self.assertTrue(hasattr(PGconn, "ssl_in_use"))
        # No ConnectionInfo.ssl_in_use or PGconn.ssl_attribute exists here.
        check_neon_readonly.verify_driver_security(self.driver(), HOST, "neondb")

    def test_tls_probe_rejects_unencrypted_unverified_or_wrong_target(self):
        for kwargs in ({"ssl": False}, {"mode": "require"}, {"host": "wrong.neon.tech"},
                       {"database": "wrong"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RuntimeError):
                check_neon_readonly.verify_driver_security(self.driver(**kwargs), HOST, "neondb")

    def config(self):
        directory = Path(__file__).resolve().parents[1]
        config = Config(str(directory / "alembic.ini"))
        config.set_main_option("script_location", str(directory / "migrations"))
        return config

    def test_online_migrations_block_remote_targets_before_engine_creation(self):
        targets = (
            f"postgresql+psycopg://test:dummy@{HOST}/neondb",
            "postgresql+psycopg://test:dummy@old-development.render.test/old_dev",
            "postgresql+psycopg://ensound_isolated_test:dummy@127.0.0.1:55432/wrong",
            "postgresql+psycopg://wrong:dummy@127.0.0.1:55432/ensound_3a_isolated_test",
            "postgresql+psycopg://ensound_isolated_test:dummy@127.0.0.1:5432/ensound_3a_isolated_test",
            "postgresql+psycopg://ensound_isolated_test:dummy@127.0.0.1:55432/ensound_3a_isolated_test?host=remote",
        )
        for url in targets:
            with self.subTest(target=url.split("@")[-1]), \
                 patch("app.database.get_database_url", return_value=url), \
                 patch("sqlalchemy.create_engine") as create:
                with self.assertRaisesRegex(RuntimeError, "Remote migrations are disabled"):
                    command.upgrade(self.config(), "head")
                create.assert_not_called()

    def test_existing_local_postgres_fixture_is_still_permitted(self):
        url = "postgresql+psycopg://ensound_isolated_test:dummy@127.0.0.1:55432/ensound_3a_isolated_test"
        with patch("app.database.get_database_url", return_value=url), \
             patch("sqlalchemy.create_engine", side_effect=RuntimeError("local fixture reached")) as create:
            with self.assertRaisesRegex(RuntimeError, "local fixture reached"):
                command.upgrade(self.config(), "head")
            create.assert_called_once()

    def test_probe_cli_preserves_empty_default_and_explicit_startup_controls(self):
        for extra, empty, readonly in (([], True, False),
                (["--allow-existing-business-rows", "--require-read-only-default"], False, True)):
            with self.subTest(extra=extra), \
                 patch("sys.argv", ["probe", "--expected-host", HOST, *extra]), \
                 patch.object(check_neon_readonly, "probe", return_value={"status": "PASS"}) as probe, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check_neon_readonly.main(), 0)
                probe.assert_called_once_with(HOST, "neondb", require_empty=empty,
                                              require_read_only_default=readonly)
