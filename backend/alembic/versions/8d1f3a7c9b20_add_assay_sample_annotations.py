"""add assay sample annotations

Revision ID: 8d1f3a7c9b20
Revises: 6f4c3a2b1d0e
Create Date: 2026-06-29 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8d1f3a7c9b20"
down_revision: Union[str, None] = "6f4c3a2b1d0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assay_sample_links", sa.Column("annotations_json", sa.JSON(), nullable=True))
    op.execute("UPDATE assay_sample_links SET annotations_json = '{}' WHERE annotations_json IS NULL")


def downgrade() -> None:
    op.drop_column("assay_sample_links", "annotations_json")
