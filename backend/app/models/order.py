from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Uuid, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("product_code = 'ensound_up_web_full_access'", name="ck_orders_product_code"),
        CheckConstraint("currency = 'TWD'", name="ck_orders_currency"),
        CheckConstraint("amount > 0", name="ck_orders_amount_positive"),
        CheckConstraint(
            "pricing_code IN ('regular', 'launch_promo')", name="ck_orders_pricing_code"
        ),
        CheckConstraint(
            "status IN ('pending', 'paid', 'expired', 'cancelled', 'refund_pending', 'refunded')",
            name="ck_orders_status",
        ),
        CheckConstraint("expires_at > created_at", name="ck_orders_expiry_after_creation"),
        UniqueConstraint("provider_order_id", name="uq_orders_provider_order_id"),
        UniqueConstraint("provider_trade_no", name="uq_orders_provider_trade_no"),
        UniqueConstraint(
            "email_verification_token_hash", name="uq_orders_email_verification_token_hash"
        ),
        Index("ix_orders_entitlement_email", "entitlement_email"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    entitlement_email: Mapped[str] = mapped_column(String(254), nullable=False)
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    pricing_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_trade_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_environment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_payment_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider_paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verification_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email_verification_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
