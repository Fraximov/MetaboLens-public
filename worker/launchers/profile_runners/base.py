"""What every profile runner shares: the run handle and how Nextflow is started."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from services.materialized_run import MaterializedRun, resume_requested


@dataclass
class RunHandle:
    run: MaterializedRun
    external_run_id: str
    command: list[str]
    execution_log_path: str
    execution_log_handle: IO[str]
    process: subprocess.Popen[str]


def pipeline_arguments(parameters: dict) -> list[str]:
    """Forward the materialized pipeline contract without knowing its engine."""

    arguments = []
    for name, value in parameters.items():
        if name == "nextflow_resume" or value in (None, ""):
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid Nextflow parameter name: {name!r}")
        arguments.extend([f"--{name}", str(value)])
    return arguments


def nextflow_run_command(run: MaterializedRun, nextflow_bin: str, work_dir: str) -> list[str]:
    """Build `nextflow run` for `run`; a resume continues the earlier run by name."""

    resume = resume_requested(run)
    command = [nextflow_bin, "run", run.entrypoint_path]
    if resume:
        command.extend(["-resume", run.run_name])
    command.extend(["-profile", run.nextflow_profile, "-c", run.nextflow_config_override_path, "-work-dir", work_dir])
    if not resume:
        command.extend(["-name", run.run_name])
    command.extend(pipeline_arguments(run.nextflow_params))
    return command


def start_logged_process(run: MaterializedRun, command: list[str], *, resume_banner: str, run_id_prefix: str) -> RunHandle:
    """Start `command` in the run folder, writing its output to `logs/execution.log`.

    A resumed run appends to the earlier log below `resume_banner`.
    """

    logs_dir = Path(run.logs_path)
    logs_dir.mkdir(parents=True, exist_ok=True)
    execution_log_path = str(logs_dir / "execution.log")
    resume = resume_requested(run)
    log_handle = open(execution_log_path, "a" if resume else "w", encoding="utf-8")
    if resume:
        log_handle.write(f"\n\n--- {resume_banner} ---\n")
        log_handle.flush()
    env = os.environ.copy()
    env.setdefault("NXF_ANSI_LOG", "false")
    process = subprocess.Popen(
        command,
        cwd=run.run_root,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    return RunHandle(
        run=run,
        external_run_id=f"{run_id_prefix}-{process.pid}",
        command=command,
        execution_log_path=execution_log_path,
        execution_log_handle=log_handle,
        process=process,
    )
