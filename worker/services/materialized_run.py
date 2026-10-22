"""The run description every worker step shares, and its recovery snapshot.

Materialization turns one queued job into a `MaterializedRun`; the launcher,
profile runners, status reader and artifact collector only read it. The worker
also saves it as `contract/materialized-run.json`, so a restarted worker can
reattach to a detached SLURM job or continue a paused run.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from services.run_workspace import atomic_write_text


def nextflow_param_text(value: Any) -> str:
    """Nextflow parameters are text: booleans become "true"/"false", None becomes ""."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def is_truthy(value: Any) -> bool:
    """Read a yes/no flag such as `nextflow_resume` from launch or Nextflow parameters."""

    return str(value or "").strip().lower() in {"1", "true", "yes"}


@dataclass
class MaterializedRun:
    """Runtime description consumed after materialization by worker services."""

    job_id: int
    study_id: int
    assay_id: int
    workflow_definition_id: int
    execution_profile: str
    pipeline_root: str
    entrypoint_path: str
    nextflow_profile: str
    run_name: str
    run_root: str
    staged_input_dir: str
    contract_dir: str
    results_path: str
    logs_path: str
    work_path: str
    params_path: str
    samplesheet_path: str
    staged_msdial_config_path: str
    nextflow_config_override_path: str
    artifact_manifest_path: str
    reference_file_asset_id: int
    reference_path: str
    ms1_library_path: str
    ms2_library_path: str
    is_library_path: str
    config_source: str
    metadata_snapshot: dict[str, Any]
    method_metadata: dict[str, Any]
    selected_files: list[dict[str, Any]]
    nextflow_params: dict[str, str]
    # Additive recovery data; version-1 legacy snapshots load with an empty value.
    engine_execution: dict[str, Any] = field(default_factory=dict)
    submission_attempt: int = 0


def resume_requested(run: MaterializedRun) -> bool:
    """True when `run` continues an earlier Nextflow run with `-resume`."""

    return is_truthy(run.nextflow_params.get("nextflow_resume"))


MATERIALIZED_RUN_SNAPSHOT_VERSION = 1
MATERIALIZED_RUN_SNAPSHOT_FILENAME = "materialized-run.json"


def write_materialized_run_snapshot(run: MaterializedRun) -> MaterializedRun:
    """Atomically persist the worker's recovery contract."""

    payload = {"version": MATERIALIZED_RUN_SNAPSHOT_VERSION, "run": asdict(run)}
    atomic_write_text(
        Path(run.contract_dir) / MATERIALIZED_RUN_SNAPSHOT_FILENAME,
        json.dumps(payload, indent=2, sort_keys=True),
    )
    return run


def load_materialized_run_snapshot(job) -> MaterializedRun:
    """Load and validate a worker recovery contract."""

    if not job.run_root:
        raise ValueError(f"Workflow job {job.id} has no run root")
    snapshot_path = Path(job.run_root) / "contract" / MATERIALIZED_RUN_SNAPSHOT_FILENAME
    if not snapshot_path.is_file():
        raise ValueError(f"Missing recovery snapshot: {snapshot_path}")

    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid recovery snapshot: {snapshot_path}") from exc

    version = payload.get("version")
    if version != MATERIALIZED_RUN_SNAPSHOT_VERSION:
        raise ValueError(f"Unsupported recovery snapshot version {version!r}")
    run_payload = payload.get("run")
    if not isinstance(run_payload, dict):
        raise ValueError(f"Invalid recovery snapshot: {snapshot_path}")
    try:
        run = MaterializedRun(**run_payload)
    except TypeError as exc:
        raise ValueError(f"Invalid recovery snapshot: {snapshot_path}") from exc

    if run.job_id != job.id:
        raise ValueError(f"Recovery snapshot belongs to workflow job {run.job_id}, not {job.id}")
    if job.execution_profile and run.execution_profile != job.execution_profile:
        raise ValueError("Recovery snapshot execution profile does not match the database job")
    if Path(run.run_root).expanduser() != Path(job.run_root).expanduser():
        raise ValueError("Recovery snapshot run root does not match the database job")
    return run
