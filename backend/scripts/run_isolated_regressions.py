"""Run existing synthetic regressions without PostgreSQL or network access.

From backend/: python -m scripts.run_isolated_regressions
The opt-in PostgreSQL suite stays disabled; SQLite fixtures use temporary files.
"""
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

import psycopg


def blocked(*args, **kwargs):
    raise AssertionError("External connection disabled in isolated regressions")


def main() -> int:
    for key in tuple(os.environ):
        if (key.startswith(("ECPAY_", "NEWEBPAY_", "RESEND_", "ENSOUND_", "PG"))
                or key in ("DATABASE_URL", "CLAIM_RATE_LIMIT_KEY", "EMAIL_VERIFY_BASE_URL")):
            os.environ.pop(key, None)
    directory = Path(__file__).resolve().parents[1] / "tests"
    with (patch.object(socket.socket, "connect", blocked),
          patch.object(socket.socket, "connect_ex", blocked),
          patch.object(socket, "create_connection", blocked),
          patch.object(psycopg, "connect", blocked)):
        suite = unittest.defaultTestLoader.discover(str(directory))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
