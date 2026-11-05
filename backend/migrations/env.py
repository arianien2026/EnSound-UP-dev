from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.database import Base, get_database_url
from app import models  # noqa: F401 - register models with Base.metadata


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_database_url()
    target = make_url(url)
    # Frozen Development release: no remote migration is authorized. Preserve
    # only the existing opt-in local PostgreSQL fixture, whose own tests verify
    # server identity before invoking Alembic. Never allow query target overrides.
    if (target.drivername != "postgresql+psycopg" or
            target.host not in ("localhost", "127.0.0.1", "::1") or
            target.port != 55432 or target.database != "ensound_3a_isolated_test" or
            target.username != "ensound_isolated_test" or target.query):
        raise RuntimeError("Remote migrations are disabled for the frozen Development schema")
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
