from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class FileRegister(BaseModel):
    file_kind: str
    original_filename: str
    sample_id: int | None = None


class FileFinalize(BaseModel):
    checksum_sha256: str
    file_size_bytes: int
    media_type: str | None = None
    status: str = "validated"


class FileAssetOut(ORMModel):
    id: int
    experiment_id: int
    sample_id: int | None
    file_kind: str
    original_filename: str
    storage_uri: str
    checksum_sha256: str | None
    file_size_bytes: int | None
    media_type: str | None
    status: str
    created_by: int
    created_at: datetime
