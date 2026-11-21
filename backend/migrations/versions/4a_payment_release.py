"""Pin purchase and checkout identity; add append-only refund events."""

import sqlalchemy as sa
from alembic import op

revision = "4a_payment_release"
down_revision = "3c3b_device_release"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("purchase_key", sa.Uuid()))
    op.add_column("orders", sa.Column("checkout_started_at", sa.DateTime(timezone=True)))
    op.add_column("orders", sa.Column("checkout_merchant_id", sa.String(10)))
    op.create_unique_constraint("uq_orders_purchase_key", "orders", ["purchase_key"])
    op.create_table(
        "refund_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reference", sa.String(96), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("order_id", "reference", name="uq_refund_event_reference"),
    )
    op.create_index("ix_refund_events_order_id", "refund_events", ["order_id"])


def downgrade() -> None:
    raise RuntimeError("Keep payment identity and refund audit records; restore the application instead")
