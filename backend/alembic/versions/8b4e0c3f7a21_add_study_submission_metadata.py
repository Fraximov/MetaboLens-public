"""Store editable submission details alongside existing Study and Protocol records."""
from alembic import op
import sqlalchemy as sa

revision = "8b4e0c3f7a21"
down_revision = "7a3d9b2e6f10"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("studies", sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"))
    op.alter_column("studies", "metadata_json", server_default=None)


def downgrade():
    op.drop_column("studies", "metadata_json")
