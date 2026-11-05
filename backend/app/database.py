import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    try:
        parsed = make_url(url)
    except Exception:
        raise ValueError("DATABASE_URL must be a valid PostgreSQL URL") from None
    if parsed.drivername not in ("postgresql", "postgresql+psycopg"):
        raise ValueError("DATABASE_URL must use PostgreSQL with psycopg")
    parsed = parsed.set(drivername="postgresql+psycopg")
    host = (parsed.host or "").lower()
    expected_host = os.environ.get("ENSOUND_DEV_DATABASE_HOST", "").strip().lower()
    expected_database = os.environ.get("ENSOUND_DEV_DATABASE_NAME", "neondb").strip()
    if expected_host and (host != expected_host or parsed.database != expected_database):
        raise ValueError("Database does not match the configured Development target")
    if host.endswith(".neon.tech") or expected_host:
        # Prevent libpq query parameters from overriding the verified URL target.
        if any(key in parsed.query for key in ("host", "hostaddr", "port", "dbname", "service", "servicefile")):
            raise ValueError("Database target overrides are not allowed")
        mode = parsed.query.get("sslmode")
        if mode not in (None, "require", "verify-ca", "verify-full"):
            raise ValueError("Neon requires certificate-verified TLS")
        root = parsed.query.get("sslrootcert", "system")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("A TLS trust store is required")
        parsed = parsed.update_query_dict({"sslmode": "verify-full", "sslrootcert": root})
    # Keep credentials intact for the driver; callers must never log this value.
    return parsed.render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(
        get_database_url(), pool_pre_ping=True, hide_parameters=True,
        connect_args={"connect_timeout": 3}
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker:
    return sessionmaker(bind=get_engine())
