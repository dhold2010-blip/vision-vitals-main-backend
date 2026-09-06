"""Add secure device, capture, and sensor integration.

Revision ID: 0002_part2_hardware
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_part2_hardware"
down_revision = "0001_part1_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_images",
        sa.Column("source", sa.String(32), nullable=False, server_default="UPLOAD"),
    )

    op.create_table(
        "devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_identifier", sa.String(128), nullable=False),
        sa.Column("device_name", sa.String(120), nullable=False),
        sa.Column("device_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("firmware_version", sa.String(64)),
        sa.Column("software_version", sa.String(64)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("credential_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_user_id", "device_identifier", name="uq_device_owner_identifier"),
    )
    op.create_index("ix_devices_owner_user_id", "devices", ["owner_user_id"])

    op.create_table(
        "device_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_device_sessions_device_id", "device_sessions", ["device_id"])
    op.create_index("ix_device_sessions_active", "device_sessions", ["device_id", "revoked_at"])

    op.create_table(
        "device_captures",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("analysis_id", sa.String(36), sa.ForeignKey("analyses.id", ondelete="SET NULL")),
        sa.Column("capture_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("device_id", "idempotency_key", name="uq_device_capture_idempotency"),
    )
    op.create_index("ix_device_captures_device_id", "device_captures", ["device_id"])
    op.create_index("ix_device_captures_user_id", "device_captures", ["user_id"])
    op.create_index("ix_device_captures_analysis_id", "device_captures", ["analysis_id"])

    op.create_table(
        "sensor_readings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("capture_id", sa.String(36), sa.ForeignKey("device_captures.id", ondelete="SET NULL")),
        sa.Column("sensor_type", sa.String(64), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("quality", sa.String(32)),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sensor_readings_device_id", "sensor_readings", ["device_id"])
    op.create_index("ix_sensor_readings_capture_id", "sensor_readings", ["capture_id"])


def downgrade() -> None:
    op.drop_table("sensor_readings")
    op.drop_table("device_captures")
    op.drop_table("device_sessions")
    op.drop_table("devices")
    op.drop_column("analysis_images", "source")