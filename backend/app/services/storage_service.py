import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.config import Settings
from app.core.acquisition_files import is_acquisition_file
from app.core.errors import bad_request


@dataclass
class StoredUpload:
    original_filename: str
    storage_uri: str
    source_reference: str
    checksum_sha256: str
    file_size_bytes: int
    media_type: str


class UploadStorage:
    def build_registered_uri(self, experiment_id: int, original_filename: str) -> str:
        raise NotImplementedError

    def store_thermo_raw(self, experiment_id: int, upload: UploadFile) -> StoredUpload:
        raise NotImplementedError


class LocalUploadStorage(UploadStorage):
    def __init__(self, upload_storage_dir: str):
        self.upload_storage_dir = upload_storage_dir

    def build_registered_uri(self, experiment_id: int, original_filename: str) -> str:
        safe_name = Path(original_filename).name
        planned_path = (Path(self.upload_storage_dir) / f"experiment_{experiment_id}" / safe_name).resolve()
        return f"file://{planned_path}"

    def store_thermo_raw(self, experiment_id: int, upload: UploadFile) -> StoredUpload:
        filename = Path(upload.filename or "").name
        if not filename:
            raise bad_request("Missing file name")
        if not is_acquisition_file(filename):
            raise bad_request("Only Thermo RAW (*.raw) or mzML (*.mzML) files are accepted")

        exp_dir = Path(self.upload_storage_dir) / f"experiment_{experiment_id}"
        exp_dir.mkdir(parents=True, exist_ok=True)
        stored_filename = f"{uuid4().hex}_{filename}"
        stored_path = exp_dir / stored_filename

        sha256 = hashlib.sha256()
        total_size = 0
        with stored_path.open("wb") as out:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                sha256.update(chunk)
                out.write(chunk)
        upload.file.close()

        media_type = upload.content_type or "application/octet-stream"
        absolute_path = str(stored_path.resolve())
        return StoredUpload(
            original_filename=filename,
            storage_uri=f"file://{absolute_path}",
            source_reference=absolute_path,
            checksum_sha256=sha256.hexdigest(),
            file_size_bytes=total_size,
            media_type=media_type,
        )


class S3UploadStorage(UploadStorage):
    def __init__(self, bucket: str, prefix: str):
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def build_registered_uri(self, experiment_id: int, original_filename: str) -> str:
        safe_name = Path(original_filename).name
        object_key_prefix = f"{self.prefix}/" if self.prefix else ""
        return f"s3://{self.bucket}/{object_key_prefix}experiment_{experiment_id}/{safe_name}"

    def store_thermo_raw(self, experiment_id: int, upload: UploadFile) -> StoredUpload:
        raise NotImplementedError(
            "UPLOAD_STORAGE_BACKEND='s3' is configured but S3 transport is not implemented yet. "
            "Implement S3UploadStorage.store_thermo_raw with your object-storage client."
        )


def get_upload_storage(settings: Settings) -> UploadStorage:
    backend = settings.upload_storage_backend.strip().lower()
    if backend == "local":
        return LocalUploadStorage(settings.upload_storage_dir)
    if backend == "s3":
        return S3UploadStorage(settings.upload_storage_s3_bucket, settings.upload_storage_s3_prefix)
    raise bad_request(f"Unsupported upload storage backend: '{settings.upload_storage_backend}'")
