"""add dataset file source locator fields

Revision ID: d1b9e2c4f7a1
Revises: a8b3c1d4e5f6
Create Date: 2026-04-09 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1b9e2c4f7a1"
down_revision: Union[str, None] = "a8b3c1d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dataset_files", sa.Column("source_scheme", sa.String(length=50), nullable=False, server_default="file"))
    op.add_column("dataset_files", sa.Column("ingest_mode", sa.String(length=50), nullable=False, server_default="uploaded_file"))
    op.create_index(op.f("ix_dataset_files_source_scheme"), "dataset_files", ["source_scheme"], unique=False)
    op.create_index(op.f("ix_dataset_files_ingest_mode"), "dataset_files", ["ingest_mode"], unique=False)
    op.alter_column("dataset_files", "source_scheme", server_default=None)
    op.alter_column("dataset_files", "ingest_mode", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_dataset_files_ingest_mode"), table_name="dataset_files")
    op.drop_index(op.f("ix_dataset_files_source_scheme"), table_name="dataset_files")
    op.drop_column("dataset_files", "ingest_mode")
    op.drop_column("dataset_files", "source_scheme")
