"""refactor to LabID-style study assay schema

Revision ID: a8b3c1d4e5f6
Revises: f2a6d9c1b8e4
Create Date: 2026-04-09 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a8b3c1d4e5f6"
down_revision: Union[str, None] = "f2a6d9c1b8e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_TABLES = [
    "workflow_run_artifacts",
    "result_sets",
    "provenance_events",
    "file_assets",
    "workflow_jobs",
    "canonical_workflow_metadata",
    "metadata_submissions",
    "samples",
    "role_assignments",
    "experiments",
]


def upgrade() -> None:
    for table_name in OLD_TABLES:
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))

    op.create_table(
        "studies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_or_internal_identifier", sa.String(length=100), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_studies_id"), "studies", ["id"], unique=False)
    op.create_index(op.f("ix_studies_public_or_internal_identifier"), "studies", ["public_or_internal_identifier"], unique=True)
    op.create_index(op.f("ix_studies_status"), "studies", ["status"], unique=False)
    op.create_index(op.f("ix_studies_created_by"), "studies", ["created_by"], unique=False)

    op.create_table(
        "role_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("study_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"]),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "study_id", "role", name="uq_user_study_role"),
    )
    op.create_index(op.f("ix_role_assignments_study_id"), "role_assignments", ["study_id"], unique=False)
    op.create_index(op.f("ix_role_assignments_role"), "role_assignments", ["role"], unique=False)
    op.create_index(op.f("ix_role_assignments_user_id"), "role_assignments", ["user_id"], unique=False)

    op.create_table(
        "samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("study_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("replicate_number", sa.Integer(), nullable=True),
        sa.Column("dilution_factor", sa.Float(), nullable=True),
        sa.Column("sample_type", sa.String(length=100), nullable=True),
        sa.Column("organism", sa.String(length=100), nullable=True),
        sa.Column("matrix", sa.String(length=100), nullable=True),
        sa.Column("annotations_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_samples_study_id"), "samples", ["study_id"], unique=False)

    op.create_table(
        "protocols",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("study_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("protocol_type", sa.String(length=100), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("parameters_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_protocols_study_id"), "protocols", ["study_id"], unique=False)

    op.create_table(
        "assays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("study_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("assay_type", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assays_study_id"), "assays", ["study_id"], unique=False)
    op.create_index(op.f("ix_assays_status"), "assays", ["status"], unique=False)
    op.create_index(op.f("ix_assays_is_default"), "assays", ["is_default"], unique=False)
    op.create_index(op.f("ix_assays_created_by"), "assays", ["created_by"], unique=False)

    op.create_table(
        "assay_sample_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assay_id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("sample_role", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assay_id"], ["assays.id"]),
        sa.ForeignKeyConstraint(["sample_id"], ["samples.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assay_id", "sample_id", name="uq_assay_sample"),
    )
    op.create_index(op.f("ix_assay_sample_links_assay_id"), "assay_sample_links", ["assay_id"], unique=False)
    op.create_index(op.f("ix_assay_sample_links_sample_id"), "assay_sample_links", ["sample_id"], unique=False)

    op.create_table(
        "assay_protocol_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assay_id", sa.Integer(), nullable=False),
        sa.Column("protocol_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assay_id"], ["assays.id"]),
        sa.ForeignKeyConstraint(["protocol_id"], ["protocols.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assay_id", "protocol_id", name="uq_assay_protocol"),
    )
    op.create_index(op.f("ix_assay_protocol_links_assay_id"), "assay_protocol_links", ["assay_id"], unique=False)
    op.create_index(op.f("ix_assay_protocol_links_protocol_id"), "assay_protocol_links", ["protocol_id"], unique=False)

    op.create_table(
        "metabolomics_assays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assay_id", sa.Integer(), nullable=False),
        sa.Column("assay_technique", sa.String(length=255), nullable=True),
        sa.Column("measurement_type", sa.String(length=255), nullable=True),
        sa.Column("polarity", sa.String(length=100), nullable=True),
        sa.Column("column_type", sa.String(length=255), nullable=True),
        sa.Column("chromatography_instrument", sa.String(length=255), nullable=True),
        sa.Column("column_model", sa.String(length=255), nullable=True),
        sa.Column("scan_mz_range", sa.String(length=100), nullable=True),
        sa.Column("instrument_model", sa.String(length=255), nullable=True),
        sa.Column("ion_source", sa.String(length=255), nullable=True),
        sa.Column("mass_analyzer", sa.String(length=255), nullable=True),
        sa.Column("ms_type", sa.String(length=100), nullable=True),
        sa.Column("instrument_vendor", sa.String(length=255), nullable=True),
        sa.Column("acquisition_software", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assay_id"], ["assays.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assay_id"),
    )
    op.create_index(op.f("ix_metabolomics_assays_assay_id"), "metabolomics_assays", ["assay_id"], unique=True)

    op.create_table(
        "datasets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("study_id", sa.Integer(), nullable=False),
        sa.Column("assay_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("dataset_kind", sa.String(length=50), nullable=False),
        sa.Column("dataset_type", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assay_id"], ["assays.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_datasets_study_id"), "datasets", ["study_id"], unique=False)
    op.create_index(op.f("ix_datasets_assay_id"), "datasets", ["assay_id"], unique=False)
    op.create_index(op.f("ix_datasets_dataset_kind"), "datasets", ["dataset_kind"], unique=False)
    op.create_index(op.f("ix_datasets_status"), "datasets", ["status"], unique=False)
    op.create_index(op.f("ix_datasets_created_by"), "datasets", ["created_by"], unique=False)

    op.create_table(
        "dataset_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=True),
        sa.Column("file_role", sa.String(length=100), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("storage_uri", sa.String(length=1024), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("media_type", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["sample_id"], ["samples.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_dataset_files_dataset_id"), "dataset_files", ["dataset_id"], unique=False)
    op.create_index(op.f("ix_dataset_files_sample_id"), "dataset_files", ["sample_id"], unique=False)
    op.create_index(op.f("ix_dataset_files_status"), "dataset_files", ["status"], unique=False)
    op.create_index(op.f("ix_dataset_files_created_by"), "dataset_files", ["created_by"], unique=False)

    op.create_table(
        "metadata_submissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("study_id", sa.Integer(), nullable=False),
        sa.Column("assay_id", sa.Integer(), nullable=False),
        sa.Column("submitted_by", sa.Integer(), nullable=False),
        sa.Column("raw_payload_json", sa.JSON(), nullable=False),
        sa.Column("normalized_payload_json", sa.JSON(), nullable=False),
        sa.Column("validation_status", sa.String(length=50), nullable=False),
        sa.Column("validation_messages_json", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assay_id"], ["assays.id"]),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"]),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_metadata_submissions_study_id"), "metadata_submissions", ["study_id"], unique=False)
    op.create_index(op.f("ix_metadata_submissions_assay_id"), "metadata_submissions", ["assay_id"], unique=False)
    op.create_index(op.f("ix_metadata_submissions_submitted_by"), "metadata_submissions", ["submitted_by"], unique=False)

    op.create_table(
        "workflow_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("study_id", sa.Integer(), nullable=False),
        sa.Column("assay_id", sa.Integer(), nullable=False),
        sa.Column("workflow_definition_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("input_manifest_json", sa.JSON(), nullable=False),
        sa.Column("parameter_values_json", sa.JSON(), nullable=False),
        sa.Column("execution_backend", sa.String(length=50), nullable=False),
        sa.Column("execution_profile", sa.String(length=100), nullable=False),
        sa.Column("run_name", sa.String(length=255), nullable=True),
        sa.Column("run_root", sa.String(length=1024), nullable=True),
        sa.Column("results_path", sa.String(length=1024), nullable=True),
        sa.Column("logs_path", sa.String(length=1024), nullable=True),
        sa.Column("artifact_manifest_path", sa.String(length=1024), nullable=True),
        sa.Column("status_payload_json", sa.JSON(), nullable=False),
        sa.Column("external_run_id", sa.String(length=255), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assay_id"], ["assays.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"]),
        sa.ForeignKeyConstraint(["workflow_definition_id"], ["workflow_definitions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflow_jobs_study_id"), "workflow_jobs", ["study_id"], unique=False)
    op.create_index(op.f("ix_workflow_jobs_assay_id"), "workflow_jobs", ["assay_id"], unique=False)
    op.create_index(op.f("ix_workflow_jobs_status"), "workflow_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_workflow_jobs_workflow_definition_id"), "workflow_jobs", ["workflow_definition_id"], unique=False)

    op.create_table(
        "workflow_run_artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_job_id", sa.Integer(), nullable=False),
        sa.Column("dataset_file_id", sa.Integer(), nullable=False),
        sa.Column("artifact_role", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_file_id"], ["dataset_files.id"]),
        sa.ForeignKeyConstraint(["workflow_job_id"], ["workflow_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflow_run_artifacts_dataset_file_id"), "workflow_run_artifacts", ["dataset_file_id"], unique=False)
    op.create_index(op.f("ix_workflow_run_artifacts_workflow_job_id"), "workflow_run_artifacts", ["workflow_job_id"], unique=False)

    op.create_table(
        "result_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("study_id", sa.Integer(), nullable=False),
        sa.Column("assay_id", sa.Integer(), nullable=True),
        sa.Column("workflow_job_id", sa.Integer(), nullable=True),
        sa.Column("result_type", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assay_id"], ["assays.id"]),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"]),
        sa.ForeignKeyConstraint(["workflow_job_id"], ["workflow_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_result_sets_study_id"), "result_sets", ["study_id"], unique=False)
    op.create_index(op.f("ix_result_sets_assay_id"), "result_sets", ["assay_id"], unique=False)

    op.create_table(
        "provenance_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("study_id", sa.Integer(), nullable=False),
        sa.Column("assay_id", sa.Integer(), nullable=True),
        sa.Column("workflow_job_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("source_ref_json", sa.JSON(), nullable=False),
        sa.Column("output_ref_json", sa.JSON(), nullable=False),
        sa.Column("software_versions_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assay_id"], ["assays.id"]),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"]),
        sa.ForeignKeyConstraint(["workflow_job_id"], ["workflow_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_provenance_events_study_id"), "provenance_events", ["study_id"], unique=False)
    op.create_index(op.f("ix_provenance_events_assay_id"), "provenance_events", ["assay_id"], unique=False)
    op.create_index(op.f("ix_provenance_events_event_type"), "provenance_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_provenance_events_workflow_job_id"), "provenance_events", ["workflow_job_id"], unique=False)


def downgrade() -> None:
    raise NotImplementedError("Downgrade is not supported for the LabID-style schema reset migration")
