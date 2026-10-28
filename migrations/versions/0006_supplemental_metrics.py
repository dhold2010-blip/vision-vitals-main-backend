"""Persist entered measurements and reference-based estimates with provenance."""
from alembic import op
import sqlalchemy as sa

revision = "0006_supplemental_metrics"
down_revision = "0005_camera_metrics"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analysis_results", sa.Column("supplemental_metrics", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("analysis_results", "supplemental_metrics")
