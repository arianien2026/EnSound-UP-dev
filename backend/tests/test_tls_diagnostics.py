"""Failure diagnostics must distinguish causes without exposing connection data."""
import json
import os
import unittest
from unittest.mock import patch, MagicMock

from sqlalchemy.exc import OperationalError
from scripts import check_neon_readonly as probe


HOST = "ep-synthetic-diagnostic.c-14.us-east-1.aws.neon.tech"
URL = f"postgresql://synthetic:do-not-log-password@{HOST}/neondb"


class TlsDiagnosticsTests(unittest.TestCase):
    def diagnose(self, message, url=URL):
        with patch.dict(os.environ, {"DATABASE_URL": url}, clear=True):
            error = OperationalError(None, None, Exception(message))
            return probe.tls_failure_diagnostics(error)

    def test_distinguishes_chain_from_hostname(self):
        cases = {
            'server certificate for "wrong.invalid" does not match host name "localhost"': "HOSTNAME_MISMATCH",
            "SSL error: certificate verify failed: unable to get local issuer certificate": "UNTRUSTED_CHAIN",
            "SSL error: certificate verify failed: self-signed certificate": "UNTRUSTED_CHAIN",
            "SSL error: certificate verify failed: certificate has expired": "CERTIFICATE_EXPIRED",
            "SSL error: certificate verify failed: certificate is not yet valid": "CERTIFICATE_NOT_YET_VALID",
            'root certificate file "/synthetic/ca.pem" does not exist': "CA_FILE_MISSING",
            'could not read root certificate file "/synthetic/ca.pem"': "CA_FILE_ERROR",
            "SSL error: certificate verify failed": "CERTIFICATE_VALIDATION_UNSPECIFIED",
            "unknown secret failure": "UNSPECIFIED",
        }
        for message, expected in cases.items():
            with self.subTest(reason=expected):
                self.assertEqual(self.diagnose(message)["reason"], expected)

    def test_system_source_and_full_verification_reported(self):
        result = self.diagnose("SSL error: certificate verify failed")
        self.assertEqual(result["ca_source"], "system")
        self.assertEqual(result["sslmode"], "verify-full")
        self.assertIn("libpq_version", result)
        self.assertIn("python_openssl_version", result)

    def test_no_exception_url_query_or_environment_values_are_emitted(self):
        secret = "do-not-log-password"
        with patch.dict(os.environ, {"DATABASE_URL": URL,
                                   "SSL_CERT_FILE": "/private/secret-ca.pem",
                                   "SSL_CERT_DIR": "/private/secret-directory"}, clear=True):
            result = probe.tls_failure_diagnostics(Exception(
                f"certificate verify failed {URL} {secret} /private/secret-ca.pem"))
        encoded = json.dumps(result)
        for value in (secret, HOST, URL, "/private", "secret-directory"):
            self.assertNotIn(value, encoded)
        self.assertTrue(result["ssl_cert_file_env_present"])
        self.assertTrue(result["ssl_cert_dir_env_present"])

    def test_explicit_ca_path_is_not_emitted_or_replaced(self):
        result = self.diagnose("failure", URL + "?sslrootcert=%2Fprivate%2Fca.pem")
        self.assertEqual(result["ca_source"], "explicit_file")
        self.assertNotIn("/private", json.dumps(result))
        self.assertIn("explicit_ca_file_readable", result)

    def test_bad_config_is_redacted(self):
        result = self.diagnose("unknown", "bad-secret-url")
        self.assertEqual(result["configuration"], "UNAVAILABLE")
        self.assertNotIn("bad-secret-url", json.dumps(result))

    def test_startup_failure_keeps_cleanup_and_no_auth_or_sql(self):
        engine = MagicMock()
        engine.connect.side_effect = OperationalError(None, None, Exception(
            "certificate verify failed: unable to get local issuer certificate " + URL))
        with patch.dict(os.environ, {"DATABASE_URL": URL}, clear=True), \
             patch.object(probe, "get_engine", return_value=engine), \
             patch.object(probe, "get_session_factory") as factory:
            result = probe.probe(HOST, require_read_only_default=True)
            factory.assert_not_called()
            factory.cache_clear.assert_called_once()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["error_code"], "CERTIFICATE_VERIFICATION_FAILED")
        self.assertEqual(result["tls_diagnostics"]["reason"], "UNTRUSTED_CHAIN")
        self.assertEqual(result["orm_order"], "NOT RUN")
        self.assertNotIn("do-not-log-password", json.dumps(result))
        engine.dispose.assert_called_once()


if __name__ == "__main__":
    unittest.main()
