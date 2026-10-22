import shlex
import subprocess
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from launchers.profile_runners.base import RunHandle, start_logged_process
from launchers.profile_runners.slurm_singularity import SlurmSingularityProfileRunner
from services.materialized_run import MaterializedRun
from services.run_workspace import atomic_write_text

def parse_sbatch_job_id(output: str) -> str:
    """Extract the numeric SLURM Job ID from "sbatch --parsable" output, ignoring any cluster suffixes."""

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise ValueError("No output from sbatch command to parse job ID")
    job_id = lines[-1].split(';')[0]  # Take the last line and split by ';' to ignore any cluster suffix
    if not job_id.isdigit():
        raise ValueError(f"Invalid job ID extracted from sbatch output: '{job_id}'")
    return job_id

def parse_slurm_job_ids_by_name(output: str,job_name: str,) -> list[str]:
    """Extract unique numeric job IDs with an exact job-name match."""

    job_ids: list[str] = []

    for line in output.splitlines():
        columns = [
            column.strip()
            for column in line.split("|")
        ]
        if len(columns) < 2:
            continue

        job_id, row_job_name = columns[:2]
        if row_job_name != job_name:
            continue
        if not job_id.isdigit():
            continue
        if job_id not in job_ids:
            job_ids.append(job_id)

    return job_ids
@dataclass(frozen=True)
class SlurmJobStatus:
    """Represents the status of a SLURM job."""

    state: str
    terminal: bool
    exit_code: int | None = None


class SlurmStatusUnavailable(RuntimeError):
    """The scheduler could not be queried, but the job may still be running."""
class SlurmSubmissionUncertain(RuntimeError):
    """Submission may have succeeded, so retrying could duplicate the job."""

def normalize_slurm_state(value: str) -> str:
    """Normalize SLURM state strings"""

    return value.strip().split(maxsplit=1)[0].rstrip("+").upper()  # Take the first word and convert to uppercase


def parse_squeue_status(output: str) -> SlurmJobStatus | None:
    """Interpret the first non empty state returned by squeue for a given job ID, returning None if the job is not found."""

    for line in output.splitlines():
        state = normalize_slurm_state(line)
        if state:
            return SlurmJobStatus(
                state=state,
                terminal = False,
                exit_code= None,
            )
    return None  # Job not found in squeue output


