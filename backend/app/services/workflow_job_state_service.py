"""Worker-owned workflow job lifecycle transitions.

This module mutates `WorkflowJob` state as the worker prepares, submits, runs,
and finishes jobs. It delegates output-file registration to the artifact
registration service and does not own launch-time validation.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.models import ResultSet, WorkflowJob
from app.services.provenance_service import ProvenanceService
from app.services.workflow_artifact_registration_service import WorkflowArtifactRegistrationService


class WorkflowJobStateService:
    def __init__(self, db: Session):
        self.db = db
        self.prov = ProvenanceService(db)
        self.artifacts = WorkflowArtifactRegistrationService(db)

    def claim_next_job(
        self,
        *,
        execution_profile: str | None = None,
    ) -> WorkflowJob | None:
        query = self.db.query(WorkflowJob).filter(
            WorkflowJob.status == "queued"
        )
        if execution_profile is not None:
            query = query.filter(
                WorkflowJob.execution_profile == execution_profile
            )
        job = query.order_by(WorkflowJob.id.asc()).first()
        if not job:
            return None
        job.status = "preparing"
        job.status_payload_json = {**(job.status_payload_json or {}), "phase": "preparing"}
        self.prov.log(job.study_id, "workflow.preparing", assay_id=job.assay_id, workflow_job_id=job.id)
        self.db.commit()
        self.db.refresh(job)
        return job

    def list_active_slurm_jobs(
        self,
    ) -> list[WorkflowJob]:
        """List unfinished SSH SLURM jobs for worker reconciliation."""

        return (
            self.db.query(WorkflowJob)
            .filter(
                WorkflowJob.status.in_(
                    ("submitted", "running")
                ),
                WorkflowJob.execution_profile
                == "slurm_singularity_ssh",
                WorkflowJob.external_run_id.is_not(None),
            )
            .order_by(WorkflowJob.id.asc())
            .all()
        )

    def update_prepared(
        self,
        job: WorkflowJob,
        *,
        run_name: str,
        run_root: str,
        results_path: str,
        logs_path: str,
        artifact_manifest_path: str,
        status_payload: dict | None = None,
    ) -> WorkflowJob:
        job.run_name = run_name
        job.run_root = run_root
        job.results_path = results_path
        job.logs_path = logs_path
        job.artifact_manifest_path = artifact_manifest_path
        if status_payload is not None:
            job.status_payload_json = status_payload
        self.db.commit()
        self.db.refresh(job)
        return job

    def mark_submitted(self, job: WorkflowJob, external_run_id: str, status_payload: dict | None = None) -> WorkflowJob:
        job.status = "submitted"
        job.external_run_id = external_run_id
        job.submitted_at = datetime.now(timezone.utc)
        if status_payload is not None:
            job.status_payload_json = status_payload
        self.prov.log(job.study_id, "workflow.submitted", assay_id=job.assay_id, workflow_job_id=job.id, output_ref={"external_run_id": external_run_id})
        self.db.commit()
        self.db.refresh(job)
        return job

    def mark_running(self, job: WorkflowJob, status_payload: dict | None = None) -> WorkflowJob:
        job.status = "running"
        if job.started_at is None:
            job.started_at = datetime.now(timezone.utc)
        if status_payload is not None:
            job.status_payload_json = status_payload
        self.prov.log(job.study_id, "workflow.started", assay_id=job.assay_id, workflow_job_id=job.id)
        self.db.commit()
        self.db.refresh(job)
        return job

    def update_status(self, job: WorkflowJob, status_payload: dict) -> WorkflowJob:
        job.status_payload_json = status_payload
        self.db.commit()
        self.db.refresh(job)
        return job

    def mark_failed(
        self,
        job: WorkflowJob,
        error_message: str,
        status_payload: dict | None = None,
        output_records: list[dict] | None = None,
    ) -> WorkflowJob:
        job.status = "failed"
        job.error_message = error_message
        job.finished_at = datetime.now(timezone.utc)
        if status_payload is not None:
            job.status_payload_json = status_payload
        if output_records:
            self.artifacts.register_output_records(job, output_records)
        self.prov.log(job.study_id, "workflow.finished", assay_id=job.assay_id, workflow_job_id=job.id, output_ref={"status": "failed"})
        self.db.commit()
        self.db.refresh(job)
        return job

    def mark_succeeded(
        self,
        job: WorkflowJob,
        output_records: list[dict],
        *,
        status_payload: dict | None = None,
        result_summary: dict | None = None,
        software_versions: dict | None = None,
    ) -> WorkflowJob:
        job.status = "succeeded"
        job.finished_at = datetime.now(timezone.utc)
        if status_payload is not None:
            job.status_payload_json = status_payload
        generated_file_ids = self.artifacts.register_output_records(job, output_records)
        if result_summary is not None:
            result_set = ResultSet(
                study_id=job.study_id,
                assay_id=job.assay_id,
                workflow_job_id=job.id,
                result_type="workflow_run_summary",
                summary_json=result_summary,
            )
            self.db.add(result_set)
        self.prov.log(
            job.study_id,
            "workflow.finished",
            assay_id=job.assay_id,
            workflow_job_id=job.id,
            output_ref={"status": "succeeded", "generated_file_ids": generated_file_ids},
            software_versions=software_versions or {},
        )
        self.db.commit()
        self.db.refresh(job)
        return job

