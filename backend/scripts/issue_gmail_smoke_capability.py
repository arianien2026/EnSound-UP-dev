"""Single-process, single-signature issuer. Private key NEVER leaves RAM.

Working directory must be a fresh private directory outside any Git repository.
No stdout, no secrets in args, no network and no automatic email.
Write public.json once; wait for challenge.json; sign once; write proof.json;
terminate. Restarting the issuer creates a DIFFERENT public key, rejected by
Render's unchanged verification key. Never save/serialize/recover the private key.
"""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f)
        f.flush()
        os.fsync(f.fileno())


def main():
    folder = Path.cwd().resolve()
    if any((p / ".git").exists() for p in (folder, *folder.parents)):
        return 1
    os.umask(0o077)
    private = Ed25519PrivateKey.generate()
    run = secrets.token_hex(16)
    write_new("public.json", {"public_key": private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(), "run_id": run})
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        if Path("challenge.json").exists():
            challenge = json.loads(Path("challenge.json").read_text())
            if (challenge.get("run_id") != run
                    or not re.fullmatch(r"[a-f0-9]{64}", challenge.get("boot_nonce", ""))):
                return 1
            message = ("EnSound-Gmail-Smoke-v1\n" + run + "\n" + challenge["boot_nonce"]).encode("ascii")
            signature = base64.b64encode(private.sign(message)).decode("ascii")
            # Only one signing call; no private key serialization or persistence.
            write_new("proof.json", {"signature": signature})
            return 0
        time.sleep(0.2)
    return 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        code = 1
    os._exit(code)  # End issuer process; no reusable private key survives.
