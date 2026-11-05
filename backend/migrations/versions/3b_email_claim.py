"""Post-purchase Email claim credentials and shared request throttling.

Revision ID: 3b_email_claim
Revises: 3a_entitlements
"""

from alembic import op
import sqlalchemy as sa


revision = "3b_email_claim"
down_revision = "3a_entitlements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_claim_tokens",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("entitlement_id", sa.Uuid(as_uuid=True), sa.ForeignKey("entitlements.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("purpose", sa.String(32), server_default=sa.text("'device_claim'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("purpose = 'device_claim'", name="ck_email_claim_tokens_purpose"),
    )
    op.create_table(
        "email_claim_sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("entitlement_id", sa.Uuid(as_uuid=True), sa.ForeignKey("entitlements.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("session_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("scope", sa.String(32), server_default=sa.text("'device_claim'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("scope = 'device_claim'", name="ck_email_claim_sessions_scope"),
    )
    op.create_table(
        "email_claim_rate_limits",
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("email_claim_rate_limits")
    op.drop_table("email_claim_sessions")
    op.drop_table("email_claim_tokens")
