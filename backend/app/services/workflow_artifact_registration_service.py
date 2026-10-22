"""Register workflow output files back into the study data model.

This module converts collected filesystem artifacts into `Dataset`,
`DatasetFile`, and `WorkflowRunArtifact` records. It does not decide job state;
the worker state service calls it during terminal transitions.
"""

from sqlalchemy.orm import Session

from app.models.models import Dataset, DatasetFile, DatasetSampleLink, WorkflowJob, WorkflowRunArtifact


class WorkflowArtifactRegistrationService:
    def __init__(self, db: Session):
        self.db = db

    def register_output_records(self, job: WorkflowJob, output_records: list[dict]) -> list[int]:
        input_sample_ids = self.input_sample_ids_for_job(job)
        derived_dataset = self._get_or_create_dataset(
            job,
            dataset_kind="derived",
            dataset_type="workflow_outputs",
            name=f"job-{job.id}-derived",
        )
        self.ensure_dataset_sample_links(derived_dataset.id, input_sample_ids)

        generated_file_ids: list[int] = []
        for record in output_records:
            target_dataset = derived_dataset
            if record["artifact_role"] == "msdial_mztabm":
                target_dataset = self._get_or_create_dataset(
                    job,
                    dataset_kind="maf",
                    dataset_type="metabolite_assignment",
                    name=f"job-{job.id}-maf",
                )
                self.ensure_dataset_sample_links(target_dataset.id, input_sample_ids)

            existing_file = self._existing_artifact_file(job.id, record)
            if existing_file is not None:
                existing_file.dataset_id = target_dataset.id
                existing_file.file_role = record["file_kind"]
                existing_file.original_filename = record["filename"]
                existing_file.checksum_sha256 = record.get("checksum_sha256")
                existing_file.file_size_bytes = record.get("file_size_bytes")
                existing_file.media_type = record.get("media_type")
                existing_file.status = "validated"
                generated_file_ids.append(existing_file.id)
                continue

            dataset_file = DatasetFile(
                dataset_id=target_dataset.id,
                sample_id=None,
                file_role=record["file_kind"],
                original_filename=record["filename"],
                storage_uri=record["storage_uri"],
                checksum_sha256=record.get("checksum_sha256"),
                file_size_bytes=record.get("file_size_bytes"),
                media_type=record.get("media_type"),
                status="validated",
                created_by=job.requested_by,
            )
            self.db.add(dataset_file)
            self.db.flush()
            generated_file_ids.append(dataset_file.id)
            link = WorkflowRunArtifact(workflow_job_id=job.id, dataset_file_id=dataset_file.id, artifact_role=record["artifact_role"])
            self.db.add(link)
            self.db.flush()
        return generated_file_ids

    def _existing_artifact_file(self, workflow_job_id: int, record: dict) -> DatasetFile | None:
        return (
            self.db.query(DatasetFile)
            .join(WorkflowRunArtifact, WorkflowRunArtifact.dataset_file_id == DatasetFile.id)
            .filter(
                WorkflowRunArtifact.workflow_job_id == workflow_job_id,
                WorkflowRunArtifact.artifact_role == record["artifact_role"],
                DatasetFile.storage_uri == record["storage_uri"],
            )
            .first()
        )

    def input_sample_ids_for_job(self, job: WorkflowJob) -> list[int]:
        manifest = dict(job.input_manifest_json or {})
        selected_files = manifest.get("selected_files") or []
        sample_ids: list[int] = []
        for item in selected_files:
            sample_id = item.get("sample_id")
            if sample_id is None:
                continue
            try:
                sample_ids.append(int(sample_id))
            except (TypeError, ValueError):
                continue

        if sample_ids:
            return list(dict.fromkeys(sample_ids))

        file_ids = [int(file_id) for file_id in (manifest.get("file_ids") or [])]
        if not file_ids:
            return []
        rows = self.db.query(DatasetFile.sample_id).filter(DatasetFile.id.in_(file_ids)).all()
        return list(dict.fromkeys(int(row[0]) for row in rows if row[0] is not None))

    def ensure_dataset_sample_links(self, dataset_id: int, sample_ids: list[int], sample_role: str = "input") -> None:
        for sample_id in sample_ids:
            existing = (
                self.db.query(DatasetSampleLink)
                .filter(DatasetSampleLink.dataset_id == dataset_id, DatasetSampleLink.sample_id == sample_id)
                .first()
            )
            if existing is not None:
                continue
            self.db.add(DatasetSampleLink(dataset_id=dataset_id, sample_id=sample_id, sample_role=sample_role))

    def _get_or_create_dataset(self, job: WorkflowJob, *, dataset_kind: str, dataset_type: str, name: str) -> Dataset:
        dataset = (
            self.db.query(Dataset)
            .filter(
                Dataset.study_id == job.study_id,
                Dataset.assay_id == job.assay_id,
                Dataset.dataset_kind == dataset_kind,
                Dataset.name == name,
            )
            .first()
        )
        if dataset is not None:
            return dataset

        dataset = Dataset(
            study_id=job.study_id,
            assay_id=job.assay_id,
            name=name,
            dataset_kind=dataset_kind,
            dataset_type=dataset_type,
            status="validated",
            created_by=job.requested_by,
        )
        self.db.add(dataset)
        self.db.flush()
        return dataset
