"""add sample description and source file path

Revision ID: 5b0f3c8e7d91
Revises: bba34086291e
Create Date: 2026-03-17 16:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5b0f3c8e7d91"
down_revision: Union[str, None] = "bba34086291e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("samples", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("samples", sa.Column("source_file_path", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("samples", "source_file_path")
    op.drop_column("samples", "description")
