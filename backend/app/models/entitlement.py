from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Entitlement(Base):
    __tablename__ = "entitlements"
    __table_args__ = (
        UniqueConstraint("source_order_id", name="uq_entitlements_source_order_id"),
        CheckConstraint(
            "product_code = 'ensound_up_web_full_access'", name="ck_entitlements_product_code"
        ),
        CheckConstraint(
            "status IN ('pending', 'failed', 'active', 'revoked')",
            name="ck_entitlements_status",
        ),
        CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL AND revocation_reason IS NOT NULL) "
            "OR (status != 'revoked' AND revoked_at IS NULL AND revocation_reason IS NULL)",
            name="ck_entitlements_revocation",
        ),
        CheckConstraint(
            "(status IN ('active', 'revoked') AND activated_at IS NOT NULL) "
            "OR (status IN ('pending', 'failed') AND activated_at IS NULL)",
            name="ck_entitlements_activation",
        ),
        CheckConstraint(
            "(status = 'failed' AND grant_failed_at IS NOT NULL) "
            "OR (status != 'failed' AND grant_failed_at IS NULL)",
            name="ck_entitlements_grant_failure",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    source_order_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    entitlement_email: Mapped[str] = mapped_column(String(254), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grant_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(128))
    last_device_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
