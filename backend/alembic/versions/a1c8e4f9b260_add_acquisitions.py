"""Give instrument measurements their own identity and sequence metadata."""
import logging

from alembic import op
import sqlalchemy as sa

revision = "a1c8e4f9b260"
down_revision = "9c5f1d2a8b34"
branch_labels = None
depends_on = None

KEYS = {"batch_id", "injection_order", "vial", "acquired_at", "injection_volume_ul"}


def upgrade():
    op.create_table("acquisitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assay_id", sa.Integer(), sa.ForeignKey("assays.id"), nullable=False),
        sa.Column("sample_id", sa.Integer(), sa.ForeignKey("samples.id"), nullable=False),
        sa.Column("annotations_json", sa.JSON(), nullable=False),
        sa.Column("metadata_review_required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_acquisitions_assay_id", "acquisitions", ["assay_id"])
    op.create_index("ix_acquisitions_sample_id", "acquisitions", ["sample_id"])
    op.add_column("dataset_files", sa.Column("acquisition_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_dataset_files_acquisition", "dataset_files", "acquisitions", ["acquisition_id"], ["id"])
    op.create_index("ix_dataset_files_acquisition_id", "dataset_files", ["acquisition_id"])
    backfill(op.get_bind())


def backfill(connection):
    metadata = sa.MetaData()
    files = sa.Table("dataset_files", metadata, autoload_with=connection)
    datasets = sa.Table("datasets", metadata, autoload_with=connection)
    samples = sa.Table("samples", metadata, autoload_with=connection)
    links = sa.Table("assay_sample_links", metadata, autoload_with=connection)
    acquisitions = sa.Table("acquisitions", metadata, autoload_with=connection)
    rows = connection.execute(sa.select(
        files.c.id, files.c.sample_id, files.c.created_at, datasets.c.assay_id,
        samples.c.annotations_json.label("sample_annotations"), links.c.annotations_json.label("assay_annotations")
    ).select_from(files.join(datasets, files.c.dataset_id == datasets.c.id)
        .join(samples, sa.and_(samples.c.id == files.c.sample_id, samples.c.study_id == datasets.c.study_id))
        .outerjoin(links, sa.and_(links.c.sample_id == files.c.sample_id, links.c.assay_id == datasets.c.assay_id)))
        .where(datasets.c.dataset_kind == "raw", files.c.file_role == "thermo_raw")).mappings().all()
    counts = {}
    for row in rows:
        pair = (row["assay_id"], row["sample_id"])
        counts[pair] = counts.get(pair, 0) + 1
    ambiguous = []
    for row in rows:
        review = counts[(row["assay_id"], row["sample_id"])] > 1
        legacy = {**(row["sample_annotations"] or {}), **(row["assay_annotations"] or {})}
        annotations = {} if review else {key: value for key, value in legacy.items() if key in KEYS}
        identity = connection.execute(acquisitions.insert().values(
            assay_id=row["assay_id"], sample_id=row["sample_id"], annotations_json=annotations,
            metadata_review_required=review, created_at=row["created_at"])).inserted_primary_key[0]
        connection.execute(files.update().where(files.c.id == row["id"]).values(acquisition_id=identity))
        if review:
            ambiguous.append(row["id"])
    if ambiguous:
        logging.getLogger("alembic.runtime.migration").warning(
            "Acquisition metadata needs operator review for file IDs %s; shared legacy values were not copied.", ambiguous)


def downgrade():
    op.drop_index("ix_dataset_files_acquisition_id", table_name="dataset_files")
    op.drop_constraint("fk_dataset_files_acquisition", "dataset_files", type_="foreignkey")
    op.drop_column("dataset_files", "acquisition_id")
    op.drop_table("acquisitions")
