"""Persist camera geometry separately from generated descriptions."""
from alembic import op
import sqlalchemy as sa

revision = "0005_camera_metrics"
down_revision = "0004_visual_assessment"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analysis_results", sa.Column("camera_metrics", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("analysis_results", "camera_metrics")
