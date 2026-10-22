"""add workflow execution contract fields

Revision ID: c4d7b8a1f2e3
Revises: 9f1d2c7a4b33
Create Date: 2026-03-24 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4d7b8a1f2e3"
down_revision: Union[str, None] = "9f1d2c7a4b33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("workflow_definitions", sa.Column("execution_backend", sa.String(length=50), nullable=False, server_default="nextflow"))
    op.add_column("workflow_definitions", sa.Column("execution_profile", sa.String(length=100), nullable=False, server_default="local_docker"))
    op.add_column("workflow_definitions", sa.Column("pipeline_root", sa.String(length=1024), nullable=True))
    op.add_column("workflow_definitions", sa.Column("default_nextflow_profile", sa.String(length=100), nullable=False, server_default="docker"))
    op.add_column("workflow_definitions", sa.Column("static_input_config_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))

    op.add_column("workflow_jobs", sa.Column("execution_profile", sa.String(length=100), nullable=False, server_default="local_docker"))
    op.add_column("workflow_jobs", sa.Column("run_name", sa.String(length=255), nullable=True))
    op.add_column("workflow_jobs", sa.Column("run_root", sa.String(length=1024), nullable=True))
    op.add_column("workflow_jobs", sa.Column("results_path", sa.String(length=1024), nullable=True))
    op.add_column("workflow_jobs", sa.Column("logs_path", sa.String(length=1024), nullable=True))
    op.add_column("workflow_jobs", sa.Column("artifact_manifest_path", sa.String(length=1024), nullable=True))
    op.add_column("workflow_jobs", sa.Column("status_payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("workflow_jobs", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))

    op.alter_column("workflow_definitions", "execution_backend", server_default=None)
    op.alter_column("workflow_definitions", "execution_profile", server_default=None)
    op.alter_column("workflow_definitions", "default_nextflow_profile", server_default=None)
    op.alter_column("workflow_definitions", "static_input_config_json", server_default=None)
    op.alter_column("workflow_jobs", "execution_profile", server_default=None)
    op.alter_column("workflow_jobs", "status_payload_json", server_default=None)


def downgrade() -> None:
    op.drop_column("workflow_jobs", "submitted_at")
    op.drop_column("workflow_jobs", "status_payload_json")
    op.drop_column("workflow_jobs", "artifact_manifest_path")
    op.drop_column("workflow_jobs", "logs_path")
    op.drop_column("workflow_jobs", "results_path")
    op.drop_column("workflow_jobs", "run_root")
    op.drop_column("workflow_jobs", "run_name")
    op.drop_column("workflow_jobs", "execution_profile")

    op.drop_column("workflow_definitions", "static_input_config_json")
    op.drop_column("workflow_definitions", "default_nextflow_profile")
    op.drop_column("workflow_definitions", "pipeline_root")
    op.drop_column("workflow_definitions", "execution_profile")
    op.drop_column("workflow_definitions", "execution_backend")
