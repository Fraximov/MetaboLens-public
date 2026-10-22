"""Read workflow jobs and registered workflow outputs for the UI.

This module is intentionally read-only. It checks study access through
`StudyService` and shapes the existing response dictionaries without changing
job state or output records.
"""

from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session

from app.core.errors import bad_request, not_found
from app.models.models import Dataset, DatasetFile, DatasetSampleLink, Sample, WorkflowJob, WorkflowRunArtifact
from app.services.study_service import StudyService


class WorkflowJobReadService:
    def __init__(self, db: Session):
        self.db = db
        self.studies = StudyService(db)

    def list_jobs(self, user_id: int, assay_id: int) -> list[WorkflowJob]:
        self.studies.get_assay(user_id, assay_id)
        return (
            self.db.query(WorkflowJob)
            .filter(WorkflowJob.assay_id == assay_id, WorkflowJob.deleted_at.is_(None))
            .order_by(WorkflowJob.id.desc())
            .all()
        )

    def get_job(self, user_id: int, job_id: int) -> WorkflowJob:
        job = self.db.query(WorkflowJob).filter(WorkflowJob.id == job_id, WorkflowJob.deleted_at.is_(None)).first()
        if not job:
            raise not_found("Workflow job not found")
        self.studies.get(user_id, job.study_id)
        return job

    def list_output_files(self, user_id: int, assay_id: int, job_id: int | None = None) -> list[dict]:
        self.studies.get_assay(user_id, assay_id)
        if job_id is not None:
            job = (
                self.db.query(WorkflowJob)
                .filter(
                    WorkflowJob.id == job_id,
                    WorkflowJob.assay_id == assay_id,
                    WorkflowJob.deleted_at.is_(None),
                )
                .first()
            )
            if job is None:
                raise not_found("Workflow job not found for assay")

        query = (
            self.db.query(WorkflowRunArtifact, WorkflowJob.workflow_definition_id, WorkflowJob.execution_profile, DatasetFile, Dataset)
            .join(WorkflowJob, WorkflowJob.id == WorkflowRunArtifact.workflow_job_id)
            .join(DatasetFile, DatasetFile.id == WorkflowRunArtifact.dataset_file_id)
            .join(Dataset, Dataset.id == DatasetFile.dataset_id)
            .filter(WorkflowJob.assay_id == assay_id, WorkflowJob.deleted_at.is_(None))
        )
        if job_id is not None:
            query = query.filter(WorkflowRunArtifact.workflow_job_id == job_id)
        rows = query.order_by(WorkflowRunArtifact.id.desc()).all()
        dataset_ids = list(dict.fromkeys(int(dataset.id) for _, _, _, _, dataset in rows))
        dataset_sample_rows = (
            self.db.query(DatasetSampleLink.dataset_id, Sample.id, Sample.name)
            .join(Sample, Sample.id == DatasetSampleLink.sample_id)
            .filter(DatasetSampleLink.dataset_id.in_(dataset_ids))
            .order_by(DatasetSampleLink.dataset_id.asc(), Sample.id.asc())
            .all()
            if dataset_ids
            else []
        )
        dataset_sample_ids_by_dataset: dict[int, list[int]] = {}
        dataset_sample_names_by_dataset: dict[int, list[str]] = {}
        for dataset_id, sample_id, sample_name in dataset_sample_rows:
            dataset_sample_ids_by_dataset.setdefault(int(dataset_id), []).append(int(sample_id))
            dataset_sample_names_by_dataset.setdefault(int(dataset_id), []).append(sample_name)

        file_sample_ids = [int(dataset_file.sample_id) for _, _, _, dataset_file, _ in rows if dataset_file.sample_id is not None]
        file_sample_name_by_id: dict[int, str] = {}
        if file_sample_ids:
            sample_rows = self.db.query(Sample.id, Sample.name).filter(Sample.id.in_(file_sample_ids)).all()
            file_sample_name_by_id = {int(sample_id): sample_name for sample_id, sample_name in sample_rows}

        output_files: list[dict] = []
        for artifact, workflow_definition_id, execution_profile, dataset_file, dataset in rows:
            output_files.append(
                {
                    "workflow_job_id": artifact.workflow_job_id,
                    "workflow_definition_id": workflow_definition_id,
                    "execution_profile": execution_profile,
                    "artifact_role": artifact.artifact_role,
                    "file_id": dataset_file.id,
                    "dataset_id": dataset.id,
                    "dataset_name": dataset.name,
                    "dataset_kind": dataset.dataset_kind,
                    "dataset_type": dataset.dataset_type,
                    "dataset_sample_ids": dataset_sample_ids_by_dataset.get(int(dataset.id), []),
                    "dataset_sample_names": dataset_sample_names_by_dataset.get(int(dataset.id), []),
                    "file_sample_id": dataset_file.sample_id,
                    "file_sample_name": file_sample_name_by_id.get(int(dataset_file.sample_id)) if dataset_file.sample_id is not None else None,
                    "file_role": dataset_file.file_role,
                    "original_filename": dataset_file.original_filename,
                    "status": dataset_file.status,
                    "storage_uri": dataset_file.storage_uri,
                    "file_size_bytes": dataset_file.file_size_bytes,
                    "checksum_sha256": dataset_file.checksum_sha256,
                    "media_type": dataset_file.media_type,
                    "created_at": dataset_file.created_at,
                }
            )
        return output_files

    def get_output_file_download(self, user_id: int, assay_id: int, file_id: int) -> tuple[Path, str, str]:
        self.studies.get_assay(user_id, assay_id)

        row = (
            self.db.query(WorkflowRunArtifact, DatasetFile)
            .join(WorkflowJob, WorkflowJob.id == WorkflowRunArtifact.workflow_job_id)
            .join(DatasetFile, DatasetFile.id == WorkflowRunArtifact.dataset_file_id)
            .filter(
                WorkflowJob.assay_id == assay_id,
                WorkflowJob.deleted_at.is_(None),
                DatasetFile.id == file_id,
            )
            .first()
        )
        if row is None:
            raise not_found("Workflow output file not found for assay")

        _, dataset_file = row
        parsed = urlparse(dataset_file.storage_uri or "")
        if parsed.scheme != "file":
            raise bad_request("Only local workflow output files can be downloaded")
        path = Path(unquote(parsed.path)).expanduser().resolve()
        if not path.is_file():
            raise not_found("Workflow output file is no longer available on disk")
        media_type = dataset_file.media_type or "application/octet-stream"
        filename = dataset_file.original_filename or path.name
        return path, media_type, filename
