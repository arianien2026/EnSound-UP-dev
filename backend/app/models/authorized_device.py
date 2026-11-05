from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuthorizedDevice(Base):
    __tablename__ = "authorized_devices"
    __table_args__ = (
        UniqueConstraint("entitlement_id", "device_id", name="uq_authorized_devices_entitlement_device"),
        UniqueConstraint("credential_hash", name="uq_authorized_devices_credential_hash"),
        Index("ix_authorized_devices_entitlement_id", "entitlement_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    entitlement_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("entitlements.id", ondelete="RESTRICT"), nullable=False
    )
    device_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    credential_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    credential_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
