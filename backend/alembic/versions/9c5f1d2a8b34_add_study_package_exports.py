"""Track the export of a package revision into a self-contained folder."""
from alembic import op
import sqlalchemy as sa

revision = "9c5f1d2a8b34"
down_revision = "8b4e0c3f7a21"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("study_packages", sa.Column("export_status", sa.String(50), nullable=True))
    op.add_column("study_packages", sa.Column("export_uri", sa.String(1024), nullable=True))
    op.add_column("study_packages", sa.Column("export_error", sa.Text(), nullable=True))
    op.add_column("study_packages", sa.Column("export_requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("study_packages", sa.Column("export_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("study_packages", sa.Column("export_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_study_packages_export_status", "study_packages", ["export_status"])


def downgrade():
    op.drop_index("ix_study_packages_export_status", table_name="study_packages")
    for column in ("export_finished_at", "export_requested_at", "export_requested_by", "export_error",
                   "export_uri", "export_status"):
        op.drop_column("study_packages", column)
