"""add canonical workflow metadata

Revision ID: f2a6d9c1b8e4
Revises: c4d7b8a1f2e3
Create Date: 2026-03-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2a6d9c1b8e4"
down_revision: Union[str, None] = "c4d7b8a1f2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "canonical_workflow_metadata",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("metadata_submission_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.String(length=100), nullable=True),
        sa.Column("profile_version", sa.String(length=50), nullable=True),
        sa.Column("instrument_vendor", sa.String(length=255), nullable=True),
        sa.Column("instrument_model", sa.String(length=255), nullable=True),
        sa.Column("ion_source", sa.String(length=255), nullable=True),
        sa.Column("polarity_mode", sa.String(length=100), nullable=True),
        sa.Column("separation_mode", sa.String(length=100), nullable=True),
        sa.Column("column_name", sa.String(length=255), nullable=True),
        sa.Column("column_chemistry", sa.String(length=255), nullable=True),
        sa.Column("column_temperature_c", sa.String(length=50), nullable=True),
        sa.Column("injection_volume_ul", sa.String(length=50), nullable=True),
        sa.Column("acquisition_mode", sa.String(length=100), nullable=True),
        sa.Column("mz_range", sa.String(length=100), nullable=True),
        sa.Column("resolution", sa.String(length=100), nullable=True),
        sa.Column("acquisition_software", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["metadata_submission_id"], ["metadata_submissions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("metadata_submission_id"),
    )
    op.create_index(
        op.f("ix_canonical_workflow_metadata_experiment_id"),
        "canonical_workflow_metadata",
        ["experiment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_canonical_workflow_metadata_metadata_submission_id"),
        "canonical_workflow_metadata",
        ["metadata_submission_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_canonical_workflow_metadata_metadata_submission_id"), table_name="canonical_workflow_metadata")
    op.drop_index(op.f("ix_canonical_workflow_metadata_experiment_id"), table_name="canonical_workflow_metadata")
    op.drop_table("canonical_workflow_metadata")
