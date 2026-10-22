import re
import shlex
from pathlib import Path

from config import WorkerSettings
from launchers.profile_runners.base import RunHandle, nextflow_run_command, start_logged_process
from services.materialized_run import MaterializedRun


class SlurmSingularityProfileRunner:
    """Submit one Nextflow driver job to SLURM and let Nextflow schedule tasks.

    This runner is deliberately a thin worker-side adapter. The backend has
    already validated permissions and queued a `WorkflowJob`; `RunMaterializer`
    has already converted that job into a `MaterializedRun` with concrete paths
    and Nextflow parameters. This class only turns that run contract plus worker
    environment settings into an auditable `contract/run_nextflow.sbatch`.

    Parameter origin map:
    - `run.*`: per-job values from the materialized workflow contract.
    - `run.nextflow_params`: pipeline parameters written at materialization
      from launch parameters, defaults, and staged files.
    - `self.settings.*`: worker deployment settings from environment variables.
    """


    def __init__(self, settings: WorkerSettings):
        """Keep deployment settings separate from the per-job run contract."""

        self.settings = settings

    def submit(self, run: MaterializedRun) -> RunHandle:
        """Write the batch script and start `sbatch --wait` as the tracked process."""

        script_path = self.write_sbatch_script(run)
        return start_logged_process(
            run,
            self.build_command(script_path),
            resume_banner="Continuing workflow through SLURM with Nextflow -resume",
            run_id_prefix="slurm-sbatch",
        )

    def build_command(self, script_path: Path) -> list[str]:
        """Build the host command that submits the generated SLURM script.

        The `sbatch` binary path comes from worker settings so cluster-specific
        installations can override it without changing job contracts.
        """

        return [self.settings.workflow_slurm_sbatch_bin, "--wait", str(script_path)]

    def write_sbatch_script(self, run: MaterializedRun, script_run: MaterializedRun | None = None) -> Path:
        """Persist the generated script inside the job's auditable contract dir.

        `script_run` supplies the paths written into the script when they differ
        from the worker's own view of the run (the SSH runner's cluster paths).
        """

        script_path = Path(run.contract_dir) / "run_nextflow.sbatch"
        script_path.write_text(self.build_sbatch_script(script_run or run), encoding="utf-8")
        script_path.chmod(0o750)
        return script_path

    def build_sbatch_script(self, run: MaterializedRun) -> str:
        """Render the SLURM driver script for one materialized workflow run.

        SLURM resource lines are deployment settings, while paths and Nextflow
        arguments come from `run`. Keeping both in the generated script makes
        the exact cluster submission inspectable after the job finishes.
        """

        work_dir = self.work_dir_for(run)
        nextflow_command = self.build_nextflow_command(run, work_dir)
        # Driver job resources come from WorkerSettings/env vars, not the
        # workflow launch form. They size only the small Nextflow manager job.
        lines = [
            "#!/bin/bash -l",
            f"#SBATCH --job-name={self.slurm_job_name(run)}",
            f"#SBATCH --output={self._quote(str(Path(run.logs_path) / 'slurm-nextflow-%j.out'))}",
            f"#SBATCH --error={self._quote(str(Path(run.logs_path) / 'slurm-nextflow-%j.err'))}",
            f"#SBATCH --cpus-per-task={int(self.settings.workflow_slurm_driver_cpus)}",
            f"#SBATCH --mem={self.settings.workflow_slurm_driver_memory}",
            f"#SBATCH --time={self.settings.workflow_slurm_driver_time}",
        ]
        if self.settings.workflow_slurm_partition:
            lines.append(f"#SBATCH --partition={self.settings.workflow_slurm_partition}")
        if self.settings.workflow_slurm_account:
            lines.append(f"#SBATCH --account={self.settings.workflow_slurm_account}")

        # Runtime paths come from MaterializedRun: the worker created these
        # folders from the queued WorkflowJob before this runner is called.
        lines.extend([
            "",
            "set -euo pipefail",
            f"cd {self._quote(run.run_root)}",
            f"mkdir -p {self._quote(str(work_dir))} {self._quote(run.results_path)} {self._quote(run.logs_path)}",
        ])
        # Cluster software/cache choices are worker deployment settings. They
        # are intentionally outside the user-facing workflow parameters.
        if self.settings.workflow_slurm_nextflow_module:
            lines.append(f"module load {shlex.quote(self.settings.workflow_slurm_nextflow_module)}")
        if self.settings.workflow_slurm_container_cache_dir:
            cache_dir = self.settings.workflow_slurm_container_cache_dir
            lines.append(f"mkdir -p {self._quote(cache_dir)}")
            lines.append(f"export APPTAINER_CACHEDIR={self._quote(cache_dir)}")
            lines.append('export SINGULARITY_CACHEDIR="${APPTAINER_CACHEDIR}"')
        if self.settings.workflow_slurm_container_tmp_dir:
            tmp_dir = self.settings.workflow_slurm_container_tmp_dir
            lines.append(f"mkdir -p {self._quote(tmp_dir)}")
            lines.append(f"export APPTAINER_TMPDIR={self._quote(tmp_dir)}")
            lines.append('export SINGULARITY_TMPDIR="${APPTAINER_TMPDIR}"')
        lines.extend([
            "export NXF_ANSI_LOG=false",
            "",
            shlex.join(nextflow_command),
            "",
        ])
        return "\n".join(lines)

    def build_nextflow_command(self, run: MaterializedRun, work_dir: Path | None = None) -> list[str]:
        """Build the `nextflow run` command embedded in the batch script.

        `work_dir` defaults to the scratch-backed path from
        `WORKFLOW_SLURM_WORK_ROOT` when configured.
        """

        return nextflow_run_command(run, self.settings.workflow_nextflow_bin, str(work_dir or self.work_dir_for(run)))

    def work_dir_for(self, run: MaterializedRun) -> Path:
        """Choose scratch-backed Nextflow work storage when configured."""

        if self.settings.workflow_slurm_work_root:
            return Path(self.settings.workflow_slurm_work_root).expanduser() / f"job_{run.job_id}" / "work"
        return Path(run.work_path)

    @staticmethod
    def slurm_job_name(run: MaterializedRun) -> str:
        """Derive a SLURM-safe job name from the worker's stable run name."""

        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", run.run_name).strip("_")
        suffix = f"_attempt_{run.submission_attempt}" if run.submission_attempt else ""
        return f"mlens_{safe or run.job_id}"[:128 - len(suffix)] + suffix

    @staticmethod
    def _quote(value: str) -> str:
        """Shell-quote generated script values that may contain spaces."""

        return shlex.quote(value)
