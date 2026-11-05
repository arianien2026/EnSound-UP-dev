from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmailClaimToken(Base):
    __tablename__ = "email_claim_tokens"
    __table_args__ = (
        CheckConstraint("purpose IN ('device_claim', 'device_release')", name="ck_email_claim_tokens_purpose"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    entitlement_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("entitlements.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'device_claim'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailClaimSession(Base):
    __tablename__ = "email_claim_sessions"
    __table_args__ = (
        CheckConstraint("scope IN ('device_claim', 'device_release')", name="ck_email_claim_sessions_scope"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    entitlement_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("entitlements.id", ondelete="RESTRICT"), nullable=False
    )
    session_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'device_claim'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailClaimRateLimit(Base):
    __tablename__ = "email_claim_rate_limits"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
