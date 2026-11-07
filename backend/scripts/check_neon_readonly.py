"""Read-only application/Neon probe. Never runs migrations, sends mail, or writes rows.

Run from backend/: python -m scripts.check_neon_readonly --expected-host HOST
DATABASE_URL must already be supplied privately in the process environment.
"""
import argparse
import json
import os
import ssl
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select, text

from app.database import Base, get_database_url, get_engine, get_session_factory
from sqlalchemy.engine import make_url
import psycopg
from app.models import (
    AuthorizedDevice, EmailClaimRateLimit, EmailClaimSession, EmailClaimToken,
    Entitlement, Order,
)


EXPECTED_REVISION = "3c3b_device_release"
EXPECTED_TABLES = {
    "orders", "entitlements", "email_claim_tokens", "email_claim_sessions",
    "email_claim_rate_limits", "authorized_devices", "alembic_version",
}
ORM_MODELS = (Order, Entitlement, AuthorizedDevice, EmailClaimToken,
              EmailClaimSession, EmailClaimRateLimit)


def tls_failure_diagnostics(error: Exception) -> dict:
    """Allowlisted diagnostics only: never emit exception text, URLs or env values."""
    detail = str(getattr(error, "orig", error)).lower()
    reason = "UNSPECIFIED"
    for code, phrases in (
        ("HOSTNAME_MISMATCH", ("does not match host name", "hostname mismatch")),
        ("CA_FILE_MISSING", ("root certificate file",)),
        ("CERTIFICATE_EXPIRED", ("certificate has expired",)),
        ("CERTIFICATE_NOT_YET_VALID", ("certificate is not yet valid",)),
        ("UNTRUSTED_CHAIN", ("unable to get local issuer certificate",
                             "unable to verify the first certificate",
                             "self-signed certificate", "self signed certificate")),
        ("CERTIFICATE_VALIDATION_UNSPECIFIED", ("certificate verify failed",)),
    ):
        if any(phrase in detail for phrase in phrases):
            reason = code
            break
    # A root certificate error can also mean unreadable/invalid content, not absence.
    if reason == "CA_FILE_MISSING" and "does not exist" not in detail:
        reason = "CA_FILE_ERROR"
    result = {
        "reason": reason, "python_version": sys.version.split()[0],
        "psycopg_version": psycopg.__version__, "libpq_version": psycopg.pq.version(),
        "psycopg_implementation": psycopg.pq.__impl__,
        # This is Python's OpenSSL, not necessarily psycopg-binary's OpenSSL.
        "python_openssl_version": ssl.OPENSSL_VERSION,
        "ssl_cert_file_env_present": bool(os.environ.get("SSL_CERT_FILE")),
        "ssl_cert_dir_env_present": bool(os.environ.get("SSL_CERT_DIR")),
        "known_ca_files_readable": {
            path: Path(path).is_file() and os.access(path, os.R_OK)
            for path in ("/usr/lib/psycopg-openssl/cert.pem",
                         "/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/cert.pem")
        },
    }
    try:
        url = make_url(get_database_url())
        root = url.query.get("sslrootcert")
        result["sslmode"] = ("verify-full" if url.query.get("sslmode") == "verify-full"
                             else "OTHER_OR_UNSET")
        result["ca_source"] = ("system" if root == "system" else
                               "explicit_file" if isinstance(root, str) else "UNSET")
        if isinstance(root, str) and root != "system":
            result["explicit_ca_file_readable"] = Path(root).is_file() and os.access(root, os.R_OK)
    except Exception:
        result["configuration"] = "UNAVAILABLE"
    return result


def local_revision_available() -> bool:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    try:
        return ScriptDirectory.from_config(config).get_revision(EXPECTED_REVISION) is not None
    except Exception:
        return False


def verify_driver_security(driver, expected_host: str, expected_database: str) -> None:
    params = driver.info.get_parameters()
    if (driver.info.host != expected_host or driver.info.dbname != expected_database
            or not driver.pgconn.ssl_in_use or params.get("sslmode") != "verify-full"):
        raise RuntimeError("Secure Development connection verification failed")


