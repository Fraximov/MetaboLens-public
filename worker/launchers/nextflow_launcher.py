import time
import logging
from dataclasses import dataclass

from config import WorkerSettings
from launchers.mock_run import MockCompletedRun
from launchers.profile_runners.base import RunHandle
from launchers.profile_runners.local_docker import LocalDockerProfileRunner
from launchers.profile_runners.slurm_singularity import SlurmSingularityProfileRunner
from launchers.profile_runners.slurm_singularity_ssh import (SlurmSingularitySshProfileRunner, SlurmStatusUnavailable)
from services.materialized_run import MaterializedRun


@dataclass
class SlurmRunHandle:
    """Durable handle for a SLURM job submitted via SSH, which can be used to poll or wait for the job's completion."""
    run: MaterializedRun
    external_run_id: str
    command: list[str]
    exit_code: int | None = None


class NextflowLauncher:
    def __init__(self, settings: WorkerSettings):
        self.settings = settings
        self.local_runner = LocalDockerProfileRunner(settings)
        self.slurm_runner = SlurmSingularityProfileRunner(settings)
        self.slurm_ssh_runner = SlurmSingularitySshProfileRunner(settings)

    def submit(self, run: MaterializedRun) -> RunHandle | MockCompletedRun | SlurmRunHandle:
        if self.settings.nextflow_mode.strip().lower() == "mock":
            time.sleep(1)
            return MockCompletedRun(external_run_id=f"nf-mock-{run.job_id}", exit_code=0)
        if run.execution_profile == "local_docker":
            return self.local_runner.submit(run)
        if run.execution_profile == "slurm_singularity":
            return self.slurm_runner.submit(run)
        if run.execution_profile == "slurm_singularity_ssh":
            job_id = self.slurm_ssh_runner.submit_detached(run)

            return SlurmRunHandle(
                run=run,
                external_run_id=job_id,
                command=["slurm-job", job_id],
            )
        raise ValueError(f"Unsupported execution_profile for current worker: {run.execution_profile}")

    @staticmethod
    def attach_slurm(
        run: MaterializedRun,
        external_run_id: str,
    ) -> SlurmRunHandle:
        """Create a handle for an already-submitted SLURM job."""

        if run.execution_profile != "slurm_singularity_ssh":
            raise ValueError(
                "Only slurm_singularity_ssh runs can be attached"
            )

        job_id = str(external_run_id).strip()
        if not job_id.isdigit():
            raise ValueError(
                f"Invalid SLURM job ID: {external_run_id!r}"
            )

        return SlurmRunHandle(
            run=run,
            external_run_id=job_id,
            command=["slurm-job", job_id],
        )

    def poll(
        self,
        handle: RunHandle | SlurmRunHandle | MockCompletedRun,
    ) -> int | None:
        if isinstance(handle, MockCompletedRun):
            return handle.exit_code

        if isinstance(handle, SlurmRunHandle):
            try:
                status = self.slurm_ssh_runner.query_job_status(
                    handle.external_run_id
                )
            except SlurmStatusUnavailable as exc:
                logging.warning(
                    "SLURM job %s status temporarily unavailable: %s",
                    handle.external_run_id,
                    exc,
                )
                return None

            if status is None or not status.terminal:
                return None

            handle.exit_code = (
                status.exit_code
                if status.exit_code is not None
                else 1
            )
            return handle.exit_code

        return handle.process.poll()

    @staticmethod
    def wait(handle: RunHandle | MockCompletedRun | SlurmRunHandle) -> int:
        if isinstance(handle, MockCompletedRun):
            return handle.exit_code
        if isinstance(handle, SlurmRunHandle):
            if handle.exit_code is None:
               raise RuntimeError("Cannot wait for a non-terminal SLURM job")
            return handle.exit_code

        try:
            return handle.process.wait()
        finally:
            handle.execution_log_handle.close()

    @staticmethod
    def command_for(handle: RunHandle | MockCompletedRun | SlurmRunHandle) -> list[str]:
        if isinstance(handle, MockCompletedRun):
            return ["mock"]
        return handle.command

    @staticmethod
    def external_run_id_for(handle: RunHandle | MockCompletedRun | SlurmRunHandle) -> str:
        return handle.external_run_id
