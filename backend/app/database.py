import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgresql+psycopg://"):
        return url
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    raise ValueError("DATABASE_URL must be a PostgreSQL URL")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(
        get_database_url(), pool_pre_ping=True, connect_args={"connect_timeout": 3}
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker:
    return sessionmaker(bind=get_engine())
