"""add dataset sample links

Revision ID: e7c2a4f9b1d3
Revises: d1b9e2c4f7a1
Create Date: 2026-04-09 20:05:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import table, column


# revision identifiers, used by Alembic.
revision: str = "e7c2a4f9b1d3"
down_revision: Union[str, None] = "d1b9e2c4f7a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dataset_sample_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("sample_role", sa.String(length=50), nullable=False, server_default="input"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["sample_id"], ["samples.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "sample_id", name="uq_dataset_sample"),
    )
    op.create_index(op.f("ix_dataset_sample_links_dataset_id"), "dataset_sample_links", ["dataset_id"], unique=False)
    op.create_index(op.f("ix_dataset_sample_links_sample_id"), "dataset_sample_links", ["sample_id"], unique=False)
    op.alter_column("dataset_sample_links", "sample_role", server_default=None)
    op.alter_column("dataset_sample_links", "created_at", server_default=None)

    bind = op.get_bind()
    dataset_files = table(
        "dataset_files",
        column("id", sa.Integer()),
        column("dataset_id", sa.Integer()),
        column("sample_id", sa.Integer()),
    )
    datasets = table(
        "datasets",
        column("id", sa.Integer()),
        column("study_id", sa.Integer()),
        column("assay_id", sa.Integer()),
        column("dataset_kind", sa.String()),
    )
    workflow_jobs = table(
        "workflow_jobs",
        column("study_id", sa.Integer()),
        column("assay_id", sa.Integer()),
        column("input_manifest_json", sa.JSON()),
    )
    dataset_sample_links = table(
        "dataset_sample_links",
        column("dataset_id", sa.Integer()),
        column("sample_id", sa.Integer()),
        column("sample_role", sa.String()),
        column("created_at", sa.DateTime(timezone=True)),
    )

    seen: set[tuple[int, int]] = set()

    raw_rows = bind.execute(
        sa.select(dataset_files.c.dataset_id, dataset_files.c.sample_id).where(dataset_files.c.sample_id.is_not(None))
    )
    for dataset_id, sample_id in raw_rows:
        key = (int(dataset_id), int(sample_id))
        if key in seen:
            continue
        seen.add(key)
        bind.execute(
            dataset_sample_links.insert().values(
                dataset_id=key[0],
                sample_id=key[1],
                sample_role="input",
                created_at=sa.func.now(),
            )
        )

    derived_datasets = bind.execute(
        sa.select(datasets.c.id, datasets.c.study_id, datasets.c.assay_id).where(datasets.c.dataset_kind.in_(["derived", "maf"]))
    ).fetchall()
    if not derived_datasets:
        return

    dataset_ids_by_assay: dict[tuple[int, int], list[int]] = {}
    for dataset_id, study_id, assay_id in derived_datasets:
        dataset_ids_by_assay.setdefault((int(study_id), int(assay_id)), []).append(int(dataset_id))

    jobs = bind.execute(sa.select(workflow_jobs.c.study_id, workflow_jobs.c.assay_id, workflow_jobs.c.input_manifest_json)).fetchall()
    for study_id, assay_id, manifest in jobs:
        target_dataset_ids = dataset_ids_by_assay.get((int(study_id), int(assay_id)), [])
        if not target_dataset_ids:
            continue
        manifest = manifest or {}
        sample_ids: list[int] = []
        for item in manifest.get("selected_files") or []:
            sample_id = item.get("sample_id")
            if sample_id is None:
                continue
            try:
                sample_ids.append(int(sample_id))
            except (TypeError, ValueError):
                continue

        if not sample_ids:
            file_ids: list[int] = []
            for value in manifest.get("file_ids") or []:
                try:
                    file_ids.append(int(value))
                except (TypeError, ValueError):
                    continue
            if file_ids:
                sample_rows = bind.execute(
                    sa.select(dataset_files.c.sample_id).where(
                        sa.and_(dataset_files.c.id.in_(file_ids), dataset_files.c.sample_id.is_not(None))
                    )
                )
                sample_ids = [int(row[0]) for row in sample_rows if row[0] is not None]

        for dataset_id in target_dataset_ids:
            for sample_id in dict.fromkeys(sample_ids):
                key = (int(dataset_id), int(sample_id))
                if key in seen:
                    continue
                seen.add(key)
                bind.execute(
                    dataset_sample_links.insert().values(
                        dataset_id=key[0],
                        sample_id=key[1],
                        sample_role="input",
                        created_at=sa.func.now(),
                    )
                )


def downgrade() -> None:
    op.drop_index(op.f("ix_dataset_sample_links_sample_id"), table_name="dataset_sample_links")
    op.drop_index(op.f("ix_dataset_sample_links_dataset_id"), table_name="dataset_sample_links")
    op.drop_table("dataset_sample_links")
