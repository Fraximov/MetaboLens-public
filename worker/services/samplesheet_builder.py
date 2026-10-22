"""Build and write the workflow `samplesheet.csv` contract.

This module turns selected input files plus linked `Sample` metadata into the
CSV consumed by downstream adapter/statistics steps. It does not stage files or
decide workflow launch eligibility.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copy import deepcopy
from app.services.samplesheet_rows_service import SamplesheetRowsService
from app.models.models import DatasetFile


@dataclass(frozen=True)
class SamplesheetFile:
    """The samplesheet path plus the exact rows/columns written."""

    path: Path
    rows: list[dict[str, Any]]
    fieldnames: list[str]


class SamplesheetBuilder(SamplesheetRowsService):
    """Build samplesheet rows from selected files and linked study samples."""

    def __init__(self, db):
        self.db = db

    def write(self, *, selected_files: list[DatasetFile], reference_file_asset_id: int, output_path: Path,
              staged_files: list[dict[str, Any]] | None = None,
              frozen_rows: list[dict[str, Any]] | None = None, legacy_metadata: bool = False) -> SamplesheetFile:
        """Write `samplesheet.csv` for downstream adapter/statistics steps."""

        rows = deepcopy(frozen_rows) if frozen_rows is not None else self.build_rows(selected_files, reference_file_asset_id, legacy_metadata=legacy_metadata)
        if frozen_rows is not None and (len(rows) != len(selected_files) or [r.get("file_asset_id") for r in rows] != [f.id for f in selected_files]):
            raise ValueError("Frozen samplesheet rows must match selected file identities exactly")
        if staged_files is not None:
            staged_by_id = {item["file_asset_id"]: Path(item["staged_path"]).name for item in staged_files}
            if (len(staged_by_id) != len(staged_files)
                    or set(staged_by_id) != {row["file_asset_id"] for row in rows}):
                raise ValueError("Staged files must match samplesheet file identities exactly")
            for row in rows:
                row["acquisition_id"] = f"file-{row['file_asset_id']}"
                row["staged_filename"] = staged_by_id[row["file_asset_id"]]
        fieldnames = self.derive_fieldnames(rows)
        with output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return SamplesheetFile(path=output_path, rows=rows, fieldnames=fieldnames)
