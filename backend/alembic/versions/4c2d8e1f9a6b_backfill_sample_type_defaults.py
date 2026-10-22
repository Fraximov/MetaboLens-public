"""backfill sample type defaults

Revision ID: 4c2d8e1f9a6b
Revises: e7c2a4f9b1d3
Create Date: 2026-04-13 10:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4c2d8e1f9a6b"
down_revision: Union[str, None] = "e7c2a4f9b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE samples SET sample_type = 'sample' WHERE sample_type IS NULL OR TRIM(sample_type) = ''")
    op.alter_column("samples", "sample_type", existing_type=sa.String(length=100), server_default="sample")


def downgrade() -> None:
    op.alter_column("samples", "sample_type", existing_type=sa.String(length=100), server_default=None)
