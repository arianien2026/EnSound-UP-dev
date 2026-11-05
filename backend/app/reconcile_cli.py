"""Explicit, Development-only operator entry for entitlement recovery.

Run with python -m app.reconcile_cli. Importing this module never connects to a DB.
"""

import argparse
import os
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.database import get_database_url

from app.database import get_session_factory
from app.services.entitlement_reconcile import (
    _issue_recovery_permit, recovery_candidates, reconcile_order_ids,
)


CONFIRMATION = "RECONCILE-ENSOUND-DEV-ENTITLEMENTS"


def _validate_target(expected_database: str) -> bool:
    """Fail closed before engine/session creation; never log connection strings."""
    allowed_host = os.environ.get("ENSOUND_RECONCILE_ALLOWED_HOST", "").strip()
    allowed_port = os.environ.get("ENSOUND_RECONCILE_ALLOWED_PORT", "").strip()
    allowed_database = os.environ.get("ENSOUND_RECONCILE_ALLOWED_DATABASE", "").strip()
    allowed_schema = os.environ.get("ENSOUND_RECONCILE_ALLOWED_SCHEMA", "").strip()

    # Trusted values must be configured independently of DATABASE_URL.
    if not all((allowed_host, allowed_port, allowed_database, allowed_schema)):
        return False
    if not allowed_port.isascii() or not allowed_port.isdecimal():
        return False
    port = int(allowed_port)
    if not 1 <= port <= 65535 or allowed_schema != "public":
        return False
    if expected_database != allowed_database:
        return False
    try:
        url = make_url(get_database_url())
        # No URL-specified connection options, including search_path or multi-host.
        if url.drivername != "postgresql+psycopg" or url.query:
            return False
        if not url.host or url.port is None or not url.database:
            return False
        if url.host.casefold() != allowed_host.casefold():
            return False
        if url.port != port or url.database != allowed_database:
            return False
    except (ValueError, TypeError, RuntimeError, OverflowError):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review or recover eligible paid Development orders")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--order-id", type=UUID, help="One EnSound order UUID")
    scope.add_argument("--batch-limit", type=int, help="At most this many candidates (1–100)")
    parser.add_argument("--expected-database", required=True, help="Exact Development database name")
    parser.add_argument("--execute", action="store_true", help="Perform grant attempts (default: dry run)")
    parser.add_argument("--confirm", help=f"Required with --execute: {CONFIRMATION}")
    args = parser.parse_args(argv)

    if args.batch_limit is not None and not 1 <= args.batch_limit <= 100:
        parser.error("--batch-limit must be between 1 and 100")
    if args.execute and args.confirm != CONFIRMATION:
        parser.error("--execute requires the exact --confirm phrase")
    if not args.execute and args.confirm is not None:
        parser.error("--confirm is only accepted with --execute")
    if not args.expected_database.strip():
        parser.error("--expected-database must not be blank")
    if os.environ.get("ENSOUND_RECONCILE_TARGET") != "development":
        parser.error("ENSOUND_RECONCILE_TARGET must explicitly equal development")

    if not _validate_target(args.expected_database):
        print("STOP: untrusted database target; no connection attempted")
        return 2

    try:
        factory = get_session_factory()
        with factory() as session:
            actual_database = session.execute(text("SELECT current_database()")).scalar_one()
            actual_schema = session.execute(text("SELECT current_schema()")).scalar_one()
        if actual_database != args.expected_database or actual_schema != "public":
            print("STOP: database identity does not match; no orders changed")
            return 2
        candidates = recovery_candidates(
            factory, order_id=args.order_id,
            limit=1 if args.order_id is not None else args.batch_limit,
        )
    except Exception:
        # Never print the connection URL, SQL exceptions, or payment payloads.
        print("STOP: database unavailable or recovery scan failed; no grants attempted")
        return 2

    print(f"mode={'execute' if args.execute else 'dry-run'} candidates={len(candidates)}")
    if not args.execute:
        for order_id in candidates:
            print(f"candidate={order_id}")
        return 0

    errors = 0
    for order_id in candidates:
        try:
            permit = _issue_recovery_permit(factory, [order_id])
            result = reconcile_order_ids([order_id], factory, permit=permit)
            outcome = "activated" if result.activated else "rejected" if result.rejected else "failed"
            errors += int(outcome != "activated")
        except Exception:
            # Each order remains independently retryable; do not expose DB internals.
            outcome = "failed"
            errors += 1
        print(f"order={order_id} outcome={outcome}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
