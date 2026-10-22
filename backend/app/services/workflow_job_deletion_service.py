from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.errors import bad_request, not_found
from app.core.permissions import require_allowed
from app.core.states import FINISHED_JOB_STATES
from app.models.models import Dataset, DatasetFile, DatasetSampleLink, ResultSet, WorkflowFeatureReview, WorkflowJob, WorkflowRunArtifact
from app.services.audit_service import AuditService
from app.services.study_service import StudyService




class WorkflowJobDeletionService:
    def __init__(self, db: Session):
        self.db = db
        self.studies = StudyService(db)
        self.audit = AuditService(db)

    def soft_delete(self, user_id: int, assay_id: int, job_id: int) -> WorkflowJob:
        job = (
            self.db.query(WorkflowJob)
            .filter(
                WorkflowJob.id == job_id,
                WorkflowJob.assay_id == assay_id,
            )
            .first()
        )
        if job is None:
            raise not_found("Workflow job not found for assay")

        study = self.studies.get(user_id, job.study_id)
        require_allowed(
            self.studies.perms.can_operate_workflows(user_id, study.id),
            "Only operator/admin can delete workflow jobs",
        )
        if job.deleted_at is not None:
            return job
        if job.status not in FINISHED_JOB_STATES:
            raise bad_request("Only succeeded or failed workflow jobs can be deleted")

        job.deleted_at = datetime.now(timezone.utc)
        job.deleted_by = user_id
        self.audit.log(
            user_id,
            "workflow.delete",
            "workflow_job",
            str(job.id),
            {
                "study_id": job.study_id,
                "assay_id": job.assay_id,
                "execution_status": job.status,
            },
        )
        self.db.commit()
        self.db.refresh(job)
        return job

    def purge_generated_records(self, job_id: int) -> dict:
        job = self.db.query(WorkflowJob).filter(WorkflowJob.id == job_id).first()
        if job is None:
            raise not_found("Workflow job not found")
        if job.deleted_at is None:
            raise bad_request("Workflow job must be deleted before purge")
        if job.status not in FINISHED_JOB_STATES:
            raise bad_request("Only terminal workflow jobs can be purged")
        if job.purged_at is not None:
            return {
                "job_id": job.id,
                "deleted_file_count": 0,
                "deleted_dataset_count": 0,
                "deleted_result_count": 0,
            }

        artifacts = (
            self.db.query(WorkflowRunArtifact)
            .filter(WorkflowRunArtifact.workflow_job_id == job.id)
            .all()
        )
        file_ids = list(dict.fromkeys(artifact.dataset_file_id for artifact in artifacts))
        files = self.db.query(DatasetFile).filter(DatasetFile.id.in_(file_ids)).all() if file_ids else []
        dataset_ids = {dataset_file.dataset_id for dataset_file in files}
        manifest = dict(job.input_manifest_json or {})
        input_file_values = list(manifest.get("file_ids") or [])
        input_file_values.append(manifest.get("reference_file_asset_id"))
        input_file_values.extend(
            item.get("file_asset_id")
            for item in (manifest.get("selected_files") or [])
            if isinstance(item, dict)
        )
        input_file_ids: set[int] = set()
        for value in input_file_values:
            try:
                input_file_ids.add(int(value))
            except (TypeError, ValueError):
                continue

        self.db.query(WorkflowFeatureReview).filter(
            WorkflowFeatureReview.workflow_job_id == job.id,
        ).delete(synchronize_session=False)
        self.db.query(WorkflowRunArtifact).filter(
            WorkflowRunArtifact.workflow_job_id == job.id,
        ).delete(synchronize_session=False)
        deleted_result_count = self.db.query(ResultSet).filter(
            ResultSet.workflow_job_id == job.id,
        ).delete(synchronize_session=False)
        self.db.flush()

        deleted_file_count = 0
        for dataset_file in files:
            if dataset_file.id in input_file_ids:
                continue
            still_referenced = (
                self.db.query(WorkflowRunArtifact.id)
                .filter(WorkflowRunArtifact.dataset_file_id == dataset_file.id)
                .first()
            )
            if still_referenced is None:
                self.db.delete(dataset_file)
                deleted_file_count += 1
        self.db.flush()

        owned_names = {f"job-{job.id}-derived", f"job-{job.id}-maf"}
        datasets = (
            self.db.query(Dataset)
            .filter(Dataset.id.in_(dataset_ids), Dataset.name.in_(owned_names))
            .all()
            if dataset_ids
            else []
        )
        deleted_dataset_count = 0
        for dataset in datasets:
            has_files = self.db.query(DatasetFile.id).filter(DatasetFile.dataset_id == dataset.id).first()
            if has_files is None:
                self.db.query(DatasetSampleLink).filter(
                    DatasetSampleLink.dataset_id == dataset.id,
                ).delete(synchronize_session=False)
                self.db.delete(dataset)
                deleted_dataset_count += 1

        job.purged_at = datetime.now(timezone.utc)
        self.audit.log(
            None,
            "workflow.purge",
            "workflow_job",
            str(job.id),
            {
                "deleted_file_count": deleted_file_count,
                "deleted_dataset_count": deleted_dataset_count,
                "deleted_result_count": int(deleted_result_count or 0),
            },
        )
        self.db.commit()
        self.db.refresh(job)
        return {
            "job_id": job.id,
            "deleted_file_count": deleted_file_count,
            "deleted_dataset_count": deleted_dataset_count,
            "deleted_result_count": int(deleted_result_count or 0),
        }
