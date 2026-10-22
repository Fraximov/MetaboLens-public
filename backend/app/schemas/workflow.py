from datetime import datetime

from pydantic import BaseModel, Field, StrictStr, field_validator

from app.schemas.common import ORMModel


class WorkflowDefinitionOut(ORMModel):
    id: int
    name: str
    version: str
    container_profile: str
    nextflow_entrypoint: str
    parameter_schema_json: dict
    execution_backend: str
    execution_profile: str
    pipeline_root: str | None
    default_nextflow_profile: str
    static_input_config_json: dict
    is_active: bool


class WorkflowLibraryOption(BaseModel):
    label: str
    value: str


class WorkflowLibraryOptionsOut(BaseModel):
    ms1_libraries: list[WorkflowLibraryOption]
    ms2_libraries: list[WorkflowLibraryOption]
    is_libraries: list[WorkflowLibraryOption] = Field(default_factory=list)
    msdial_templates: list[WorkflowLibraryOption]
    default_ms1_library_path: str | None = None
    default_ms2_library_path: str | None = None
    default_is_library_path: str | None = None
    default_msdial_config_path: str | None = None
    masscube_templates: list[WorkflowLibraryOption] = Field(default_factory=list)
    default_masscube_config_path: str | None = None


class WorkflowTemplateContentOut(BaseModel):
    path: str
    content: str
    checksum_sha256: str


class WorkflowLaunch(BaseModel):
    parameters: dict = Field(default_factory=dict)
    file_ids: list[int] | None = None
    msdial_template_text: str | None = None
    masscube_template_text: str | None = None
    review_stages: list[str] | None = None
    metabolensr_profile: str | None = Field(default=None, strict=True, min_length=1)
    metabolensr_enabled_rules: dict[str, list[StrictStr]] | None = None
    metabolensr_expected_image: str | None = Field(default=None, strict=True,
                                                  pattern=r'^[^\s]+@sha256:[0-9a-f]{64}$')

    @field_validator('metabolensr_enabled_rules', mode='before')
    @classmethod
    def rule_selection_is_an_object(cls, value):
        if not isinstance(value, dict):
            raise ValueError('metabolensr_enabled_rules must be an object; omit it to use the preset')
        return value


class WorkflowFeatureReviewPatch(BaseModel):
    preview_file_id: int
    expected_revision: int = Field(ge=0)
    changes: list[dict] = Field(min_length=1, max_length=200)


class WorkflowContinueRequest(BaseModel):
    preview_file_id: int | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    operator_note: str | None = None
    apply_rt_correction: bool | None = None
    rt_correction_target_ids: list[str] | None = None


class WorkflowJobOut(ORMModel):
    id: int
    study_id: int
    assay_id: int
    workflow_definition_id: int
    requested_by: int
    status: str
    input_manifest_json: dict
    parameter_values_json: dict
    execution_backend: str
    execution_profile: str
    run_name: str | None
    run_root: str | None
    results_path: str | None
    logs_path: str | None
    artifact_manifest_path: str | None
    status_payload_json: dict
    external_run_id: str | None
    submitted_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    deleted_at: datetime | None
    deleted_by: int | None
    purged_at: datetime | None
    created_at: datetime


class WorkflowOutputFileOut(BaseModel):
    workflow_job_id: int
    workflow_definition_id: int
    execution_profile: str | None = None
    artifact_role: str
    file_id: int
    dataset_id: int
    dataset_name: str | None = None
    dataset_kind: str | None = None
    dataset_type: str | None = None
    dataset_sample_ids: list[int] = []
    dataset_sample_names: list[str] = []
    file_sample_id: int | None = None
    file_sample_name: str | None = None
    file_role: str
    original_filename: str
    status: str
    storage_uri: str
    file_size_bytes: int | None = None
    checksum_sha256: str | None = None
    media_type: str | None = None
    created_at: datetime


class WorkflowEicFeatureOut(BaseModel):
    feature_index: int
    alignment_id: int | None = None
    metabolite_name: str | None = None
    adduct_type: str | None = None
    average_rt: float | None = None
    average_mz: float | None = None
    annotated: bool = False
    has_ms2: bool = False
    msms_matched: bool = False
    low_score: bool = False
    has_reference: bool = False
    label: str


class WorkflowEicSummaryOut(BaseModel):
    eic_file_id: int
    eic_filename: str
    alignment_file_id: int
    alignment_filename: str
    version: str
    feature_count: int


class WorkflowEicPointOut(BaseModel):
    x: float
    intensity: float


class WorkflowEicPeakOut(BaseModel):
    file_id: int
    sample_name: str
    point_count: int
    returned_point_count: int
    top: float
    left: float
    right: float
    points: list[WorkflowEicPointOut]


class WorkflowEicSpotOut(BaseModel):
    version: str
    feature_count: int
    center: dict
    trace_count: int
    peaks: list[WorkflowEicPeakOut]


class WorkflowEicMs2PeakOut(BaseModel):
    mz: float
    intensity: float
    matched: bool = False


class WorkflowEicMs2ReferenceOut(BaseModel):
    name: str | None = None
    inchikey: str | None = None
    precursor_mz: float | None = None
    library_filename: str | None = None
    peaks: list[WorkflowEicMs2PeakOut] = []


class WorkflowEicMs2Out(BaseModel):
    available: bool = False
    match_tolerance_da: float | None = None
    experimental: list[WorkflowEicMs2PeakOut] = []
    reference: WorkflowEicMs2ReferenceOut | None = None
    scores: dict = {}
    reference_rt: float | None = None
    reference_mz: float | None = None
    library_filename: str | None = None


class WorkflowEicTraceOut(BaseModel):
    eic_file_id: int
    eic_filename: str
    alignment_file_id: int
    alignment_filename: str
    reader: str
    feature: WorkflowEicFeatureOut
    spot: WorkflowEicSpotOut
    ms2: WorkflowEicMs2Out | None = None


class WorkflowFeatureMapOut(BaseModel):
    feature_id: str
    source_feature_id: str | None = None
    metabolite_name: str
    average_mz: float | None = None
    average_rt: float | None = None
    adduct_type: str | None = None
    has_ms2: bool | None = None
    annotated: bool
    msms_matched: bool | None = None
    has_reference: bool | None = None
    low_score: bool | None = None
    label: str


class WorkflowFeatureOut(WorkflowFeatureMapOut):
    decisions: dict[str, str | None] = Field(default_factory=dict)


class WorkflowFeaturePageOut(BaseModel):
    items: list[WorkflowFeatureOut]
    total: int
    page: int
    page_size: int


class WorkflowFeaturePeakOut(WorkflowEicPeakOut):
    top: float | None = None
    left: float | None = None
    right: float | None = None
    native_feature_id: str | None = None
    detected: bool | None = None
    gap_filled: bool | None = None
    acquisition_id: str
    file_asset_id: str | None = None
    source_column: str


class WorkflowFeatureSpotOut(WorkflowEicSpotOut):
    peaks: list[WorkflowFeaturePeakOut]


class WorkflowFeatureMs2Out(WorkflowEicMs2Out):
    provider: str


class WorkflowFeatureTraceOut(BaseModel):
    preview_file_id: int
    feature: WorkflowFeatureOut
    reader: str | None = None
    spot: WorkflowFeatureSpotOut | None = None
    ms2: WorkflowFeatureMs2Out | None = None
    evidence_message: str | None = None
