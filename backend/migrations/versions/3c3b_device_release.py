"""Purpose-scoped release proof and per-entitlement cooldown.

Revision ID: 3c3b_device_release
Revises: 3c_authorized_devices
"""

from alembic import op
import sqlalchemy as sa


revision = "3c3b_device_release"
down_revision = "3c_authorized_devices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("entitlements", sa.Column("last_device_released_at", sa.DateTime(timezone=True)))
    op.drop_constraint("ck_email_claim_tokens_purpose", "email_claim_tokens", type_="check")
    op.create_check_constraint("ck_email_claim_tokens_purpose", "email_claim_tokens",
                               "purpose IN ('device_claim', 'device_release')")
    op.drop_constraint("ck_email_claim_sessions_scope", "email_claim_sessions", type_="check")
    op.create_check_constraint("ck_email_claim_sessions_scope", "email_claim_sessions",
                               "scope IN ('device_claim', 'device_release')")


def downgrade() -> None:
    # Never erase an issued proof or the cooldown history: doing so could
    # restore an early-release allowance. PostgreSQL rolls the migration back.
    op.execute(sa.text("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM entitlements WHERE last_device_released_at IS NOT NULL)
             OR EXISTS (SELECT 1 FROM email_claim_tokens WHERE purpose = 'device_release')
             OR EXISTS (SELECT 1 FROM email_claim_sessions WHERE scope = 'device_release')
          THEN RAISE EXCEPTION 'Device release records prevent downgrade';
          END IF;
        END $$;
    """))
    op.drop_constraint("ck_email_claim_sessions_scope", "email_claim_sessions", type_="check")
    op.create_check_constraint("ck_email_claim_sessions_scope", "email_claim_sessions",
                               "scope = 'device_claim'")
    op.drop_constraint("ck_email_claim_tokens_purpose", "email_claim_tokens", type_="check")
    op.create_check_constraint("ck_email_claim_tokens_purpose", "email_claim_tokens",
                               "purpose = 'device_claim'")
    op.drop_column("entitlements", "last_device_released_at")