SLURM_SUCCESS_STATES = {"COMPLETED"}
SLURM_FAILURE_STATES = {"FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "DEADLINE", "OUT_OF_MEMORY"}


def parse_slurm_exit_code(value: str) -> int:
    """Extract the exit:signal portio of SLURM signal representation, return 1 for failure if not found."""

    process_code = value.strip().split(":",1)[0]  # Take the part before the colon
    try:
        return int(process_code)
    except ValueError:
        return 1  # Default to 1 for failure if parsing fails

def parse_sacct_status(output: str,job_id: str) -> SlurmJobStatus | None:
    """Read the top-level job row from sacct parsed output and return a SlurmJobStatus, or None if the job is not found."""

    for line in output.splitlines():
        columns = [column.strip() for column in line.split("|")]
        if len(columns) < 3:
            continue  # Not enough columns to parse

        row_job_id, raw_state, raw_exit_code = columns[:3]
        if row_job_id != job_id:
            continue

        state = normalize_slurm_state(raw_state)

        if state in SLURM_SUCCESS_STATES:
            exit_code = parse_slurm_exit_code(raw_exit_code)
            return SlurmJobStatus(state=state, terminal=True, exit_code=exit_code)
        elif state in SLURM_FAILURE_STATES:
            exit_code = parse_slurm_exit_code(raw_exit_code)
            return SlurmJobStatus(state=state, terminal=True, exit_code=exit_code if exit_code != 0 else 1)

        return SlurmJobStatus(state=state, terminal=False, exit_code=None)


    return None  # Job not found in sacct output


class SlurmSingularitySshProfileRunner(SlurmSingularityProfileRunner):
    """Submit the generated SLURM driver script through SSH.

    This runner is for the first outside-cluster MetaboLens prototype. It still
    writes the job contract to a locally visible group-share mount, but the
    generated script contains the cluster-visible paths. SSH is used only to run
    `sbatch --wait` from a login node.

    It intentionally does not copy files. Configure mount path mapping instead:
    - local: `/Volumes/example-lab`
    - remote: `/g/example-lab`
    """

    def _ssh(self, remote_command: str) -> list[str]:
        """Wrap one shell command line for the login node in the configured SSH transport."""

        if not self.settings.workflow_slurm_ssh_host:
            raise ValueError("WORKFLOW_SLURM_SSH_HOST is required for slurm_singularity_ssh")
        return [
            self.settings.workflow_slurm_ssh_bin,
            *shlex.split(self.settings.workflow_slurm_ssh_options or ""),
            self.ssh_target(),
            remote_command,
        ]

    def build_scheduler_query_command(
        self,
        remote_arguments: list[str],
    ) -> list[str]:
        """Wrap one scheduler command in the configured SSH transport."""

        return self._ssh(shlex.join(remote_arguments))

    def build_squeue_query_command(
        self,
        job_id: str,
    ) -> list[str]:
        """Build the query used while a job remains active."""

        return self.build_scheduler_query_command(
            [
                "squeue",
                "-h",
                "-j",
                job_id,
                "-o",
                "%T",
            ]
        )

    def build_squeue_name_query_command(
        self,
        job_name: str,
    ) -> list[str]:
        """Build the active-job lookup for a deterministic name."""

        return self.build_scheduler_query_command(
            [
                "squeue",
                "-h",
                "--name",
                job_name,
                "--format",
                "%i|%j",
            ]
        )


    def build_sacct_name_query_command(
        self,
        job_name: str,
    ) -> list[str]:
        """Build the accounting lookup for a deterministic name."""

        return self.build_scheduler_query_command(
            [
                "sacct",
                "-n",
                "-X",
                "--name",
                job_name,
                "--format=JobIDRaw,JobName",
                "--parsable2",
            ]
        )

    def build_sacct_query_command(
        self,
        job_id: str,
    ) -> list[str]:
        """Build the accounting query used after a job leaves squeue."""

        return self.build_scheduler_query_command(
            [
                "sacct",
                "-n",
                "-X",
                "-j",
                job_id,
                "--format=JobIDRaw,State,ExitCode",
                "--parsable2",
            ]
        )

    def run_scheduler_query(
        self,
        command: list[str],
        *,
        missing_job_is_empty: bool = False,
    ) -> str:
        """Execute one read-only scheduler query through SSH."""
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=(
                    self.settings
                    .workflow_slurm_ssh_command_timeout_seconds
                ),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SlurmStatusUnavailable(
                "SLURM SSH status query timed out"
            ) from exc

        if completed.returncode != 0:
            message = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or "SSH status query failed"
            )
            if (
                missing_job_is_empty
                and "invalid job id specified" in message.lower()
            ):
                return ""
            raise SlurmStatusUnavailable(
                f"SLURM SSH status query failed: {message}"
            )

        return completed.stdout

    def query_job_status(
        self,
        job_id: str,
    ) -> SlurmJobStatus | None:
        """Return the latest known scheduler state for one SLURM job."""

        if not job_id.isdigit():
            raise ValueError(
                f"SLURM job ID must be numeric: {job_id!r}"
            )

        squeue_output = self.run_scheduler_query(
            self.build_squeue_query_command(job_id),
            missing_job_is_empty=True,
        )
        active_status = parse_squeue_status(squeue_output)
        if active_status is not None:
            return active_status

        sacct_output = self.run_scheduler_query(
            self.build_sacct_query_command(job_id)
        )
        return parse_sacct_status(
            sacct_output,
            job_id,
        )

    def find_job_id_by_name(
        self,
        job_name: str,
    ) -> str | None:
        """Find one unambiguous scheduler job by name."""

        commands = [
            self.build_squeue_name_query_command(job_name),
            self.build_sacct_name_query_command(job_name),
        ]

        for command in commands:
            output = self.run_scheduler_query(command)
            job_ids = parse_slurm_job_ids_by_name(
                output,
                job_name,
            )

            if len(job_ids) > 1:
                raise SlurmSubmissionUncertain(
                    "Multiple SLURM jobs have the expected "
                    f"name {job_name!r}: {job_ids}"
                )

            if job_ids:
                return job_ids[0]

        return None

    def submit(self, run: MaterializedRun) -> RunHandle:
        """Submit over SSH and keep the connection open until the job ends.

        The worker uses `submit_detached`; this blocking path serves
        `tools/slurm_singularity_smoke.py --submit`.
        """

        remote_run = self.remote_run_for(run)
        script_path = self.write_sbatch_script(run, remote_run)
        return start_logged_process(
            run,
            self.build_command(remote_run, script_path),
            resume_banner="Continuing workflow through SSH + SLURM with Nextflow -resume",
            run_id_prefix="slurm-ssh",
        )

    def build_command(self, remote_run: MaterializedRun, script_path: Path | None = None) -> list[str]:
        """Build the local SSH command that submits the remote batch script.

        Args:
            remote_run: Materialized run after local mount paths have been
                translated to cluster-visible paths.
            script_path: Present for signature clarity in tests; the remote
                command submits the script by its path relative to `run_root`.
        """

        return self._ssh(
            f"cd {shlex.quote(remote_run.run_root)} && "
            f"{shlex.join([self.settings.workflow_slurm_sbatch_bin, '--wait', 'contract/run_nextflow.sbatch'])}"
        )

    def build_detached_submit_command(
        self,
        remote_run: MaterializedRun,
    ) -> list[str]:
        """Build the local SSH command that submits the remote batch script in detached mode.

        Args:
            remote_run: Materialized run after local mount paths have been
                translated to cluster-visible paths.
        """

        return self._ssh(
            f"cd {shlex.quote(remote_run.run_root)} && "
            f"{shlex.join([self.settings.workflow_slurm_sbatch_bin, '--parsable', 'contract/run_nextflow.sbatch'])}"
        )

    @staticmethod
    def slurm_job_id_receipt_path(
        run: MaterializedRun,
    ) -> Path:
        """Return the persistent scheduler receipt path."""

        suffix = f"-{run.submission_attempt}" if run.submission_attempt else ""
        return Path(run.contract_dir) / f"slurm-job-id{suffix}.txt"

    def read_slurm_job_id_receipt(
        self,
        run: MaterializedRun,
    ) -> str | None:
        """Read a previously recorded scheduler ID."""

        receipt_path = self.slurm_job_id_receipt_path(run)
        if not receipt_path.is_file():
            return None

        try:
            return parse_sbatch_job_id(
                receipt_path.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid SLURM job receipt: {receipt_path}"
            ) from exc

    def write_slurm_job_id_receipt(
        self,
        run: MaterializedRun,
        job_id: str,
    ) -> None:
        """Atomically persist a scheduler ID on the shared filesystem."""

        atomic_write_text(self.slurm_job_id_receipt_path(run), f"{job_id}\n")


    @staticmethod
    def slurm_submission_attempt_path(
        run: MaterializedRun,
    ) -> Path:
        """Return the durable submission-attempt marker path."""

        suffix = f"-{run.submission_attempt}" if run.submission_attempt else ""
        return Path(run.contract_dir) / f"slurm-submission-attempt{suffix}.txt"


    def write_slurm_submission_attempt(
        self,
        run: MaterializedRun,
    ) -> None:
        """Record an attempt before contacting the scheduler."""

        atomic_write_text(self.slurm_submission_attempt_path(run), f"job_name={self.slurm_job_name(run)}\n")

    def submit_detached(
        self,
        run: MaterializedRun,
    ) -> str:
        """Submit once, store the scheduler receipt, and return its ID."""
        existing_job_id = self.read_slurm_job_id_receipt(run)
        if existing_job_id is not None:
            return existing_job_id

        attempt_path = self.slurm_submission_attempt_path(run)
        if attempt_path.is_file():
            job_name = self.slurm_job_name(run)
            try:
                recovered_job_id = self.find_job_id_by_name(job_name)
            except SlurmStatusUnavailable as exc:
                raise SlurmSubmissionUncertain(
                    "SLURM submission may have succeeded; but the scheduler could not be queried to confirm."
                ) from exc
            if recovered_job_id is None:
                raise SlurmSubmissionUncertain(
                        "A previous SLURM submission may have reached "
                        "the scheduler, but no matching job was found; "
                        "refusing to submit again."
                        )

            self.write_slurm_job_id_receipt(run, recovered_job_id)
            return recovered_job_id

        logs_dir = Path(run.logs_path)
        logs_dir.mkdir(parents=True, exist_ok=True)

        remote_run = self.remote_run_for(run)
        self.write_sbatch_script(run, remote_run)

        command = self.build_detached_submit_command(remote_run)
        self.write_slurm_submission_attempt(run)
        try:
            completed = subprocess.run(
                command,
                cwd=run.run_root,
                capture_output=True,
                text=True,
                timeout=(
                    self.settings
                    .workflow_slurm_ssh_command_timeout_seconds
                ),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SlurmSubmissionUncertain(
                "SLURM SSH submission timed out and may have reached the scheduler; refusing to retry to avoid duplicate job submission"
            ) from exc

        execution_log_path = logs_dir / "execution.log"
        with execution_log_path.open(
            "a",
            encoding="utf-8",
        ) as log_handle:
            log_handle.write(
                "\n--- Submitting workflow to SLURM ---\n"
            )
            log_handle.write(f"$ {shlex.join(command)}\n")
            if completed.stdout:
                log_handle.write(completed.stdout)
            if completed.stderr:
                log_handle.write(completed.stderr)

        if completed.returncode != 0:
            message = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or "SSH submission failed"
            )
            raise RuntimeError(
                f"SLURM SSH submission failed: {message}"
            )

        job_id = parse_sbatch_job_id(completed.stdout)

        self.write_slurm_job_id_receipt(run, job_id)

        return job_id

    def remote_run_for(self, run: MaterializedRun) -> MaterializedRun:
        """Return a copy of `run` with local mount paths converted to cluster paths.

        Every text field is mapped, so a path field added later cannot be
        forgotten; `remote_path` leaves values outside the local mount alone.
        """

        remote_fields = {}
        for item in fields(run):
            value = getattr(run, item.name)
            if isinstance(value, str):
                remote_fields[item.name] = self.remote_path(value)
        remote_fields["nextflow_params"] = {key: self.remote_value(value) for key, value in run.nextflow_params.items()}
        return replace(run, **remote_fields)

    def remote_value(self, value: Any) -> Any:
        """Map string path values while leaving booleans/numbers untouched."""

        if isinstance(value, str):
            return self.remote_path(value)
        return value

    def remote_path(self, value: str) -> str:
        """Translate a local group-share mount path to the cluster `/g` path."""

        local_root = self.settings.workflow_slurm_local_mount_root.rstrip("/")
        remote_root = self.settings.workflow_slurm_remote_mount_root.rstrip("/")
        if not local_root:
            return value
        if not remote_root:
            raise ValueError("WORKFLOW_SLURM_REMOTE_MOUNT_ROOT is required when local mount mapping is set")
        if value == local_root:
            return remote_root
        if value.startswith(f"{local_root}/"):
            return f"{remote_root}{value[len(local_root):]}"
        return value

    def ssh_target(self) -> str:
        """Build `user@host` when an SSH username is configured."""

        if self.settings.workflow_slurm_ssh_user:
            return f"{self.settings.workflow_slurm_ssh_user}@{self.settings.workflow_slurm_ssh_host}"
        return self.settings.workflow_slurm_ssh_host
