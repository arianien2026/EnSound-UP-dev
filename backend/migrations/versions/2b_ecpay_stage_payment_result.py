"""Store verified ECPay Stage payment result identity.

Revision ID: 2b_ecpay_stage_payment_result
Revises: 1e_email_verification
"""

from alembic import op
import sqlalchemy as sa


revision = "2b_ecpay_stage_payment_result"
down_revision = "1e_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("provider_trade_no", sa.String(20)))
    op.add_column("orders", sa.Column("payment_environment", sa.String(32)))
    op.add_column("orders", sa.Column("provider_payment_type", sa.String(50)))
    op.add_column("orders", sa.Column("provider_paid_at", sa.DateTime(timezone=True)))
    op.create_unique_constraint("uq_orders_provider_trade_no", "orders", ["provider_trade_no"])


def downgrade() -> None:
    op.drop_constraint("uq_orders_provider_trade_no", "orders", type_="unique")
    op.drop_column("orders", "provider_paid_at")
    op.drop_column("orders", "provider_payment_type")
    op.drop_column("orders", "payment_environment")
    op.drop_column("orders", "provider_trade_no")
