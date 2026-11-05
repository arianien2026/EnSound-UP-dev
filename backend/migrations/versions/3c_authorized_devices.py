"""Authorized devices for backend-only online entitlement checks.

Revision ID: 3c_authorized_devices
Revises: 3b_email_claim
"""

from alembic import op
import sqlalchemy as sa


revision = "3c_authorized_devices"
down_revision = "3b_email_claim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "authorized_devices",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("entitlement_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("entitlements.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("device_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("credential_hash", sa.String(64), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("entitlement_id", "device_id",
                            name="uq_authorized_devices_entitlement_device"),
        sa.UniqueConstraint("credential_hash", name="uq_authorized_devices_credential_hash"),
    )
    op.create_index("ix_authorized_devices_entitlement_id", "authorized_devices", ["entitlement_id"])


def downgrade() -> None:
    op.drop_index("ix_authorized_devices_entitlement_id", table_name="authorized_devices")
    op.drop_table("authorized_devices")
