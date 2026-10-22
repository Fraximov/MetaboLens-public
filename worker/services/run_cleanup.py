"""Cleanup helpers for worker-owned workflow run scratch data."""

from pathlib import Path
import shutil
from typing import Any

from services.materialized_run import MaterializedRun


class RunCleanupService:
    """Remove disposable run files after durable artifacts are registered."""

    def remove_staged_raw_inputs(self, run: MaterializedRun) -> dict[str, Any]:
        """Delete copied RAW inputs for a succeeded workflow run.

        The original RAW files are tracked through DatasetFile.storage_uri and
        are not touched here. Only the worker-owned `inputs/raw_data` directory
        inside this run folder is eligible for removal.
        """

        target = Path(run.staged_input_dir).resolve()
        run_root = Path(run.run_root).resolve()
        allowed_target = run_root / "inputs" / "raw_data"
        payload: dict[str, Any] = {
            "staged_inputs_removed": False,
            "staged_input_dir": str(target),
            "removed_file_count": 0,
            "removed_bytes": 0,
        }
        try:
            if target != allowed_target:
                raise ValueError(f"Refusing to remove unexpected staged input path: {target}")
            if not target.exists():
                payload["staged_inputs_removed"] = True
                payload["cleanup_note"] = "staged input directory was already absent"
                return payload
            if not target.is_dir():
                raise ValueError(f"Staged input path is not a directory: {target}")

            file_count, byte_count = self._directory_size(target)
            shutil.rmtree(target)
            payload.update(
                {
                    "staged_inputs_removed": True,
                    "removed_file_count": file_count,
                    "removed_bytes": byte_count,
                }
            )
        except Exception as exc:  # noqa: BLE001 - cleanup must not fail a succeeded scientific run
            payload["cleanup_error"] = str(exc)
        return payload

    @staticmethod
    def _directory_size(path: Path) -> tuple[int, int]:
        file_count = 0
        byte_count = 0
        for child in path.rglob("*"):
            if not child.is_file():
                continue
            file_count += 1
            try:
                byte_count += child.stat().st_size
            except OSError:
                continue
        return file_count, byte_count
