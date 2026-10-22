"""Study-owned immutable MetaboLights package revisions."""
from alembic import op
import sqlalchemy as sa

revision = "7a3d9b2e6f10"
down_revision = "6d5de2d68288"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("study_packages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("study_id", sa.Integer(), sa.ForeignKey("studies.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("output_uri", sa.String(1024), nullable=True),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("study_id", "revision", name="uq_study_package_revision"),
        sa.UniqueConstraint("study_id", "request_key", name="uq_study_package_request"))
    op.create_index("ix_study_packages_study_id", "study_packages", ["study_id"])
    op.create_index("ix_study_packages_status", "study_packages", ["status"])


def downgrade():
    op.drop_table("study_packages")
