"""Add pending-order email verification state.

Revision ID: 1e_email_verification
Revises: 1c_pending_orders
"""

from alembic import op
import sqlalchemy as sa


revision = "1e_email_verification"
down_revision = "1c_pending_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.add_column("orders", sa.Column("email_verification_token_hash", sa.String(64)))
    op.add_column("orders", sa.Column("email_verification_expires_at", sa.DateTime(timezone=True)))
    op.create_unique_constraint(
        "uq_orders_email_verification_token_hash", "orders", ["email_verification_token_hash"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_orders_email_verification_token_hash", "orders", type_="unique")
    op.drop_column("orders", "email_verification_expires_at")
    op.drop_column("orders", "email_verification_token_hash")
    op.drop_column("orders", "email_verified_at")
