import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.database import get_database_url, get_engine, get_session_factory
from app.main import app


HOST = "ep-synthetic-test.c-14.us-east-1.aws.neon.tech"
URL = f"postgresql://test:dummy%40password@{HOST}/neondb"


class DatabaseSecurityTests(unittest.TestCase):
    def tearDown(self):
        if get_engine.cache_info().currsize:
            get_engine().dispose()
        get_session_factory.cache_clear()
        get_engine.cache_clear()

    def configured_url(self, url=URL, **environment):
        with patch.dict(os.environ, {"DATABASE_URL": url, **environment}, clear=True):
            return make_url(get_database_url())

    def test_neon_default_checks_certificate_and_hostname(self):
        url = self.configured_url()
        self.assertEqual(url.query["sslmode"], "verify-full")
        self.assertEqual(url.query["sslrootcert"], "system")
        self.assertEqual(url.password, "dummy@password")
        self.assertEqual(url.drivername, "postgresql+psycopg")

    def test_neon_require_is_strengthened_without_changing_target(self):
        for mode in ("require", "verify-ca", "verify-full"):
            with self.subTest(mode=mode):
                url = self.configured_url(URL + "?sslmode=" + mode)
                self.assertEqual(url.query["sslmode"], "verify-full")
                self.assertEqual((url.host, url.database), (HOST, "neondb"))

    def test_neon_tls_downgrade_and_duplicate_modes_are_rejected(self):
        for query in ("sslmode=disable", "sslmode=allow", "sslmode=prefer",
                      "sslmode=verify-full&sslmode=disable"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.configured_url(URL + "?" + query)

    def test_libpq_target_override_is_rejected(self):
        for query in ("host=elsewhere.test", "hostaddr=127.0.0.1", "port=1234",
                      "dbname=another", "service=another", "servicefile=/tmp/another"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.configured_url(URL + "?" + query)

    def test_development_lock_rejects_wrong_host_or_database(self):
        for url in (URL.replace(HOST, "other.neon.tech"), URL.replace("/neondb", "/other")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.configured_url(url, ENSOUND_DEV_DATABASE_HOST=HOST)
        self.assertEqual(self.configured_url(ENSOUND_DEV_DATABASE_HOST=HOST).host, HOST)

    def test_explicit_ca_file_is_retained_with_hostname_verification(self):
        url = self.configured_url(URL + "?sslrootcert=%2Ftrusted%2Fca.pem")
        self.assertEqual(url.query["sslrootcert"], "/trusted/ca.pem")
        self.assertEqual(url.query["sslmode"], "verify-full")

    def test_empty_ca_parameter_uses_system_trust_instead_of_disabling_verification(self):
        url = self.configured_url(URL + "?sslrootcert=")
        self.assertEqual(url.query["sslrootcert"], "system")
        self.assertEqual(url.query["sslmode"], "verify-full")

    def test_non_neon_local_postgres_configuration_is_preserved(self):
        url = self.configured_url("postgresql://test:dummy@127.0.0.1/local_test")
        self.assertNotIn("sslmode", url.query)

    def test_bad_configuration_is_redacted_at_health_endpoint(self):
        with patch.dict(os.environ, {"DATABASE_URL": URL + "?sslmode=disable"}, clear=True):
            with TestClient(app) as client:
                response = client.get("/health/db")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"detail": "Database unavailable"})
                self.assertNotIn("dummy", response.text)
                self.assertEqual(client.get("/health").status_code, 200)

    def test_invalid_urls_do_not_expose_input_in_errors(self):
        for url in ("not-a-url-secret", "sqlite:///secret", "postgresql+psycopg2://test:secret@host/db"):
            with self.subTest(url=url), self.assertRaises(ValueError) as caught:
                self.configured_url(url)
            self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
