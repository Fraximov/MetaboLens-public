from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel
from app.schemas.assay import MetabolomicsAssayOut


class MetadataSubmit(BaseModel):
    payload: dict


class MetadataOut(ORMModel):
    id: int
    study_id: int
    assay_id: int
    submitted_by: int
    raw_payload_json: dict
    normalized_payload_json: dict
    validation_status: str
    validation_messages_json: list
    schema_version: str
    submitted_at: datetime
    metabolomics_assay: MetabolomicsAssayOut | None = None


class MetadataProfileOut(BaseModel):
    id: str
    label: str
    description: str
    metabolomics_assay_type: str
    required_field_paths: list[str]
    default_payload_json: dict
