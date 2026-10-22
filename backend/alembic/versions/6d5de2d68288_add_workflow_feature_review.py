"""Persistent sparse feature review drafts and frozen revisions."""
from alembic import op
import sqlalchemy as sa

revision = '6d5de2d68288'
down_revision = 'c9f7e2a4b6d1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('workflow_feature_reviews',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('workflow_job_id', sa.Integer(), sa.ForeignKey('workflow_jobs.id'), nullable=False),
        sa.Column('preview_file_id', sa.Integer(), sa.ForeignKey('dataset_files.id'), nullable=False),
        sa.Column('preview_checksums_json', sa.JSON(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('overrides_json', sa.JSON(), nullable=False),
        sa.Column('frozen_revision', sa.Integer(), nullable=True),
        sa.Column('frozen_file_id', sa.Integer(), sa.ForeignKey('dataset_files.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('workflow_job_id', 'preview_file_id'))
    op.create_index('ix_workflow_feature_reviews_workflow_job_id', 'workflow_feature_reviews', ['workflow_job_id'])


def downgrade():
    op.drop_index('ix_workflow_feature_reviews_workflow_job_id', table_name='workflow_feature_reviews')
    op.drop_table('workflow_feature_reviews')
