from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(100), default="local")
    external_subject_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    roles: Mapped[list["RoleAssignment"]] = relationship(
        back_populates="user",
        foreign_keys="RoleAssignment.user_id",
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Study(Base):
    __tablename__ = "studies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    public_or_internal_identifier: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StudyPackage(Base):
    """An immutable Study export request, independent of source-job retention."""
    __tablename__ = "study_packages"
    __table_args__ = (
        UniqueConstraint("study_id", "revision", name="uq_study_package_revision"),
        UniqueConstraint("study_id", "request_key", name="uq_study_package_request"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(36))
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    request_json: Mapped[dict] = mapped_column(JSON)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(50), default="queued", index=True)
    output_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    report_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Export copies the linked spectra into a self-contained folder; None until requested.
    export_status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    export_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    export_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    export_requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    export_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    export_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RoleAssignment(Base):
    __tablename__ = "role_assignments"
    __table_args__ = (UniqueConstraint("user_id", "study_id", "role", name="uq_user_study_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    study_id: Mapped[int | None] = mapped_column(ForeignKey("studies.id"), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(50), index=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(foreign_keys=[user_id], back_populates="roles")
    granted_by_user: Mapped[User | None] = relationship(foreign_keys=[granted_by])


class Sample(Base):
    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    replicate_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dilution_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_type: Mapped[str | None] = mapped_column(String(100), nullable=True, default="sample")
    organism: Mapped[str | None] = mapped_column(String(100), nullable=True)
    matrix: Mapped[str | None] = mapped_column(String(100), nullable=True)
    annotations_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Protocol(Base):
    __tablename__ = "protocols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    protocol_type: Mapped[str] = mapped_column(String(100))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Assay(Base):
    __tablename__ = "assays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(100), default="metabolomics")
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    metabolomics_assay: Mapped["MetabolomicsAssay | None"] = relationship(
        back_populates="assay",
        uselist=False,
        cascade="all, delete-orphan",
    )


class AssaySampleLink(Base):
    __tablename__ = "assay_sample_links"
    __table_args__ = (UniqueConstraint("assay_id", "sample_id", name="uq_assay_sample"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assay_id: Mapped[int] = mapped_column(ForeignKey("assays.id"), index=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), index=True)
    sample_role: Mapped[str] = mapped_column(String(50), default="input")
    annotations_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssayProtocolLink(Base):
    __tablename__ = "assay_protocol_links"
    __table_args__ = (UniqueConstraint("assay_id", "protocol_id", name="uq_assay_protocol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assay_id: Mapped[int] = mapped_column(ForeignKey("assays.id"), index=True)
    protocol_id: Mapped[int] = mapped_column(ForeignKey("protocols.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MetabolomicsAssay(Base):
    __tablename__ = "metabolomics_assays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assay_id: Mapped[int] = mapped_column(ForeignKey("assays.id"), unique=True, index=True)
    assay_type: Mapped[str] = mapped_column(String(50), default="LC_MS")
    assay_technique: Mapped[str | None] = mapped_column(String(255), nullable=True)
    measurement_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    analysis_scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    analyte_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instrument_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ms_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    instrument_vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acquisition_software: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    assay: Mapped[Assay] = relationship(back_populates="metabolomics_assay")
    lcms_details: Mapped["LCMSAssayDetails | None"] = relationship(
        back_populates="metabolomics_assay",
        uselist=False,
        cascade="all, delete-orphan",
    )
    gcms_details: Mapped["GCMSAssayDetails | None"] = relationship(
        back_populates="metabolomics_assay",
        uselist=False,
        cascade="all, delete-orphan",
    )
    nmr_details: Mapped["NMRAssayDetails | None"] = relationship(
        back_populates="metabolomics_assay",
        uselist=False,
        cascade="all, delete-orphan",
    )


class LCMSAssayDetails(Base):
    __tablename__ = "lcms_assay_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metabolomics_assay_id: Mapped[int] = mapped_column(ForeignKey("metabolomics_assays.id"), unique=True, index=True)
    polarity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    column_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    column_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    column_guard_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chromatography_instrument: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scan_mz_range: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ion_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mass_analyzer: Mapped[str | None] = mapped_column(String(255), nullable=True)

    metabolomics_assay: Mapped[MetabolomicsAssay] = relationship(back_populates="lcms_details")


class GCMSAssayDetails(Base):
    __tablename__ = "gcms_assay_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metabolomics_assay_id: Mapped[int] = mapped_column(ForeignKey("metabolomics_assays.id"), unique=True, index=True)
    derivatization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chromatography_instrument: Mapped[str | None] = mapped_column(String(255), nullable=True)
    column_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    column_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    column_guard_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scan_mz_range: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ion_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mass_analyzer: Mapped[str | None] = mapped_column(String(255), nullable=True)

    metabolomics_assay: Mapped[MetabolomicsAssay] = relationship(back_populates="gcms_details")


class NMRAssayDetails(Base):
    __tablename__ = "nmr_assay_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metabolomics_assay_id: Mapped[int] = mapped_column(ForeignKey("metabolomics_assays.id"), unique=True, index=True)
    nmr_tube_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    solvent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sample_ph: Mapped[str | None] = mapped_column(String(100), nullable=True)
    temperature: Mapped[str | None] = mapped_column(String(100), nullable=True)
    nmr_probe: Mapped[str | None] = mapped_column(String(255), nullable=True)
    number_of_transients: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pulse_sequence_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    magnetic_field_strength: Mapped[str | None] = mapped_column(String(100), nullable=True)

    metabolomics_assay: Mapped[MetabolomicsAssay] = relationship(back_populates="nmr_details")


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    assay_id: Mapped[int] = mapped_column(ForeignKey("assays.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    dataset_kind: Mapped[str] = mapped_column(String(50), index=True)
    dataset_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default="registered", index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatasetSampleLink(Base):
    __tablename__ = "dataset_sample_links"
    __table_args__ = (UniqueConstraint("dataset_id", "sample_id", name="uq_dataset_sample"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), index=True)
    sample_role: Mapped[str] = mapped_column(String(50), default="input")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatasetFile(Base):
    __tablename__ = "dataset_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    sample_id: Mapped[int | None] = mapped_column(ForeignKey("samples.id"), nullable=True, index=True)
    acquisition_id: Mapped[int | None] = mapped_column(ForeignKey("acquisitions.id"), nullable=True, index=True)
    acquisition: Mapped["Acquisition | None"] = relationship(lazy="joined")
    file_role: Mapped[str] = mapped_column(String(100))
    original_filename: Mapped[str] = mapped_column(String(512))
    storage_uri: Mapped[str] = mapped_column(String(1024))
    source_scheme: Mapped[str] = mapped_column(String(50), default="file", index=True)
    ingest_mode: Mapped[str] = mapped_column(String(50), default="uploaded_file", index=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="registered", index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def acquisition_annotations_json(self) -> dict:
        return self.acquisition.annotations_json if self.acquisition else {}


class Acquisition(Base):
    """One instrument measurement of a study sample within an assay."""
    __tablename__ = "acquisitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assay_id: Mapped[int] = mapped_column(ForeignKey("assays.id"), index=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), index=True)
    annotations_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MetadataSubmission(Base):
    __tablename__ = "metadata_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    assay_id: Mapped[int] = mapped_column(ForeignKey("assays.id"), index=True)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    raw_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    normalized_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_status: Mapped[str] = mapped_column(String(50), default="valid")
    validation_messages_json: Mapped[list] = mapped_column(JSON, default=list)
    schema_version: Mapped[str] = mapped_column(String(50), default="metabolens-v1")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(50))
    container_profile: Mapped[str] = mapped_column(String(100), default="docker")
    nextflow_entrypoint: Mapped[str] = mapped_column(String(255))
    parameter_schema_json: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_backend: Mapped[str] = mapped_column(String(50), default="nextflow")
    execution_profile: Mapped[str] = mapped_column(String(100), default="local_docker")
    pipeline_root: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    default_nextflow_profile: Mapped[str] = mapped_column(String(100), default="docker")
    static_input_config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowJob(Base):
    __tablename__ = "workflow_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    assay_id: Mapped[int] = mapped_column(ForeignKey("assays.id"), index=True)
    workflow_definition_id: Mapped[int] = mapped_column(ForeignKey("workflow_definitions.id"), index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(50), default="queued", index=True)
    input_manifest_json: Mapped[dict] = mapped_column(JSON, default=dict)
    parameter_values_json: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_backend: Mapped[str] = mapped_column(String(50), default="nextflow")
    execution_profile: Mapped[str] = mapped_column(String(100), default="local_docker")
    run_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    run_root: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    results_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    logs_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    artifact_manifest_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    external_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowFeatureReview(Base):
    __tablename__ = "workflow_feature_reviews"
    __table_args__ = (UniqueConstraint("workflow_job_id", "preview_file_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_job_id: Mapped[int] = mapped_column(ForeignKey("workflow_jobs.id"), index=True)
    preview_file_id: Mapped[int] = mapped_column(ForeignKey("dataset_files.id"))
    preview_checksums_json: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    overrides_json: Mapped[dict] = mapped_column(JSON, default=dict)
    frozen_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frozen_file_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_files.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowRunArtifact(Base):
    __tablename__ = "workflow_run_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_job_id: Mapped[int] = mapped_column(ForeignKey("workflow_jobs.id"), index=True)
    dataset_file_id: Mapped[int] = mapped_column(ForeignKey("dataset_files.id"), index=True)
    artifact_role: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResultSet(Base):
    __tablename__ = "result_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    assay_id: Mapped[int | None] = mapped_column(ForeignKey("assays.id"), nullable=True, index=True)
    workflow_job_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_jobs.id"), nullable=True)
    result_type: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(50), default="v1")
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str] = mapped_column(String(100), index=True)
    target_id: Mapped[str] = mapped_column(String(100), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ProvenanceEvent(Base):
    __tablename__ = "provenance_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id"), index=True)
    assay_id: Mapped[int | None] = mapped_column(ForeignKey("assays.id"), nullable=True, index=True)
    workflow_job_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_jobs.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    source_ref_json: Mapped[dict] = mapped_column(JSON, default=dict)
    output_ref_json: Mapped[dict] = mapped_column(JSON, default=dict)
    software_versions_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