def probe(expected_host: str, expected_database: str = "neondb", *,
          require_empty: bool = True, require_read_only_default: bool = False) -> dict:
    # Applies the same lock to every application connection opened by this process.
    os.environ["ENSOUND_DEV_DATABASE_HOST"] = expected_host
    os.environ["ENSOUND_DEV_DATABASE_NAME"] = expected_database
    model_tables = {mapper.local_table.name for mapper in Base.registry.mappers}
    result = {"connection": "NOT RUN", "tls": "NOT RUN", "orm_order": "NOT RUN",
              "alembic_revision_available_locally": local_revision_available(),
              "entitlement_model_present": "entitlements" in model_tables,
              "device_model_present": "authorized_devices" in model_tables}
    engine = None
    try:
        result["connection"] = "FAIL"
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            driver = connection.connection.driver_connection
            verify_driver_security(driver, expected_host, expected_database)
            result["tls"] = "PASS"
            result["tls_client_mode"] = "verify-full"
            row = connection.execute(text(
                "SELECT current_database(),current_setting('server_version_num'),"
                "current_setting('transaction_read_only'),"
                "current_setting('default_transaction_read_only')"
            )).one()
            if row[0] != expected_database or row[1] != "180006" or row[2] != "on":
                raise RuntimeError("Unexpected database/version/transaction mode")
            if require_read_only_default and row[3] != "on":
                raise RuntimeError("Development connection acceptance requires read-only defaults")
            result.update(connection="PASS", database=row[0], server_version_num=row[1])
            result["default_transaction_read_only"] = row[3]
            tables = set(inspect(connection).get_table_names(schema="public"))
            if tables != EXPECTED_TABLES:
                raise RuntimeError("Unexpected table inventory")
            result["tables"] = "PASS"
            revision = connection.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one()
            if revision != EXPECTED_REVISION:
                raise RuntimeError("Unexpected database revision")
            result["database_revision"] = revision
            # Bind the real application's factory to this READ ONLY transaction.
            # Session cleanup rolls back a SAVEPOINT; the outer transaction is rolled back too.
            with get_session_factory()(bind=connection, join_transaction_mode="create_savepoint") as session:
                result["orm_models"] = {}
                for model in ORM_MODELS:
                    session.scalars(select(model).limit(1)).all()
                    result["orm_models"][model.__tablename__] = "PASS"
                result["orm_order"] = "PASS"
            result["business_rows"] = {
                table: connection.execute(text(f'SELECT count(*) FROM public."{table}"')).scalar_one()
                for table in sorted(EXPECTED_TABLES - {"alembic_version"})
            }
            connection.rollback()
            if require_empty and any(result["business_rows"].values()):
                raise RuntimeError("Development business tables are no longer empty")
        result["status"] = ("PASS" if all(result[key] for key in (
            "alembic_revision_available_locally", "entitlement_model_present", "device_model_present"
        )) else "PARTIAL")
    except Exception as error:
        # Do not print exception text: drivers may include connection details.
        detail = str(getattr(error, "orig", error)).lower()
        error_code = "CONNECTION_OR_CONFIGURATION_ERROR"
        if any(value in detail for value in ("resolve", "name resolution", "translate host name")):
            error_code = "DNS_UNAVAILABLE"
        elif "certificate" in detail:
            error_code = "CERTIFICATE_VERIFICATION_FAILED"
        result.update(status="PARTIAL", error_type=type(error).__name__,
                      error_code=error_code,
                      tls_diagnostics=tls_failure_diagnostics(error),
                      error="Application connection/probe failed; no exception details logged")
    finally:
        if engine is not None:
            engine.dispose()
        get_session_factory.cache_clear()
        get_engine.cache_clear()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--expected-database", default="neondb")
    parser.add_argument("--allow-existing-business-rows", action="store_true",
                        help="Count existing rows without requiring zero; never writes data")
    parser.add_argument("--require-read-only-default", action="store_true",
                        help="Refuse startup unless default_transaction_read_only is on")
    parser.add_argument("--controlled-e2e", action="store_true",
                        help="Require a valid expiring operator/mailbox scope instead of read-only startup")
    args = parser.parse_args()
    if args.controlled_e2e:
        from app.dev_e2e import configured_scope
        try:
            if args.require_read_only_default or configured_scope() is None:
                raise ValueError()
        except Exception:
            print(json.dumps({"status": "PARTIAL", "error_code": "E2E_SCOPE_UNAVAILABLE"}))
            return 2
    result = probe(args.expected_host, args.expected_database,
                   require_empty=not args.allow_existing_business_rows,
                   require_read_only_default=args.require_read_only_default)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
