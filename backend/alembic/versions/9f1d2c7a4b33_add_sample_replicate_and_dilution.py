"""add sample replicate_number and dilution_factor

Revision ID: 9f1d2c7a4b33
Revises: 5b0f3c8e7d91
Create Date: 2026-03-18 11:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f1d2c7a4b33"
down_revision: Union[str, None] = "5b0f3c8e7d91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("samples", sa.Column("replicate_number", sa.Integer(), nullable=True))
    op.add_column("samples", sa.Column("dilution_factor", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("samples", "dilution_factor")
    op.drop_column("samples", "replicate_number")
