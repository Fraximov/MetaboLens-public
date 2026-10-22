import logging
import time
from pathlib import Path

from app.models.models import WorkflowDefinition
from app.services.workflow_job_state_service import WorkflowJobStateService

from config import get_settings
from launchers.mock_run import MockCompletedRun, write_mock_outputs
from launchers.nextflow_launcher import NextflowLauncher
from services.artifact_collector import ArtifactCollector
from services.hpc_smoke_materializer import HpcSmokeMaterializer
from services.materialized_run import MaterializedRun
from services.run_cleanup import RunCleanupService
from services.run_materializer import RunMaterializer
from services.status_reader import StatusReader


class JobExecutor:
    def __init__(self, db):
        self.db = db
        self.settings = get_settings()
        self.workflow_service = WorkflowJobStateService(db)
        self.materializer = RunMaterializer(db, self.settings)
        self.hpc_smoke_materializer = HpcSmokeMaterializer(self.settings)
        self.launcher = NextflowLauncher(self.settings)
        self.status_reader = StatusReader()
        self.collector = ArtifactCollector()
        self.cleanup = RunCleanupService()

    def process_cycle(self) -> bool:
        """Reconcile detached jobs, then start at most one queued job."""

        active_jobs = self.workflow_service.list_active_slurm_jobs()

        # ponytail: Sequential polling is enough for the small configured cap;
        # parallelize only if measured SSH latency makes a cycle too slow.
        active_count = 0
        for job in active_jobs:
            if self._process_job(job, recovering=True):
                active_count += 1

        max_active = max(
            1,
            int(self.settings.workflow_slurm_max_active_jobs),
        )
        if active_count >= max_active:
            return bool(active_jobs)

        execution_profile = (
            "slurm_singularity_ssh" if active_count else None
        )
        job = self.workflow_service.claim_next_job(
            execution_profile=execution_profile
        )
        if job is None:
            return bool(active_jobs)

        self._process_job(job, recovering=False)
        return True

    def _process_job(self, job, *, recovering: bool) -> bool:
        """Process one job and report whether it remains detached and active.

        A recovering job is a detached SLURM job submitted in an earlier cycle:
        reattach, poll it once, and finish it if the scheduler says it ended. A
        new job is materialized and submitted; a local run is then waited for here.
        """

        run: MaterializedRun | None = None
        external_run_id: str | None = None
        scheduler_terminal = False
        try:
            if recovering:
                run = self.materializer.load_existing(job)
                external_run_id = str(job.external_run_id)
                handle = self.launcher.attach_slurm(run, external_run_id)
                running_status = self._status(run, "running", external_run_id, handle)
                running_status["worker_reconnected"] = True
                if job.status == "submitted":
                    self.workflow_service.mark_running(job, running_status)
                else:
                    self.workflow_service.update_status(job, running_status)

                if self.launcher.poll(handle) is None:
                    return True
                scheduler_terminal = True
                exit_code = self.launcher.wait(handle)
            else:
                run = self._materialize(job)
                self.workflow_service.update_prepared(
                    job,
                    run_name=run.run_name,
                    run_root=run.run_root,
                    results_path=run.results_path,
                    logs_path=run.logs_path,
                    artifact_manifest_path=run.artifact_manifest_path,
                    status_payload=self.status_reader.build_status_payload(run, phase="preparing"),
                )

                handle = self.launcher.submit(run)
                external_run_id = self.launcher.external_run_id_for(handle)
                self.workflow_service.mark_submitted(
                    job, external_run_id, self._status(run, "submitted", external_run_id, handle),
                )
                self.workflow_service.mark_running(job, self._status(run, "running", external_run_id, handle))

                if run.execution_profile == "slurm_singularity_ssh":
                    return True  # Detached: later cycles reconcile it.
                exit_code = self._wait_with_status_updates(job, run, handle, external_run_id)

            if isinstance(handle, MockCompletedRun):
                write_mock_outputs(run)
            self._finish(job, run, external_run_id, exit_code)
        except Exception as exc:  # noqa: BLE001
            if recovering and not scheduler_terminal:
                logging.exception("workflow job %s reconciliation failed; will retry", job.id)
                self.db.rollback()
                return True

            logging.exception("workflow job %s failed during worker execution", job.id)
            self._record_failure(job, run, external_run_id, exc)
        return False

    def _materialize(self, job) -> MaterializedRun:
        """Write the run folder for a newly claimed job."""

        if not self.hpc_smoke_materializer.is_smoke_job(job):
            return self.materializer.prepare(job)
        definition = self.db.query(WorkflowDefinition).filter(WorkflowDefinition.id == job.workflow_definition_id).first()
        if definition is None:
            raise ValueError(f"Workflow definition not found for job {job.id}")
        return self.hpc_smoke_materializer.prepare(job, definition)

    def _status(self, run: MaterializedRun, phase: str, external_run_id: str | None, handle) -> dict:
        """Build a status payload for a submitted run, including the command that runs it."""

        payload = self.status_reader.build_status_payload(run, phase=phase, external_run_id=external_run_id)
        payload["command"] = self.launcher.command_for(handle)
        return payload

    def _finish(self, job, run: MaterializedRun, external_run_id: str | None, exit_code: int) -> None:
        """Register the run's outputs and record whether it succeeded."""

        error_message = None if exit_code == 0 else self._extract_error_message(run)
        terminal_status = self.status_reader.build_status_payload(
            run,
            phase="succeeded" if exit_code == 0 else "failed",
            external_run_id=external_run_id,
            exit_code=exit_code,
            error_message=error_message,
        )
        collection = self.collector.collect(run, status_payload=terminal_status, exit_code=exit_code)
        if exit_code != 0:
            self.workflow_service.mark_failed(
                job,
                error_message,
                collection.status_payload,
                output_records=collection.output_records,
            )
            return

        succeeded_job = self.workflow_service.mark_succeeded(
            job,
            collection.output_records,
            status_payload=collection.status_payload,
            result_summary=collection.result_summary,
            software_versions=collection.software_versions,
        )
        cleanup_payload = self._remove_staged_inputs_unless_needed(run)
        self.workflow_service.update_status(succeeded_job, {**(succeeded_job.status_payload_json or collection.status_payload), "cleanup": cleanup_payload})

    def _remove_staged_inputs_unless_needed(self, run: MaterializedRun) -> dict:
        """Delete staged RAW copies unless a review continuation or an operator still needs them."""

        review_stages = run.engine_execution.get("descriptor", {}).get("review_transitions", {})
        stage = run.nextflow_params.get("workflow_stage")
        if stage == "hpc_smoke" or stage in review_stages:
            return {
                "staged_inputs_removed": False,
                "cleanup_note": "Retained staged inputs for review/resume or diagnostic inspection.",
            }
        return self.cleanup.remove_staged_raw_inputs(run)

    def _record_failure(self, job, run: MaterializedRun | None, external_run_id: str | None, exc: Exception) -> None:
        """Mark the job failed, registering whatever outputs the run left behind."""

        try:
            failure_payload = None
            output_records = None
            if run is not None:
                failure_payload = self.status_reader.build_status_payload(
                    run,
                    phase="failed",
                    external_run_id=external_run_id,
                    error_message=str(exc),
                )
                try:
                    collection = self.collector.collect(run, status_payload=failure_payload, exit_code=1)
                    failure_payload = collection.status_payload
                    output_records = collection.output_records
                except Exception:  # noqa: BLE001
                    pass
            self.workflow_service.mark_failed(job, str(exc), failure_payload, output_records=output_records)
        except Exception:  # noqa: BLE001
            logging.exception(
                "workflow job %s failure state could not be recorded",
                job.id,
            )
            self.db.rollback()

    def _wait_with_status_updates(self, job, run: MaterializedRun, handle, external_run_id: str | None) -> int:
        if isinstance(handle, MockCompletedRun):
            return self.launcher.wait(handle)

        poll_seconds = max(1, int(self.settings.workflow_nextflow_poll_seconds))
        update_seconds = max(poll_seconds, int(self.settings.workflow_status_update_seconds))
        last_update_at = time.monotonic()

        while True:
            exit_code = self.launcher.poll(handle)
            if exit_code is not None:
                return self.launcher.wait(handle)

            now = time.monotonic()
            if now - last_update_at >= update_seconds:
                self.workflow_service.update_status(job, self._status(run, "running", external_run_id, handle))
                last_update_at = now

            time.sleep(poll_seconds)

    @staticmethod
    def _extract_error_message(run: MaterializedRun) -> str:
        error_path = Path(run.logs_path) / "error.txt"
        if error_path.exists() and error_path.stat().st_size > 0:
            return error_path.read_text(encoding="utf-8", errors="ignore").strip()[:2000]
        execution_log_path = Path(run.logs_path) / "execution.log"
        if execution_log_path.exists() and execution_log_path.stat().st_size > 0:
            lines = execution_log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            return "\n".join(lines[-20:]).strip()[:2000] or "Workflow execution failed"
        return "Workflow execution failed"
