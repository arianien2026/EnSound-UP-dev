"""Operator-only reconciliation: reads local evidence; never contacts ECPay."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_database_url, get_session_factory
from app.services.ecpay_stage import get_payment_config
from app.services.refund_reconcile import RefundEvidence, record_refund, refund_followups


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--actor")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--follow-ups", action="store_true")
    args = parser.parse_args()
    try:
        url = make_url(get_database_url())
        local_fixture = (url.host == "127.0.0.1" and url.port == 55432
                         and url.database == "ensound_3a_isolated_test"
                         and url.username == "ensound_isolated_test" and not url.query)
        if (url.host != args.expected_host or url.database != args.expected_database
                or (not local_fixture and url.query.get("sslmode") != "verify-full")
                or any(k in url.query for k in ("host", "hostaddr", "port", "dbname", "service", "servicefile", "options"))):
            raise ValueError("Target mismatch")
        config = get_payment_config()
        now = datetime.now(timezone.utc)
        if args.follow_ups:
            if args.apply or args.evidence or args.actor:
                raise ValueError("Follow up is read only")
            ids = refund_followups(get_session_factory(), config.environment, now)
            print(json.dumps({"manual_follow_up_order_ids": [str(i) for i in ids]}))
        else:
            if not args.evidence or not args.actor:
                raise ValueError("Operator and evidence are required")
            if args.evidence.stat().st_size > 16384:
                raise ValueError("Evidence file too large")
            evidence = RefundEvidence.model_validate_json(args.evidence.read_bytes())
            print(json.dumps({"result": record_refund(
                get_session_factory(), config, evidence, args.actor, now, apply=args.apply,
            )}))
    except (ValueError, OSError, RuntimeError, SQLAlchemyError):
        print(json.dumps({"result": "denied_or_unavailable"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
