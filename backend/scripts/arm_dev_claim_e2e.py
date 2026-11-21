"""Prepare one short-lived Development Claim run locally without printing its key."""
import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.dev_claim_e2e import MAX_WINDOW, RUN_ENV


def main() -> None:
    operator_key = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    metadata = {
        "operator_sha256": hashlib.sha256(operator_key.encode("ascii")).hexdigest(),
        "starts_at": now.isoformat(),
        "expires_at": (now + MAX_WINDOW).isoformat(),
    }
    key_directory = Path(tempfile.gettempdir()).resolve()
    checkout = Path(__file__).resolve().parents[2]
    if key_directory.is_relative_to(checkout):
        raise RuntimeError("The operator key directory must be outside the checkout")
    descriptor, key_path = tempfile.mkstemp(prefix="ensound-dev-claim-key-", dir=key_directory)
    config_path = key_directory / (Path(key_path).name + ".json")
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as key_file:
            key_file.write(operator_key)
        with config_path.open("x", encoding="utf-8") as run_file:
            json.dump(metadata, run_file, indent=2)
            run_file.write("\n")
        os.chmod(config_path, 0o600)
    except OSError:
        os.unlink(key_path)
        raise
    print(f"Local operator key file: {key_path}")
    print(f"Render environment variable: {RUN_ENV}")
    print(f"Local metadata file (digest only): {config_path}")
    print(f"Run window ends: {metadata['expires_at']}")


if __name__ == "__main__":
    main()
