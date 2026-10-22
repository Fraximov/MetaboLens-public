from config import WorkerSettings
from launchers.profile_runners.base import RunHandle, nextflow_run_command, start_logged_process
from services.materialized_run import MaterializedRun


class LocalDockerProfileRunner:
    def __init__(self, settings: WorkerSettings):
        self.settings = settings

    def submit(self, run: MaterializedRun) -> RunHandle:
        return start_logged_process(
            run,
            self.build_command(run),
            resume_banner="Continuing workflow with Nextflow -resume",
            run_id_prefix="nf-local",
        )

    def build_command(self, run: MaterializedRun) -> list[str]:
        return nextflow_run_command(run, self.settings.workflow_nextflow_bin, run.work_path)
