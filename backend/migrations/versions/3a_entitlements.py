"""Add purchased entitlement records without activating payment callbacks.

Revision ID: 3a_entitlements
Revises: 2b_ecpay_stage_payment_result
"""

from alembic import op
import sqlalchemy as sa


revision = "3a_entitlements"
down_revision = "2b_ecpay_stage_payment_result"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entitlements",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_order_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("entitlement_email", sa.String(254), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("grant_failed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revocation_reason", sa.String(128)),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["source_order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("source_order_id", name="uq_entitlements_source_order_id"),
        sa.CheckConstraint("product_code = 'ensound_up_web_full_access'", name="ck_entitlements_product_code"),
        sa.CheckConstraint("status IN ('pending', 'failed', 'active', 'revoked')", name="ck_entitlements_status"),
        sa.CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL AND revocation_reason IS NOT NULL) "
            "OR (status != 'revoked' AND revoked_at IS NULL AND revocation_reason IS NULL)",
            name="ck_entitlements_revocation",
        ),
        sa.CheckConstraint(
            "(status IN ('active', 'revoked') AND activated_at IS NOT NULL) "
            "OR (status IN ('pending', 'failed') AND activated_at IS NULL)",
            name="ck_entitlements_activation",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND grant_failed_at IS NOT NULL) "
            "OR (status != 'failed' AND grant_failed_at IS NULL)",
            name="ck_entitlements_grant_failure",
        ),
    )


def downgrade() -> None:
    op.drop_table("entitlements")
