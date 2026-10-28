"""Store camera pulse and facial geometry estimates separately from AI output."""
from alembic import op
import sqlalchemy as sa

revision = "0007_optical_metrics"
down_revision = "0006_supplemental_metrics"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analysis_results", sa.Column("optical_metrics", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("analysis_results", "optical_metrics")
