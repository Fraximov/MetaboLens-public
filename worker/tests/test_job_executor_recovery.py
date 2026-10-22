import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

from services.job_executor import JobExecutor


class JobExecutorRecoveryTests(unittest.TestCase):
    def _executor(self) -> JobExecutor:
        executor = JobExecutor.__new__(JobExecutor)
        executor.db = Mock()
        executor.settings = SimpleNamespace(
            workflow_slurm_max_active_jobs=3,
        )
        executor.workflow_service = Mock()
        executor.materializer = Mock()
        executor.hpc_smoke_materializer = Mock()
        executor.launcher = Mock()
        executor.status_reader = Mock()
        executor.collector = Mock()
        executor.cleanup = Mock()
        executor._wait_with_status_updates = Mock(return_value=0)
        return executor

    @staticmethod
    def _job(
        *,
        job_id: int = 7,
        status: str = "running",
        external_run_id: str | None = "482913",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=job_id,
            workflow_definition_id=1,
            status=status,
            external_run_id=external_run_id,
            status_payload_json={},
        )

    @staticmethod
    def _run(
        *,
        job_id: int = 7,
        execution_profile: str = "slurm_singularity_ssh",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            job_id=job_id,
            execution_profile=execution_profile,
            run_name=f"job-{job_id}",
            run_root=f"/tmp/job-{job_id}",
            results_path=f"/tmp/job-{job_id}/results",
            logs_path=f"/tmp/job-{job_id}/logs",
            artifact_manifest_path=(
                f"/tmp/job-{job_id}/artifact-manifest.json"
            ),
            nextflow_params={"workflow_stage": "full"},
            engine_execution={},
        )

    def _configure_recovered_job(
        self,
        executor: JobExecutor,
        job: SimpleNamespace,
        run: SimpleNamespace,
        handle: object,
    ) -> None:
        executor.materializer.load_existing.return_value = run
        executor.launcher.attach_slurm.return_value = handle
        executor.launcher.submit.side_effect = AssertionError(
            "Recovery must not submit another job"
        )
        executor.launcher.command_for.return_value = [
            "slurm-job",
            str(job.external_run_id),
        ]
        executor.status_reader.build_status_payload.side_effect = (
            lambda _run, phase, **_: {"phase": phase}
        )

    def _configure_success(
        self,
        executor: JobExecutor,
        job: SimpleNamespace,
    ) -> None:
        collection = SimpleNamespace(
            status_payload={"phase": "succeeded"},
            output_records=[],
            result_summary={},
            software_versions={},
        )
        executor.collector.collect.return_value = collection
        executor.workflow_service.mark_succeeded.return_value = job
        executor.cleanup.remove_staged_raw_inputs.return_value = {
            "staged_inputs_removed": True,
        }

    def test_recovered_running_job_is_polled_once_without_resubmitting(
        self,
    ) -> None:
        executor = self._executor()
        job = self._job()
        run = self._run()
        handle = object()
        self._configure_recovered_job(executor, job, run, handle)
        executor.launcher.poll.return_value = None

        still_active = executor._process_job(job, recovering=True)

        self.assertTrue(still_active)
        executor.materializer.load_existing.assert_called_once_with(job)
        executor.launcher.attach_slurm.assert_called_once_with(
            run,
            "482913",
        )
        executor.launcher.submit.assert_not_called()
        executor.launcher.poll.assert_called_once_with(handle)
        executor.launcher.wait.assert_not_called()
        executor._wait_with_status_updates.assert_not_called()

    def test_recovered_completed_job_is_finalized_without_resubmitting(
        self,
    ) -> None:
        executor = self._executor()
        job = self._job()
        run = self._run()
        handle = object()
        self._configure_recovered_job(executor, job, run, handle)
        self._configure_success(executor, job)
        executor.launcher.poll.return_value = 0
        executor.launcher.wait.return_value = 0

        still_active = executor._process_job(job, recovering=True)

        self.assertFalse(still_active)
        executor.launcher.submit.assert_not_called()
        executor.launcher.poll.assert_called_once_with(handle)
        executor.launcher.wait.assert_called_once_with(handle)
        executor.workflow_service.mark_succeeded.assert_called_once()

    def test_engine_review_stages_retain_inputs_until_finalization(self):
        for stage, retain in (("is_qc_review", True), ("qc_preview", True), ("third_engine_review", True), ("full", False)):
            with self.subTest(stage=stage):
                executor = self._executor()
                job = self._job()
                run = self._run()
                run.nextflow_params["workflow_stage"] = stage
                run.engine_execution = {"descriptor": {"review_transitions": {
                    "is_qc_review": "qc_preview", "qc_preview": "full", "third_engine_review": "full"}}}
                self._configure_recovered_job(executor, job, run, object())
                self._configure_success(executor, job)
                executor.launcher.poll.return_value = 0
                executor.launcher.wait.return_value = 0
                executor._process_job(job, recovering=True)
                executor.workflow_service.mark_succeeded.assert_called_once()
                self.assertEqual(executor.cleanup.remove_staged_raw_inputs.called, not retain)

    def test_recovered_failed_job_is_finalized_without_resubmitting(
        self,
    ) -> None:
        executor = self._executor()
        job = self._job()
        run = self._run()
        handle = object()
        self._configure_recovered_job(executor, job, run, handle)
        self._configure_success(executor, job)
        executor.launcher.poll.return_value = 1
        executor.launcher.wait.return_value = 1

        still_active = executor._process_job(job, recovering=True)

        self.assertFalse(still_active)
        executor.launcher.submit.assert_not_called()
        executor.workflow_service.mark_failed.assert_called_once()
        executor.workflow_service.mark_succeeded.assert_not_called()

    def test_cycle_counts_reconciliation_error_and_continues(self) -> None:
        executor = self._executor()
        executor.settings.workflow_slurm_max_active_jobs = 2
        unavailable_job = self._job(job_id=7)
        running_job = self._job(job_id=8, external_run_id="482914")
        running_run = self._run(job_id=8)
        running_handle = object()
        executor.workflow_service.list_active_slurm_jobs.return_value = [
            unavailable_job,
            running_job,
        ]
        executor.workflow_service.claim_next_job.side_effect = AssertionError(
            "Unresolved drivers must continue to count against the cap"
        )
        executor.materializer.load_existing.side_effect = [
            OSError("shared mount unavailable"),
            running_run,
        ]
        executor.launcher.attach_slurm.return_value = running_handle
        executor.launcher.command_for.return_value = [
            "slurm-job",
            "482914",
        ]
        executor.launcher.poll.return_value = None
        executor.status_reader.build_status_payload.side_effect = (
            lambda _run, phase, **_: {"phase": phase}
        )

        with self.assertLogs(level="ERROR"):
            worked = executor.process_cycle()

        self.assertTrue(worked)
        executor.launcher.attach_slurm.assert_called_once_with(
            running_run,
            "482914",
        )
        executor.launcher.poll.assert_called_once_with(running_handle)
        executor.workflow_service.mark_failed.assert_not_called()
        executor.workflow_service.claim_next_job.assert_not_called()
        executor.db.rollback.assert_called_once_with()

    def test_cycle_continues_when_terminal_status_cannot_be_saved(
        self,
    ) -> None:
        executor = self._executor()
        completed_job = self._job(job_id=7)
        running_job = self._job(job_id=8, external_run_id="482914")
        completed_run = self._run(job_id=7)
        running_run = self._run(job_id=8)
        completed_handle = object()
        running_handle = object()
        executor.workflow_service.list_active_slurm_jobs.return_value = [
            completed_job,
            running_job,
        ]
        executor.workflow_service.claim_next_job.return_value = None
        executor.materializer.load_existing.side_effect = [
            completed_run,
            running_run,
        ]
        executor.launcher.attach_slurm.side_effect = [
            completed_handle,
            running_handle,
        ]
        executor.launcher.command_for.return_value = ["slurm-job"]
        executor.launcher.poll.side_effect = [0, None]
        executor.launcher.wait.return_value = 0
        executor.status_reader.build_status_payload.side_effect = (
            lambda _run, phase, **_: {"phase": phase}
        )
        self._configure_success(executor, completed_job)
        executor.workflow_service.mark_succeeded.side_effect = RuntimeError(
            "database unavailable"
        )
        executor.workflow_service.mark_failed.side_effect = RuntimeError(
            "database unavailable"
        )

        with self.assertLogs(level="ERROR"):
            worked = executor.process_cycle()

        self.assertTrue(worked)
        self.assertEqual(executor.launcher.poll.call_count, 2)
        executor.launcher.poll.assert_has_calls(
            [call(completed_handle), call(running_handle)]
        )
        executor.workflow_service.claim_next_job.assert_called_once_with(
            execution_profile="slurm_singularity_ssh"
        )
        executor.db.rollback.assert_called_once_with()

    def test_cycle_polls_active_jobs_and_submits_one_more_below_limit(
        self,
    ) -> None:
        executor = self._executor()
        running_job = self._job(job_id=7)
        queued_job = self._job(
            job_id=8,
            status="queued",
            external_run_id=None,
        )
        executor.workflow_service.list_active_slurm_jobs.return_value = [
            running_job
        ]
        executor.workflow_service.claim_next_job.return_value = queued_job
        executor._process_job = Mock(side_effect=[True, True])

        worked = executor.process_cycle()

        self.assertTrue(worked)
        executor._process_job.assert_has_calls(
            [
                call(running_job, recovering=True),
                call(queued_job, recovering=False),
            ]
        )
        executor.workflow_service.claim_next_job.assert_called_once_with(
            execution_profile="slurm_singularity_ssh"
        )

    def test_cycle_does_not_claim_above_active_job_limit(self) -> None:
        executor = self._executor()
        active_jobs = [
            self._job(job_id=7),
            self._job(job_id=8),
            self._job(job_id=9),
        ]
        executor.workflow_service.list_active_slurm_jobs.return_value = (
            active_jobs
        )
        executor._process_job = Mock(return_value=True)

        worked = executor.process_cycle()

        self.assertTrue(worked)
        self.assertEqual(executor._process_job.call_count, 3)
        executor.workflow_service.claim_next_job.assert_not_called()

    def test_cycle_terminal_jobs_free_capacity_in_same_cycle(self) -> None:
        executor = self._executor()
        completed_job = self._job(job_id=7)
        failed_job = self._job(job_id=8)
        running_job = self._job(job_id=9)
        queued_job = self._job(
            job_id=10,
            status="queued",
            external_run_id=None,
        )
        executor.workflow_service.list_active_slurm_jobs.return_value = [
            completed_job,
            failed_job,
            running_job,
        ]
        executor.workflow_service.claim_next_job.return_value = queued_job
        executor._process_job = Mock(
            side_effect=[False, False, True, True]
        )

        worked = executor.process_cycle()

        self.assertTrue(worked)
        executor._process_job.assert_has_calls(
            [
                call(completed_job, recovering=True),
                call(failed_job, recovering=True),
                call(running_job, recovering=True),
                call(queued_job, recovering=False),
            ]
        )
        executor.workflow_service.claim_next_job.assert_called_once_with(
            execution_profile="slurm_singularity_ssh"
        )

    def test_new_local_job_keeps_blocking_wait(self) -> None:
        executor = self._executor()
        job = self._job(
            job_id=8,
            status="preparing",
            external_run_id=None,
        )
        run = self._run(job_id=8, execution_profile="local_docker")
        handle = object()
        definition = object()
        executor.db.query.return_value.filter.return_value.first.return_value = (
            definition
        )
        executor.hpc_smoke_materializer.is_smoke_job.return_value = False
        executor.materializer.prepare.return_value = run
        executor.launcher.submit.return_value = handle
        executor.launcher.external_run_id_for.return_value = "nf-local-8"
        executor.launcher.command_for.return_value = ["nextflow", "run"]
        executor.status_reader.build_status_payload.side_effect = (
            lambda _run, phase, **_: {"phase": phase}
        )
        self._configure_success(executor, job)

        still_active = executor._process_job(job, recovering=False)

        self.assertFalse(still_active)
        executor.launcher.submit.assert_called_once_with(run)
        executor._wait_with_status_updates.assert_called_once_with(
            job,
            run,
            handle,
            "nf-local-8",
        )
        executor.workflow_service.mark_succeeded.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
