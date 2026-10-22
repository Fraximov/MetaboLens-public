from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.sample_metadata import DEFAULT_SAMPLE_TYPE, normalize_sample_annotations, normalize_sample_type
from app.schemas.common import ORMModel


class StudyCreate(BaseModel):
    title: str
    description: str | None = None
    project_id: int | None = None


class StudyPatch(BaseModel):
    title: str | None = None
    description: str | None = None


class StudyOut(ORMModel):
    id: int
    public_or_internal_identifier: str
    project_id: int | None
    title: str
    description: str | None
    status: str
    created_by: int
    created_at: datetime
    updated_at: datetime
    default_assay_id: int | None = None


class StudyListOut(StudyOut):
    sample_count: int = 0
    assay_count: int = 0


class SampleMetadataValidators(BaseModel):
    @field_validator("sample_type", mode="before", check_fields=False)
    @classmethod
    def normalize_sample_type_value(cls, value):
        return normalize_sample_type(value)

    @field_validator("annotations_json", mode="before", check_fields=False)
    @classmethod
    def normalize_annotations_value(cls, value):
        return normalize_sample_annotations(value)


class SampleCreate(SampleMetadataValidators):
    name: str
    description: str | None = None
    replicate_number: int | None = Field(default=None, ge=1)
    dilution_factor: float | None = Field(default=None, gt=0)
    sample_type: str = DEFAULT_SAMPLE_TYPE
    organism: str | None = None
    matrix: str | None = None
    annotations_json: dict = Field(default_factory=dict)


class SamplePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    replicate_number: int | None = Field(default=None, ge=1)
    dilution_factor: float | None = Field(default=None, gt=0)
    sample_type: str | None = None
    organism: str | None = None
    matrix: str | None = None
    annotations_json: dict | None = None

    @field_validator("sample_type", mode="before")
    @classmethod
    def normalize_optional_sample_type_value(cls, value):
        if value is None:
            return None
        return normalize_sample_type(value)

    @field_validator("annotations_json", mode="before")
    @classmethod
    def normalize_optional_annotations_value(cls, value):
        if value is None:
            return None
        return normalize_sample_annotations(value)


class SampleBulkCreateItem(SampleMetadataValidators):
    name: str
    description: str
    replicate_number: int | None = Field(default=None, ge=1)
    dilution_factor: float | None = Field(default=None, gt=0)
    sample_type: str = DEFAULT_SAMPLE_TYPE
    organism: str | None = None
    matrix: str | None = None
    annotations_json: dict = Field(default_factory=dict)


class SamplePathReference(BaseModel):
    source_path: str


class SampleBulkPathCreateItem(BaseModel):
    sample: SampleBulkCreateItem | None = None
    existing_sample_id: int | None = Field(default=None, ge=1)
    annotations_json: dict = Field(default_factory=dict)
    raw_file: SamplePathReference

    @field_validator("annotations_json", mode="before")
    @classmethod
    def normalize_row_annotations_value(cls, value):
        return normalize_sample_annotations(value)


class RawFolderScanRequest(BaseModel):
    folder_path: str


class RawFolderImportRequest(BaseModel):
    folder_path: str


class RawFolderImportResponse(BaseModel):
    folder_path: str
    scanned_count: int
    imported_count: int
    skipped_existing_count: int
    imported_sample_ids: list[int]
    imported_file_ids: list[int]
    skipped_existing_paths: list[str]


class RawFolderScanItem(BaseModel):
    already_registered: bool = False
    source_path: str
    original_filename: str
    sample_code: str


class RawFolderBrowseRequest(BaseModel):
    folder_path: str | None = None


class RawFolderBrowseItem(BaseModel):
    name: str
    path: str
    raw_file_count: int


class RawFolderBrowseResponse(BaseModel):
    current_path: str | None = None
    parent_path: str | None = None
    entries: list[RawFolderBrowseItem]


class SampleOut(ORMModel):
    id: int
    study_id: int
    name: str
    description: str | None
    replicate_number: int | None
    dilution_factor: float | None
    sample_type: str | None
    organism: str | None
    matrix: str | None
    annotations_json: dict
    created_at: datetime


class SampleDeleteOut(BaseModel):
    deleted_sample_id: int
    deleted_raw_file_count: int
    cleared_file_sample_count: int


class StudySummaryOut(BaseModel):
    study: StudyOut
    sample_count: int
    dataset_file_count: int
    dataset_status_counts: dict[str, int]
    latest_metadata_status: str | None = None
    latest_metadata_submitted_at: datetime | None = None
    workflow_job_count: int
    workflow_status_counts: dict[str, int]


class StudyActivityEventOut(BaseModel):
    id: int
    created_at: datetime
    actor_user_id: int | None = None
    actor_email: str | None = None
    event_type: str
    target_type: str
    target_id: str
    payload_json: dict


class StudyStructureNodeOut(BaseModel):
    id: str
    parent_id: str | None = None
    label: str
    node_type: str
    value: int
    study_id: int
    assay_id: int | None = None
    dataset_id: int | None = None
    sample_id: int | None = None
    dataset_kind: str | None = None
    details: dict = Field(default_factory=dict)


class StudyStructureOut(BaseModel):
    study_id: int
    nodes: list[StudyStructureNodeOut]
