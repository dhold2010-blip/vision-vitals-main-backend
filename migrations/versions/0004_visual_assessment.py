"""Persist image-specific visual observations without changing old reports."""

from alembic import op
import sqlalchemy as sa


revision = "0004_visual_assessment"
down_revision = "0003_device_capture_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analysis_results", sa.Column("visual_assessment", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("analysis_results", "visual_assessment")
