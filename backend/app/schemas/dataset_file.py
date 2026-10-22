from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class DatasetFileRegister(BaseModel):
    file_role: str
    original_filename: str
    sample_id: int | None = None
    storage_uri: str | None = None
    source_scheme: str | None = None
    ingest_mode: str | None = None


class DatasetFileFinalize(BaseModel):
    checksum_sha256: str
    file_size_bytes: int
    media_type: str | None = None
    status: str = "validated"


class DatasetFileOut(ORMModel):
    id: int
    dataset_id: int
    dataset_name: str | None = None
    dataset_kind: str | None = None
    dataset_type: str | None = None
    dataset_sample_ids: list[int] = []
    dataset_sample_names: list[str] = []
    sample_id: int | None
    acquisition_id: int | None = None
    acquisition_annotations_json: dict = {}
    file_sample_name: str | None = None
    file_role: str
    original_filename: str
    storage_uri: str
    source_scheme: str
    ingest_mode: str
    checksum_sha256: str | None
    file_size_bytes: int | None
    media_type: str | None
    status: str
    created_by: int
    created_at: datetime
