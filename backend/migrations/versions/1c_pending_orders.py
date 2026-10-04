"""Create pending order foundation.

Revision ID: 1c_pending_orders
Revises:
"""

from alembic import op
import sqlalchemy as sa


revision = "1c_pending_orders"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("entitlement_email", sa.String(length=254), nullable=False),
        sa.Column("product_code", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("pricing_code", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_order_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "product_code = 'ensound_up_web_full_access'", name="ck_orders_product_code"
        ),
        sa.CheckConstraint("currency = 'TWD'", name="ck_orders_currency"),
        sa.CheckConstraint("amount > 0", name="ck_orders_amount_positive"),
        sa.CheckConstraint(
            "pricing_code IN ('regular', 'launch_promo')", name="ck_orders_pricing_code"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'paid', 'expired', 'cancelled', 'refund_pending', 'refunded')",
            name="ck_orders_status",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_orders_expiry_after_creation"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_order_id", name="uq_orders_provider_order_id"),
    )
    op.create_index("ix_orders_entitlement_email", "orders", ["entitlement_email"])


def downgrade() -> None:
    op.drop_index("ix_orders_entitlement_email", table_name="orders")
    op.drop_table("orders")
