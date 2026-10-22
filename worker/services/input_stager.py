"""Stage workflow input files into a per-job run workspace.

This module owns local file resolution and copying for selected `DatasetFile`
records. It does not decide which files are eligible; the backend launch flow
already made that decision.
"""

import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.engine_contract import sha256_file
from app.models.models import DatasetFile


class InputStager:
    """Copy database-selected local input files into a run workspace."""

    def stage_raw_input_files(self, selected_files: list[DatasetFile], staged_input_dir: Path,
                             *, frozen_files: list[dict] | None = None) -> list[dict[str, Any]]:
        """Stage selected files in database order and return provenance records."""

        identities = {item['file_asset_id']: item for item in frozen_files or []}
        if frozen_files is not None and (len(identities) != len(frozen_files)
                or set(identities) != {file.id for file in selected_files}):
            raise ValueError("Frozen workflow input identities do not match selected files")
        used_target_names: set[str] = set()
        staged_files: list[dict[str, Any]] = []
        for dataset_file in selected_files:
            target_name = self._unique_target_name(dataset_file, used_target_names)
            used_target_names.add(target_name)
            staged_files.append(self.stage_one_file(dataset_file, staged_input_dir, target_name=target_name,
                                                   frozen_identity=identities.get(dataset_file.id)))
        return staged_files

    @staticmethod
    def _unique_target_name(dataset_file: DatasetFile, used_target_names: set[str]) -> str:
        if getattr(dataset_file, "acquisition_id", None) is not None:
            return f"file-{dataset_file.id}{Path(dataset_file.original_filename).suffix}"
        base_name = Path(dataset_file.original_filename).name
        if base_name not in used_target_names:
            return base_name
        return f"{dataset_file.id}_{base_name}"

    def stage_one_file(self, dataset_file: DatasetFile, staged_input_dir: Path, *, target_name: str | None = None,
                       frozen_identity: dict | None = None) -> dict[str, Any]:
        """Copy one local file asset into the staging directory."""

        identity = frozen_identity or {}
        if identity.get('storage_uri', dataset_file.storage_uri) != dataset_file.storage_uri:
            raise ValueError(f"Workflow input {dataset_file.id} storage URI changed after launch")
        source_path = Path(self.resolve_file_asset_path(dataset_file))
        target_path = staged_input_dir / (target_name or Path(dataset_file.original_filename).name)
        temp_path = target_path.with_name(f".{target_path.name}.tmp")

        try:
            staged_input_dir.mkdir(parents=True, exist_ok=True)
            self._remove_existing_path(temp_path)
            self._remove_existing_path(target_path)
            shutil.copy2(source_path, temp_path)
            self._verify_staged_file_size(source_path, temp_path)
            size = temp_path.stat().st_size
            checksum = sha256_file(temp_path)
            if identity.get('file_size_bytes') is not None and size != identity['file_size_bytes']:
                raise ValueError("staged file size differs from frozen launch identity")
            if identity.get('checksum_sha256') is not None and checksum != identity['checksum_sha256']:
                raise ValueError("staged file checksum differs from frozen launch identity")
            temp_path.replace(target_path)
        except Exception as exc:  # noqa: BLE001
            self._remove_existing_path(temp_path)
            raise ValueError(
                f"Failed to stage workflow input {dataset_file.id} ({source_path}) "
                f"to {target_path}: {exc}"
            ) from exc
        return {
            "file_asset_id": dataset_file.id,
            "sample_id": dataset_file.sample_id,
            "original_filename": dataset_file.original_filename,
            "storage_uri": dataset_file.storage_uri,
            "source_scheme": dataset_file.source_scheme,
            "ingest_mode": dataset_file.ingest_mode,
            "staged_path": str(target_path),
            "checksum_sha256": checksum,
            "file_size_bytes": size,
            # Historical null hashes cannot establish equality with launch-time bytes.
            "launch_checksum_verified": identity.get('checksum_sha256') is not None,
        }

    @staticmethod
    def _remove_existing_path(path: Path) -> None:
        """Remove stale staged output paths from previous failed attempts."""

        if not path.exists() and not path.is_symlink():
            return
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
            return
        path.unlink()

    @staticmethod
    def _verify_staged_file_size(source_path: Path, staged_path: Path) -> None:
        """Catch interrupted copies before the partial file becomes the final input."""

        source_size = source_path.stat().st_size
        staged_size = staged_path.stat().st_size
        if source_size != staged_size:
            raise ValueError(
                f"staged file size mismatch for {source_path}: "
                f"expected {source_size} bytes, copied {staged_size} bytes"
            )

    @staticmethod
    def resolve_file_asset_path(dataset_file: DatasetFile) -> str:
        """Return a local filesystem path for supported file-backed assets."""

        storage_uri = dataset_file.storage_uri or ""
        source_scheme = (getattr(dataset_file, "source_scheme", None) or "").strip().lower()
        if source_scheme in {"", "file"}:
            if storage_uri.startswith("file://"):
                return storage_uri[len("file://") :]
            candidate = Path(storage_uri)
            if candidate.is_absolute():
                return str(candidate)
            parsed = urlparse(storage_uri)
            if parsed.scheme == "file":
                return parsed.path
        raise ValueError(f"Only local file-backed workflow inputs are supported for now; unsupported storage URI: {storage_uri}")
