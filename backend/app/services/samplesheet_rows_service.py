"""Resolve the exact scientific metadata frozen at workflow launch."""
from typing import Any

from app.core.sample_context import effective_sample_annotations
from app.models.models import AssaySampleLink, Dataset, DatasetFile, Sample


class SamplesheetRowsService:
    def __init__(self, db):
        self.db = db

    def build_rows(self, selected_files: list[DatasetFile], reference_file_asset_id: int, *, legacy_metadata: bool = False) -> list[dict[str, Any]]:
        """Build one samplesheet row per selected file without changing order."""

        sample_ids = [file.sample_id for file in selected_files if file.sample_id is not None]
        samples = self.db.query(Sample).filter(Sample.id.in_(sample_ids)).all() if sample_ids else []
        sample_by_id = {sample.id: sample for sample in samples}
        dataset_ids = [int(file.dataset_id) for file in selected_files]
        datasets = self.db.query(Dataset).filter(Dataset.id.in_(dataset_ids)).all() if dataset_ids else []
        assay_id_by_dataset_id = {int(dataset.id): int(dataset.assay_id) for dataset in datasets}
        assay_ids = list(dict.fromkeys(assay_id_by_dataset_id.values()))
        links = (
            self.db.query(AssaySampleLink)
            .filter(AssaySampleLink.sample_id.in_(sample_ids), AssaySampleLink.assay_id.in_(assay_ids))
            .all()
            if sample_ids and assay_ids
            else []
        )
        link_by_assay_sample = {
            (int(link.assay_id), int(link.sample_id)): link
            for link in links
        }

        rows: list[dict[str, Any]] = []
        for file_asset in selected_files:
            sample = sample_by_id.get(file_asset.sample_id)
            assay_id = assay_id_by_dataset_id.get(int(file_asset.dataset_id))
            assay_link = link_by_assay_sample.get((assay_id, int(sample.id))) if sample is not None and assay_id is not None else None
            annotations = effective_sample_annotations(
                sample.annotations_json if sample is not None else {},
                assay_link.annotations_json if assay_link is not None else {},
                file_asset.acquisition.annotations_json if file_asset.acquisition is not None and not legacy_metadata else None,
            )
            flattened_annotations = self.flatten_annotations(annotations)
            rows.append(
                {
                    "sample_id": sample.id if sample else "",
                    "sample_name": sample.name if sample else "",
                    "file_asset_id": file_asset.id,
                    "input_filename": file_asset.original_filename,
                    "storage_uri": file_asset.storage_uri,
                    "sample_type": sample.sample_type if sample else "",
                    "organism": sample.organism if sample else "",
                    "matrix": sample.matrix if sample else "",
                    "replicate_number": sample.replicate_number if sample and sample.replicate_number is not None else "",
                    "dilution_factor": sample.dilution_factor if sample and sample.dilution_factor is not None else "",
                    "is_reference": "true" if file_asset.id == reference_file_asset_id else "false",
                    **flattened_annotations,
                }
            )
        return rows

    @staticmethod
    def derive_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
        """Keep core columns stable and include every emitted annotation key, even if cleared."""

        base_fieldnames = [
            "sample_id",
            "sample_name",
            "file_asset_id",
            "input_filename",
            "storage_uri",
            "sample_type",
            "organism",
            "matrix",
            "replicate_number",
            "dilution_factor",
            "is_reference",
        ]
        special_annotations = ["group_label", "batch_id", "injection_order"]
        extras = sorted(
            {
                key
                for row in rows
                for key, value in row.items()
                if key not in set(base_fieldnames + special_annotations)
            }
        )
        present_specials = [
            key
            for key in special_annotations
            if any(key in row for row in rows)
        ]
        return base_fieldnames + present_specials + extras

    @staticmethod
    def flatten_annotations(annotations: dict[str, Any]) -> dict[str, Any]:
        """Expose scalar sample annotations as predictable CSV columns."""

        if not isinstance(annotations, dict):
            return {}

        flattened: dict[str, Any] = {}
        for key in ("group_label", "batch_id", "injection_order"):
            value = annotations.get(key)
            if SamplesheetRowsService.is_scalar_annotation_value(value):
                flattened[key] = value

        for key, value in annotations.items():
            if key in {"group_label", "batch_id", "injection_order"}:
                continue
            if SamplesheetRowsService.is_scalar_annotation_value(value):
                flattened[f"annotation__{key}"] = value

        return flattened

    @staticmethod
    def is_scalar_annotation_value(value: Any) -> bool:
        """Return true for annotation values that can safely fit in one CSV cell."""

        return isinstance(value, (str, int, float, bool)) and not isinstance(value, bytes)
