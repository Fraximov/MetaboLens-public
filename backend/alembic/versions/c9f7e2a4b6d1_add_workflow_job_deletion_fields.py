"""add workflow job deletion fields

Revision ID: c9f7e2a4b6d1
Revises: b7e4a1c9d2f6
Create Date: 2026-08-12 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9f7e2a4b6d1"
down_revision: Union[str, None] = "b7e4a1c9d2f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("workflow_jobs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workflow_jobs", sa.Column("deleted_by", sa.Integer(), nullable=True))
    op.add_column("workflow_jobs", sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_workflow_jobs_deleted_by_users",
        "workflow_jobs",
        "users",
        ["deleted_by"],
        ["id"],
    )
    op.create_index("ix_workflow_jobs_deleted_at", "workflow_jobs", ["deleted_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_workflow_jobs_deleted_at", table_name="workflow_jobs")
    op.drop_constraint("fk_workflow_jobs_deleted_by_users", "workflow_jobs", type_="foreignkey")
    op.drop_column("workflow_jobs", "purged_at")
    op.drop_column("workflow_jobs", "deleted_by")
    op.drop_column("workflow_jobs", "deleted_at")
