from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.sample_context import normalize_acquisition_annotations
from app.schemas.common import ORMModel


class AcquisitionPatch(BaseModel):
    annotations_json: dict = Field(default_factory=dict)

    @field_validator("annotations_json")
    @classmethod
    def validate_annotations(cls, value):
        return normalize_acquisition_annotations(value)


class AcquisitionOut(ORMModel):
    id: int
    assay_id: int
    sample_id: int
    sample_name: str
    file_ids: list[int]
    filenames: list[str]
    annotations_json: dict
    metadata_review_required: bool
    created_at: datetime
