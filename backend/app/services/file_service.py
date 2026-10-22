from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.acquisition_files import ACQUISITION_SUFFIX_LABEL, is_acquisition_file
from app.core.errors import bad_request, not_found
from app.core.engine_contract import sha256_file
from app.core.sample_metadata import normalize_sample_annotations, normalize_sample_type
from app.core.states import ACCEPTED_INPUT_FILE_STATES
from app.core.sample_context import annotations_dict, sample_defaults_from_row_annotations, split_acquisition_annotations
from app.models.models import Assay, AssaySampleLink, Dataset, DatasetFile, DatasetSampleLink, Sample
from app.schemas.dataset_file import DatasetFileFinalize, DatasetFileRegister
from app.schemas.study import SampleBulkCreateItem, SampleBulkPathCreateItem
from app.services.audit_service import AuditService
from app.services.acquisition_service import AcquisitionService
from app.services.study_service import StudyService
from app.services.storage_service import get_upload_storage


class FileService:
    def __init__(self, db: Session):
        self.db = db
        self.studies = StudyService(db)
        self.audit = AuditService(db)
        self.settings = get_settings()
        self.storage = get_upload_storage(self.settings)

    @staticmethod
    def _path_within_root(path: Path, root: Path) -> bool:
        return path == root or root in path.parents

    def _browse_roots(self, *, include_uploads: bool = False) -> list[Path]:
        configured = [item.strip() for item in self.settings.raw_path_browse_roots.split(",") if item.strip()]
        if include_uploads and self.settings.upload_storage_backend.strip().lower() == "local":
            configured.append(self.settings.upload_storage_dir)
        resolved_roots: list[Path] = []
        for root in configured:
            candidate = Path(root).expanduser()
            if not candidate.is_absolute():
                continue
            try:
                resolved = candidate.resolve()
            except (OSError, RuntimeError):
                continue
            if resolved.exists() and resolved.is_dir():
                resolved_roots.append(resolved)
        return resolved_roots

    def _allowed_local_path(self, path: Path, *, include_uploads: bool = False) -> Path:
        if not path.is_absolute():
            raise bad_request("Local source path must be absolute")
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise bad_request("Local source path cannot be resolved") from exc
        if not any(self._path_within_root(resolved, root) for root in self._browse_roots(include_uploads=include_uploads)):
            raise bad_request("Local source path is outside allowed roots")
        return resolved

    def _ensure_raw_dataset(self, user_id: int, study_id: int, assay_id: int | None = None) -> Dataset:
        assay = (
            self.studies.get_default_assay(study_id)
            if assay_id is None
            else self.db.query(Assay).filter(Assay.id == assay_id, Assay.study_id == study_id).first()
        )
        if assay is None:
            raise not_found("Assay not found")
        dataset = (
            self.db.query(Dataset)
            .filter(Dataset.study_id == study_id, Dataset.assay_id == assay.id, Dataset.dataset_kind == "raw")
            .first()
        )
        if dataset is not None:
            return dataset
        dataset = Dataset(
            study_id=study_id,
            assay_id=assay.id,
            name=f"{assay.name}-raw",
            dataset_kind="raw",
            dataset_type="thermo_raw_acquisition",
            status="registered",
            created_by=user_id,
        )
        self.db.add(dataset)
        self.db.flush()
        return dataset

    def _ensure_dataset_sample_link(self, dataset_id: int, sample_id: int, sample_role: str = "input") -> None:
        existing = (
            self.db.query(DatasetSampleLink)
            .filter(DatasetSampleLink.dataset_id == dataset_id, DatasetSampleLink.sample_id == sample_id)
            .first()
        )
        if existing is not None:
            return
        self.db.add(DatasetSampleLink(dataset_id=dataset_id, sample_id=sample_id, sample_role=sample_role))

    def _ensure_assay_sample_link(
        self,
        assay_id: int,
        sample_id: int,
        sample_role: str = "input",
        annotations_json: dict | None = None,
    ) -> AssaySampleLink:
        link_annotations = annotations_dict(annotations_json)
        existing = (
            self.db.query(AssaySampleLink)
            .filter(AssaySampleLink.assay_id == assay_id, AssaySampleLink.sample_id == sample_id)
            .first()
        )
        if existing is not None:
            if link_annotations:
                existing_annotations = annotations_dict(existing.annotations_json)
                existing.annotations_json = {**existing_annotations, **link_annotations}
            return existing
        link = AssaySampleLink(
            assay_id=assay_id,
            sample_id=sample_id,
            sample_role=sample_role,
            annotations_json=link_annotations,
        )
        self.db.add(link)
        return link

    def _normalize_sample_metadata(self, sample_type: str | None, annotations_json: dict | None) -> tuple[str, dict]:
        try:
            return normalize_sample_type(sample_type), normalize_sample_annotations(annotations_json)
        except ValueError as exc:
            raise bad_request(str(exc)) from exc

    def _dataset_sample_maps(self, dataset_ids: list[int]) -> tuple[dict[int, list[int]], dict[int, list[str]]]:
        if not dataset_ids:
            return {}, {}
        rows = (
            self.db.query(DatasetSampleLink.dataset_id, Sample.id, Sample.name)
            .join(Sample, Sample.id == DatasetSampleLink.sample_id)
            .filter(DatasetSampleLink.dataset_id.in_(dataset_ids))
            .order_by(DatasetSampleLink.dataset_id.asc(), Sample.id.asc())
            .all()
        )
        ids_by_dataset: dict[int, list[int]] = {}
        names_by_dataset: dict[int, list[str]] = {}
        for dataset_id, sample_id, sample_name in rows:
            ids_by_dataset.setdefault(int(dataset_id), []).append(int(sample_id))
            names_by_dataset.setdefault(int(dataset_id), []).append(sample_name)
        return ids_by_dataset, names_by_dataset

    @staticmethod
    def _serialize_dataset_file(
        dataset_file: DatasetFile,
        dataset: Dataset,
        *,
        dataset_sample_ids: list[int],
        dataset_sample_names: list[str],
        file_sample_name: str | None,
    ) -> dict:
        return {
            "id": dataset_file.id,
            "dataset_id": dataset_file.dataset_id,
            "dataset_name": dataset.name,
            "dataset_kind": dataset.dataset_kind,
            "dataset_type": dataset.dataset_type,
            "dataset_sample_ids": dataset_sample_ids,
            "dataset_sample_names": dataset_sample_names,
            "sample_id": dataset_file.sample_id,
            "acquisition_id": dataset_file.acquisition_id,
            "acquisition_annotations_json": dataset_file.acquisition.annotations_json if dataset_file.acquisition else {},
            "file_sample_name": file_sample_name,
            "file_role": dataset_file.file_role,
            "original_filename": dataset_file.original_filename,
            "storage_uri": dataset_file.storage_uri,
            "source_scheme": dataset_file.source_scheme,
            "ingest_mode": dataset_file.ingest_mode,
            "checksum_sha256": dataset_file.checksum_sha256,
            "file_size_bytes": dataset_file.file_size_bytes,
            "media_type": dataset_file.media_type,
            "status": dataset_file.status,
            "created_by": dataset_file.created_by,
            "created_at": dataset_file.created_at,
        }

    def register(self, user_id: int, study_id: int, payload: DatasetFileRegister) -> DatasetFile:
        study = self.studies.get(user_id, study_id)
        self.studies.perms.require_edit_study(user_id, study.id, study.status)
        if payload.sample_id is not None and self.db.query(Sample.id).filter_by(id=payload.sample_id, study_id=study_id).first() is None:
            raise bad_request("Sample not found for this study")
        dataset = self._ensure_raw_dataset(user_id, study_id)
        storage_uri = payload.storage_uri or self.storage.build_registered_uri(study_id, payload.original_filename)
        parsed = urlparse(storage_uri)
        if parsed.scheme in {"", "file"}:
            if parsed.netloc not in {"", "localhost"}:
                raise bad_request("Remote file authorities are not supported")
            path = Path(unquote(parsed.path) if parsed.scheme else storage_uri)
            storage_uri = f"file://{self._allowed_local_path(path, include_uploads=True)}"
        if payload.file_role == "thermo_raw" and storage_uri in self._registered_raw_source_uris(study_id):
            raise bad_request("RAW source path has already been registered for this study")
        acquisition = AcquisitionService(self.db).create(dataset.assay_id, payload.sample_id, {}) if payload.sample_id is not None and payload.file_role == "thermo_raw" else None
        dataset_file = DatasetFile(
            dataset_id=dataset.id,
            sample_id=payload.sample_id,
            acquisition_id=acquisition.id if acquisition else None,
            file_role=payload.file_role,
            original_filename=payload.original_filename,
            storage_uri=storage_uri,
            source_scheme=payload.source_scheme or "file",
            ingest_mode=payload.ingest_mode or "uploaded_file",
            status="registered",
            created_by=user_id,
        )
        self.db.add(dataset_file)
        if payload.sample_id is not None:
            self._ensure_dataset_sample_link(dataset.id, payload.sample_id)
            self._ensure_assay_sample_link(dataset.assay_id, payload.sample_id)
        self.audit.log(user_id, "dataset_file.register", "dataset_file", "new", {"study_id": study_id, "name": payload.original_filename})
        self.db.commit()
        self.db.refresh(dataset_file)
        return dataset_file

    def finalize(self, user_id: int, dataset_file_id: int, payload: DatasetFileFinalize) -> DatasetFile:
        dataset_file = self.db.query(DatasetFile).filter(DatasetFile.id == dataset_file_id).first()
        if not dataset_file:
            raise not_found("Dataset file not found")
        dataset = self.db.query(Dataset).filter(Dataset.id == dataset_file.dataset_id).first()
        if dataset is None:
            raise not_found("Dataset not found")
        study = self.studies.get(user_id, dataset.study_id)
        self.studies.perms.require_edit_study(user_id, study.id, study.status)
        if payload.status not in {"uploaded", "validated", "failed"}:
            raise bad_request("Invalid file status")
        dataset_file.checksum_sha256 = payload.checksum_sha256
        dataset_file.file_size_bytes = payload.file_size_bytes
        dataset_file.media_type = payload.media_type
        dataset_file.status = payload.status
        self.audit.log(user_id, "dataset_file.finalize", "dataset_file", str(dataset_file.id), {"status": payload.status})
        self.db.commit()
        self.db.refresh(dataset_file)
        return dataset_file

    def list_for_study(self, user_id: int, study_id: int) -> list[dict]:
        self.studies.get(user_id, study_id)
        rows = (
            self.db.query(DatasetFile)
            .join(Dataset, Dataset.id == DatasetFile.dataset_id)
            .filter(Dataset.study_id == study_id)
            .order_by(DatasetFile.id.desc())
            .all()
        )
        if not rows:
            return []
        dataset_ids = list(dict.fromkeys(int(row.dataset_id) for row in rows))
        file_sample_ids = [int(row.sample_id) for row in rows if row.sample_id is not None]
        dataset_sample_ids_by_dataset, dataset_sample_names_by_dataset = self._dataset_sample_maps(dataset_ids)
        file_sample_name_by_id: dict[int, str] = {}
        if file_sample_ids:
            file_sample_rows = self.db.query(Sample.id, Sample.name).filter(Sample.id.in_(file_sample_ids)).all()
            file_sample_name_by_id = {int(sample_id): sample_name for sample_id, sample_name in file_sample_rows}
        dataset_by_id = {
            int(dataset.id): dataset
            for dataset in (
                self.db.query(Dataset)
                .filter(Dataset.id.in_(dataset_ids))
                .all()
            )
        }
        return [
            self._serialize_dataset_file(
                row,
                dataset_by_id[int(row.dataset_id)],
                dataset_sample_ids=dataset_sample_ids_by_dataset.get(int(row.dataset_id), []),
                dataset_sample_names=dataset_sample_names_by_dataset.get(int(row.dataset_id), []),
                file_sample_name=file_sample_name_by_id.get(int(row.sample_id)) if row.sample_id is not None else None,
            )
            for row in rows
        ]

    def list_for_assay(
        self,
        user_id: int,
        assay_id: int,
        *,
        workflow_inputs_only: bool = False,
    ) -> list[dict]:
        assay = self.studies.get_assay(user_id, assay_id)

        query = (
            self.db.query(DatasetFile)
            .join(Dataset, Dataset.id == DatasetFile.dataset_id)
            .filter(Dataset.assay_id == assay_id, Dataset.study_id == assay.study_id)
        )
        if workflow_inputs_only:
            query = query.join(Sample, Sample.id == DatasetFile.sample_id).filter(
                Dataset.dataset_kind == "raw",
                DatasetFile.file_role == "thermo_raw",
                DatasetFile.status.in_(ACCEPTED_INPUT_FILE_STATES),
                Sample.study_id == assay.study_id,
            )
        rows = query.order_by(DatasetFile.id.desc()).all()
        if not rows:
            return []
        dataset_ids = list(dict.fromkeys(int(row.dataset_id) for row in rows))
        file_sample_ids = [int(row.sample_id) for row in rows if row.sample_id is not None]
        dataset_sample_ids_by_dataset, dataset_sample_names_by_dataset = self._dataset_sample_maps(dataset_ids)
        file_sample_name_by_id: dict[int, str] = {}
        if file_sample_ids:
            file_sample_rows = self.db.query(Sample.id, Sample.name).filter(Sample.id.in_(file_sample_ids)).all()
            file_sample_name_by_id = {int(sample_id): sample_name for sample_id, sample_name in file_sample_rows}
        dataset_by_id = {int(dataset.id): dataset for dataset in self.db.query(Dataset).filter(Dataset.id.in_(dataset_ids)).all()}
        return [
            self._serialize_dataset_file(
                row,
                dataset_by_id[int(row.dataset_id)],
                dataset_sample_ids=dataset_sample_ids_by_dataset.get(int(row.dataset_id), []),
                dataset_sample_names=dataset_sample_names_by_dataset.get(int(row.dataset_id), []),
                file_sample_name=file_sample_name_by_id.get(int(row.sample_id)) if row.sample_id is not None else None,
            )
            for row in rows
        ]

    def _registered_raw_source_uris(self, study_id: int) -> set[str]:
        rows = (
            self.db.query(DatasetFile.storage_uri)
            .join(Dataset, Dataset.id == DatasetFile.dataset_id)
            .filter(Dataset.study_id == study_id, Dataset.dataset_kind == "raw")
            .all()
        )
        return {str(storage_uri) for (storage_uri,) in rows if storage_uri}

    def _normalize_local_source_path(self, source_path: str) -> tuple[str, str, int | None]:
        raw_value = (source_path or "").strip()
        if not raw_value:
            raise bad_request("RAW source path is required")

        parsed = urlparse(raw_value)
        if parsed.scheme and parsed.scheme.lower() != "file":
            raise bad_request(f"Unsupported RAW source scheme '{parsed.scheme}'. Only local file paths are supported for now")

        if parsed.scheme.lower() == "file":
            if parsed.netloc not in {"", "localhost"}:
                raise bad_request("Remote file authorities are not supported")
            local_path = Path(unquote(parsed.path))
        else:
            local_path = Path(raw_value)

        if not local_path.is_absolute():
            raise bad_request(f"RAW source path must be absolute: '{raw_value}'")
        local_path = self._allowed_local_path(local_path, include_uploads=True)
        if not is_acquisition_file(local_path.name):
            raise bad_request(f"Acquisition source path must end with {ACQUISITION_SUFFIX_LABEL}: '{raw_value}'")
        if not local_path.exists():
            raise bad_request(f"RAW source path does not exist: '{raw_value}'")
        if not local_path.is_file():
            raise bad_request(f"RAW source path is not a file: '{raw_value}'")

        resolved_path = local_path.resolve()
        if not resolved_path.exists():
            raise bad_request(f"RAW source path does not exist: '{raw_value}'")
        try:
            size_bytes = resolved_path.stat().st_size
            with resolved_path.open("rb"):
                pass
        except OSError as exc:
            raise bad_request(f"RAW source path is not readable: '{raw_value}'") from exc
        return f"file://{resolved_path}", resolved_path.name, size_bytes

    def _raw_folder_entries(self, folder_path: str) -> tuple[Path, list[Path]]:
        raw_value = (folder_path or "").strip()
        if not raw_value:
            raise bad_request("Folder path is required")

        candidate = Path(raw_value).expanduser()
        if not candidate.is_absolute():
            raise bad_request(f"Folder path must be absolute: '{raw_value}'")
        candidate = self._allowed_local_path(candidate)
        if not candidate.exists():
            raise bad_request(f"Folder path does not exist: '{raw_value}'")
        if not candidate.is_dir():
            raise bad_request(f"Folder path is not a directory: '{raw_value}'")

        try:
            entries = self._acquisition_folder_entries(candidate)
        except OSError as exc:
            raise bad_request(f"Folder path is not readable: '{raw_value}'") from exc
        return candidate.resolve(), entries

    def _acquisition_folder_entries(self, folder: Path) -> list[Path]:
        """List top-level acquisition files, one per sample.

        A converted mzML often sits next to the Thermo RAW it came from, and
        importing both would register one acquisition twice. The RAW wins in
        that case because the workflow converts it anyway.
        """

        by_stem: dict[str, Path] = {}
        for entry in folder.iterdir():
            try:
                self._allowed_local_path(entry)
            except HTTPException:
                continue
            if not entry.is_file() or not is_acquisition_file(entry.name):
                continue
            key = entry.stem.lower()
            current = by_stem.get(key)
            if current is None or entry.suffix.lower() == ".raw":
                by_stem[key] = entry
        return sorted(by_stem.values(), key=lambda entry: entry.name.lower())

    def upload_thermo_raw(
        self,
        user_id: int,
        study_id: int,
        upload: UploadFile,
        sample_id: int | None = None,
    ) -> DatasetFile:
        study = self.studies.get(user_id, study_id)
        self.studies.perms.require_edit_study(user_id, study.id, study.status)
        sample: Sample | None = None
        if sample_id is not None:
            sample = self.db.query(Sample).filter(Sample.id == sample_id, Sample.study_id == study_id).first()
            if sample is None:
                raise bad_request("Sample not found for this study")

        dataset = self._ensure_raw_dataset(user_id, study_id)
        stored = self.storage.store_thermo_raw(study_id, upload)

        acquisition = AcquisitionService(self.db).create(dataset.assay_id, sample_id, {}) if sample_id is not None else None
        dataset_file = DatasetFile(
            dataset_id=dataset.id,
            sample_id=sample_id,
            acquisition_id=acquisition.id if acquisition else None,
            file_role="thermo_raw",
            original_filename=stored.original_filename,
            storage_uri=stored.storage_uri,
            source_scheme="file",
            ingest_mode="uploaded_file",
            checksum_sha256=stored.checksum_sha256,
            file_size_bytes=stored.file_size_bytes,
            media_type=stored.media_type,
            status="uploaded",
            created_by=user_id,
        )
        self.db.add(dataset_file)
        if sample_id is not None:
            self._ensure_dataset_sample_link(dataset.id, sample_id)
            self._ensure_assay_sample_link(dataset.assay_id, sample_id)
        self.audit.log(
            user_id,
            "dataset_file.upload_raw",
            "dataset_file",
            "new",
            {
                "study_id": study_id,
                "sample_id": sample_id,
                "name": stored.original_filename,
                "storage_uri": stored.storage_uri,
            },
        )
        self.db.commit()
        self.db.refresh(dataset_file)
        return dataset_file

    def create_samples_with_files(
        self,
        user_id: int,
        study_id: int,
        samples_payload: list[SampleBulkCreateItem],
        uploads: list[UploadFile],
    ) -> list[Sample]:
        study = self.studies.get(user_id, study_id)
        self.studies.perms.require_edit_study(user_id, study.id, study.status)

        if not samples_payload:
            raise bad_request("At least one sample payload is required")
        if not uploads:
            raise bad_request("At least one Thermo RAW file is required")
        if len(samples_payload) != len(uploads):
            raise bad_request("Each sample must have one corresponding file (counts do not match)")

        raw_dataset = self._ensure_raw_dataset(user_id, study_id)
        created_samples: list[Sample] = []

        for sample_payload, upload in zip(samples_payload, uploads):
            sample_type, annotations_json = self._normalize_sample_metadata(sample_payload.sample_type, sample_payload.annotations_json)
            annotations_json, acquisition_annotations = split_acquisition_annotations(annotations_json)
            matches = self.db.query(Sample).filter(Sample.study_id == study_id,
                func.lower(Sample.name) == sample_payload.name.strip().lower()).limit(2).all()
            if len(matches) > 1:
                raise bad_request("Several study samples share this name; use explicit sample-ID registration")
            sample = matches[0] if matches else None
            if sample is None:
                sample = Sample(
                    study_id=study_id, name=sample_payload.name, description=sample_payload.description,
                    replicate_number=sample_payload.replicate_number, dilution_factor=sample_payload.dilution_factor,
                    sample_type=sample_type, organism=sample_payload.organism, matrix=sample_payload.matrix,
                    annotations_json=annotations_json)
                self.db.add(sample)
                self.db.flush()
            stored = self.storage.store_thermo_raw(study_id, upload)

            self._ensure_assay_sample_link(raw_dataset.assay_id, sample.id)
            self._ensure_dataset_sample_link(raw_dataset.id, sample.id)
            acquisition = AcquisitionService(self.db).create(raw_dataset.assay_id, sample.id, acquisition_annotations)

            dataset_file = DatasetFile(
                dataset_id=raw_dataset.id,
                sample_id=sample.id,
                acquisition_id=acquisition.id,
                file_role="thermo_raw",
                original_filename=stored.original_filename,
                storage_uri=stored.storage_uri,
                checksum_sha256=stored.checksum_sha256,
                file_size_bytes=stored.file_size_bytes,
                media_type=stored.media_type,
                status="uploaded",
                created_by=user_id,
            )
            self.db.add(dataset_file)
            self.audit.log(
                user_id,
                "sample.create_with_file",
                "sample",
                "new",
                {
                    "study_id": study_id,
                    "sample_name": sample.name,
                    "storage_uri": stored.storage_uri,
                },
            )
            created_samples.append(sample)

        self.db.commit()
        for sample in created_samples:
            self.db.refresh(sample)
        return created_samples

    def create_samples_with_paths(
        self,
        user_id: int,
        study_id: int,
        rows: list[SampleBulkPathCreateItem],
    ) -> list[Sample]:
        default_assay = self.studies.get_default_assay(study_id)
        return self.create_samples_with_paths_for_assay(user_id, default_assay.id, rows)

    def create_samples_with_paths_for_assay(
        self,
        user_id: int,
        assay_id: int,
        rows: list[SampleBulkPathCreateItem],
    ) -> list[Sample]:
        assay = self.db.query(Assay).filter(Assay.id == assay_id).first()
        if assay is None:
            raise not_found("Assay not found")
        study_id = assay.study_id
        study = self.studies.get(user_id, study_id)
        self.studies.perms.require_edit_study(user_id, study.id, study.status)

        if not rows:
            raise bad_request("At least one sample row is required")

        raw_dataset = self._ensure_raw_dataset(user_id, study_id, assay_id=assay.id)
        created_samples: list[Sample] = []

        normalized_paths: list[tuple[str, str, int | None]] = []
        for row in rows:
            storage_uri, original_filename, file_size_bytes = self._normalize_local_source_path(row.raw_file.source_path)
            normalized_paths.append((storage_uri, original_filename, file_size_bytes))

        uri_counts: dict[str, int] = {}
        for uri, _, _ in normalized_paths:
            uri_counts[uri] = uri_counts.get(uri, 0) + 1
        duplicate_source_uris = sorted(uri for uri, count in uri_counts.items() if count > 1)
        if duplicate_source_uris:
            raise bad_request(f"Duplicate RAW source paths in request: {', '.join(duplicate_source_uris)}")

        existing_source_uris = self._registered_raw_source_uris(study_id)
        for storage_uri, _, _ in normalized_paths:
            if storage_uri in existing_source_uris:
                raise bad_request(f"RAW source path '{storage_uri}' has already been registered for this study")

        try:
            checksums = {uri: sha256_file(Path(uri[len("file://"):])) for uri, _, _ in normalized_paths}
        except OSError as exc:
            raise bad_request("RAW source file could not be hashed") from exc

        study_sample_rows = (
            self.db.query(Sample, func.lower(Sample.name))
            .filter(Sample.study_id == study_id)
            .order_by(Sample.id.asc())
            .all()
        )
        sample_by_id = {int(sample.id): sample for sample, _ in study_sample_rows}
        sample_by_name: dict[str, Sample] = {}
        ambiguous_names = set()
        for sample, normalized_name in study_sample_rows:
            if str(normalized_name) in sample_by_name:
                ambiguous_names.add(str(normalized_name))
            sample_by_name.setdefault(str(normalized_name), sample)

        prepared_rows: list[tuple[Sample, bool, dict, str, str, int | None]] = []
        for row, (storage_uri, original_filename, file_size_bytes) in zip(rows, normalized_paths):
            row_annotations = normalize_sample_annotations(row.annotations_json)
            created_new_sample = False
            if row.existing_sample_id is not None:
                sample = sample_by_id.get(int(row.existing_sample_id))
                if sample is None:
                    raise bad_request(f"Existing sample #{row.existing_sample_id} was not found in this study")
            else:
                if row.sample is None:
                    raise bad_request("Each RAW path row must provide either existing_sample_id or sample metadata")
                sample_payload = row.sample
                normalized_name = sample_payload.name.strip().lower()
                if normalized_name in ambiguous_names:
                    raise bad_request("Several study samples share this name; select existing_sample_id explicitly")
                sample = sample_by_name.get(normalized_name)
                if sample is None:
                    sample_type, annotations_json = self._normalize_sample_metadata(sample_payload.sample_type, sample_payload.annotations_json)
                    annotations_json, _ = split_acquisition_annotations(annotations_json)
                    annotations_json = sample_defaults_from_row_annotations(annotations_json, row_annotations)
                    sample = Sample(
                        study_id=study_id,
                        name=sample_payload.name,
                        description=sample_payload.description,
                        replicate_number=sample_payload.replicate_number,
                        dilution_factor=sample_payload.dilution_factor,
                        sample_type=sample_type,
                        organism=sample_payload.organism,
                        matrix=sample_payload.matrix,
                        annotations_json=annotations_json,
                    )
                    self.db.add(sample)
                    sample_by_name[normalized_name] = sample
                    created_new_sample = True
            if row.sample is not None:
                _, acquisition_defaults = split_acquisition_annotations(row.sample.annotations_json)
                row_annotations = {**acquisition_defaults, **row_annotations}
            prepared_rows.append((sample, created_new_sample, row_annotations, storage_uri, original_filename, file_size_bytes))

        self.db.flush()
        sample_ids = {int(sample.id) for sample, _, _, _, _, _ in prepared_rows}
        assay_link_by_sample_id = {
            int(link.sample_id): link
            for link in self.db.query(AssaySampleLink)
            .filter(AssaySampleLink.assay_id == assay.id, AssaySampleLink.sample_id.in_(sample_ids))
            .all()
        }
        dataset_link_sample_ids = {
            int(sample_id)
            for (sample_id,) in self.db.query(DatasetSampleLink.sample_id)
            .filter(DatasetSampleLink.dataset_id == raw_dataset.id, DatasetSampleLink.sample_id.in_(sample_ids))
            .all()
        }

        for sample, created_new_sample, row_annotations, storage_uri, original_filename, file_size_bytes in prepared_rows:
            sample_id = int(sample.id)
            row_annotations, acquisition_annotations = split_acquisition_annotations(row_annotations)
            acquisition = AcquisitionService(self.db).create(assay.id, sample_id, acquisition_annotations)
            assay_link = assay_link_by_sample_id.get(sample_id)
            if assay_link is None:
                assay_link = AssaySampleLink(
                    assay_id=assay.id,
                    sample_id=sample_id,
                    sample_role="input",
                    annotations_json=row_annotations,
                )
                self.db.add(assay_link)
                assay_link_by_sample_id[sample_id] = assay_link
            elif row_annotations:
                assay_link.annotations_json = {**annotations_dict(assay_link.annotations_json), **row_annotations}

            if sample_id not in dataset_link_sample_ids:
                self.db.add(DatasetSampleLink(dataset_id=raw_dataset.id, sample_id=sample_id, sample_role="input"))
                dataset_link_sample_ids.add(sample_id)

            dataset_file = DatasetFile(
                dataset_id=raw_dataset.id,
                sample_id=sample_id,
                acquisition_id=acquisition.id,
                file_role="thermo_raw",
                original_filename=original_filename,
                storage_uri=storage_uri,
                source_scheme="file",
                ingest_mode="registered_path",
                checksum_sha256=checksums[storage_uri],
                file_size_bytes=file_size_bytes,
                media_type="application/octet-stream",
                status="registered",
                created_by=user_id,
            )
            self.db.add(dataset_file)
            self.audit.log(
                user_id,
                "sample.create_with_path",
                "sample",
                "new",
                {
                    "study_id": study_id,
                    "assay_id": assay.id,
                    "sample_name": sample.name,
                    "reused_existing_sample": not created_new_sample,
                    "acquisition_id": acquisition.id,
                    "storage_uri": storage_uri,
                },
            )
            created_samples.append(sample)

        created_sample_ids = [int(sample.id) for sample in created_samples]
        self.db.commit()
        refreshed_samples = self.db.query(Sample).filter(Sample.id.in_(set(created_sample_ids))).all()
        refreshed_by_id = {int(sample.id): sample for sample in refreshed_samples}
        return [refreshed_by_id[sample_id] for sample_id in created_sample_ids]

    def import_raw_folder_for_assay(self, user_id: int, assay_id: int, folder_path: str) -> dict:
        assay = self.db.query(Assay).filter(Assay.id == assay_id).first()
        if assay is None:
            raise not_found("Assay not found")
        study_id = assay.study_id
        study = self.studies.get(user_id, study_id)
        self.studies.perms.require_edit_study(user_id, study.id, study.status)

        resolved_folder, entries = self._raw_folder_entries(folder_path)
        raw_dataset = self._ensure_raw_dataset(user_id, study_id, assay_id=assay.id)
        existing_source_uris = self._registered_raw_source_uris(study_id)

        imported_sample_ids: list[int] = []
        imported_file_ids: list[int] = []
        skipped_existing_paths: list[str] = []

        rows = []
        source_uris = []
        for scan_index, entry in enumerate(entries, start=1):
            storage_uri, _, _ = self._normalize_local_source_path(str(entry.resolve()))
            if storage_uri in existing_source_uris:
                skipped_existing_paths.append(str(entry.resolve()))
                continue
            source_uris.append(storage_uri)
            rows.append(SampleBulkPathCreateItem(
                sample=SampleBulkCreateItem(name=entry.stem, description=entry.stem, replicate_number=1,
                                           dilution_factor=1.0, organism="Homo sapiens"),
                annotations_json={"batch_id": "1", "injection_order": scan_index},
                raw_file={"source_path": str(entry.resolve())}))
        if rows:
            samples = self.create_samples_with_paths_for_assay(user_id, assay.id, rows)
            file_by_uri = {f.storage_uri: f.id for f in self.db.query(DatasetFile).filter(
                DatasetFile.dataset_id == raw_dataset.id, DatasetFile.storage_uri.in_(source_uris))}
            imported_sample_ids = [sample.id for sample in samples]
            imported_file_ids = [file_by_uri[uri] for uri in source_uris]
        self.db.commit()
        return {
            "folder_path": str(resolved_folder),
            "scanned_count": len(entries),
            "imported_count": len(imported_sample_ids),
            "skipped_existing_count": len(skipped_existing_paths),
            "imported_sample_ids": imported_sample_ids,
            "imported_file_ids": imported_file_ids,
            "skipped_existing_paths": skipped_existing_paths,
        }

    def scan_raw_folder(self, user_id: int, study_id: int, folder_path: str) -> list[dict]:
        study = self.studies.get(user_id, study_id)
        self.studies.perms.require_edit_study(user_id, study.id, study.status)

        _, entries = self._raw_folder_entries(folder_path)
        registered = self._registered_raw_source_uris(study_id)

        return [
            {
                "source_path": str(entry.resolve()),
                "original_filename": entry.name,
                "sample_code": entry.stem,
                "already_registered": f"file://{entry.resolve()}" in registered,
            }
            for entry in entries
        ]

    def browse_raw_folders(self, user_id: int, study_id: int, folder_path: str | None) -> dict:
        study = self.studies.get(user_id, study_id)
        self.studies.perms.require_edit_study(user_id, study.id, study.status)

        browse_roots = self._browse_roots()
        raw_value = (folder_path or "").strip()
        if not raw_value:
            return {
                "current_path": None,
                "parent_path": None,
                "entries": [
                    {
                        "name": root.name or str(root),
                        "path": str(root),
                        "raw_file_count": self._count_top_level_raw_files(root),
                    }
                    for root in browse_roots
                ],
            }

        candidate = Path(raw_value).expanduser()
        if not candidate.is_absolute():
            raise bad_request(f"Folder path must be absolute: '{raw_value}'")
        resolved = self._allowed_local_path(candidate)
        if not resolved.exists():
            raise bad_request(f"Folder path does not exist: '{raw_value}'")
        if not resolved.is_dir():
            raise bad_request(f"Folder path is not a directory: '{raw_value}'")

        try:
            directories = []
            for entry in resolved.iterdir():
                try:
                    self._allowed_local_path(entry)
                except HTTPException:
                    continue
                if entry.is_dir():
                    directories.append(entry)
            directories.sort(key=lambda entry: entry.name.lower())
        except OSError as exc:
            raise bad_request(f"Folder path is not readable: '{raw_value}'") from exc

        parent_path = None
        for root in browse_roots:
            if resolved == root:
                break
            if self._path_within_root(resolved, root):
                parent = resolved.parent
                if self._path_within_root(parent, root):
                    parent_path = str(parent)
                break

        return {
            "current_path": str(resolved),
            "parent_path": parent_path,
            "entries": [
                {
                    "name": entry.name,
                    "path": str(entry.resolve()),
                    "raw_file_count": self._count_top_level_raw_files(entry),
                }
                for entry in directories
            ],
        }

    def _count_top_level_raw_files(self, folder: Path) -> int:
        try:
            return len(self._acquisition_folder_entries(folder))
        except OSError:
            return 0
