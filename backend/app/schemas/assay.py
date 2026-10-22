from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel


LEGACY_ASSAY_TYPE_MAP = {
    "metabolomics": ("metabolomics", "LC_MS"),
    "metabolomics_lcms": ("metabolomics", "LC_MS"),
    "metabolomics_gcms": ("metabolomics", "GC_MS"),
    "metabolomics_nmr": ("metabolomics", "NMR"),
}


def normalize_platform(value: str | None) -> str:
    raw = (value or "metabolomics").strip().lower()
    return raw or "metabolomics"


def normalize_metabolomics_assay_type(value: str | None) -> str:
    raw = (value or "LC_MS").strip().upper().replace("-", "_")
    if raw in {"LCMS", "LC_MS"}:
        return "LC_MS"
    if raw in {"GCMS", "GC_MS"}:
        return "GC_MS"
    if raw == "NMR":
        return "NMR"
    raise ValueError(f"Unsupported metabolomics assay type '{value}'")


class AssayCreate(BaseModel):
    name: str | None = None
    platform: str = "metabolomics"
    metabolomics_assay_type: str = "LC_MS"
    assay_type: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def normalize_values(self):
        legacy_value = (self.assay_type or "").strip().lower()
        if legacy_value in LEGACY_ASSAY_TYPE_MAP:
            legacy_platform, legacy_metabolomics_type = LEGACY_ASSAY_TYPE_MAP[legacy_value]
            if not self.platform or self.platform == "metabolomics":
                self.platform = legacy_platform
            if not self.metabolomics_assay_type or self.metabolomics_assay_type == "LC_MS":
                self.metabolomics_assay_type = legacy_metabolomics_type
        self.platform = normalize_platform(self.platform)
        self.metabolomics_assay_type = normalize_metabolomics_assay_type(self.metabolomics_assay_type)
        return self


class LCMSAssayDetailsOut(ORMModel):
    id: int
    metabolomics_assay_id: int
    polarity: str | None = None
    column_model: str | None = None
    column_type: str | None = None
    column_guard_type: str | None = None
    chromatography_instrument: str | None = None
    scan_mz_range: str | None = None
    ion_source: str | None = None
    mass_analyzer: str | None = None


class GCMSAssayDetailsOut(ORMModel):
    id: int
    metabolomics_assay_id: int
    derivatization: str | None = None
    chromatography_instrument: str | None = None
    column_model: str | None = None
    column_type: str | None = None
    column_guard_type: str | None = None
    scan_mz_range: str | None = None
    ion_source: str | None = None
    mass_analyzer: str | None = None


class NMRAssayDetailsOut(ORMModel):
    id: int
    metabolomics_assay_id: int
    nmr_tube_type: str | None = None
    solvent: str | None = None
    sample_ph: str | None = None
    temperature: str | None = None
    nmr_probe: str | None = None
    number_of_transients: str | None = None
    pulse_sequence_name: str | None = None
    magnetic_field_strength: str | None = None


class MetabolomicsAssayOut(ORMModel):
    id: int
    assay_id: int
    assay_type: str
    assay_technique: str | None = None
    measurement_type: str | None = None
    analysis_scope: str | None = None
    analyte_domain: str | None = None
    instrument_model: str | None = None
    ms_type: str | None = None
    instrument_vendor: str | None = None
    acquisition_software: str | None = None
    created_at: datetime
    updated_at: datetime
    lcms_details: LCMSAssayDetailsOut | None = None
    gcms_details: GCMSAssayDetailsOut | None = None
    nmr_details: NMRAssayDetailsOut | None = None


class AssayOut(ORMModel):
    id: int
    study_id: int
    name: str
    platform: str
    status: str
    is_default: bool
    created_by: int
    created_at: datetime
    updated_at: datetime
    # When the assay's newest live workflow job was created; lets a client pick
    # the assay people are working on. Set by the study assay list only.
    last_workflow_job_at: datetime | None = None
    metabolomics_assay: MetabolomicsAssayOut | None = None
