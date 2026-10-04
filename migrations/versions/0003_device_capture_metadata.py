"""Persist safe hardware capture metadata.

Revision ID: 0003_device_capture_metadata
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_device_capture_metadata"
down_revision = "0002_part2_hardware"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_captures",
        sa.Column(
            "capture_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("device_captures", "capture_metadata")