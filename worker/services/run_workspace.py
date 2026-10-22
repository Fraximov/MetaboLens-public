"""Per-job workflow workspace layout.

This module owns the boring filesystem layout for one workflow job. It does
not stage input files or write contract payloads; it only computes and creates
the directories and well-known contract paths.
"""

import os
from dataclasses import dataclass, fields
from pathlib import Path

from app.models.models import WorkflowJob


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` so readers see either the old file or the complete new one."""

    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(text, encoding="utf-8")
    os.replace(temporary_path, path)


@dataclass(frozen=True)
class RunWorkspace:
    """Filesystem layout for one worker-materialized workflow job."""

    run_name: str
    run_root: Path
    staged_input_dir: Path
    contract_dir: Path
    results_path: Path
    logs_path: Path
    work_path: Path
    params_path: Path
    samplesheet_path: Path
    staged_msdial_config_path: Path
    nextflow_config_override_path: Path
    artifact_manifest_path: Path

    @classmethod
    def at(cls, run_root: Path, run_name: str) -> "RunWorkspace":
        """Return the layout under `run_root` without touching the filesystem."""

        contract_dir = run_root / "contract"
        return cls(
            run_name=run_name,
            run_root=run_root,
            staged_input_dir=run_root / "inputs" / "raw_data",
            contract_dir=contract_dir,
            results_path=run_root / "results",
            logs_path=run_root / "logs",
            work_path=run_root / "work",
            params_path=contract_dir / "params.json",
            samplesheet_path=contract_dir / "samplesheet.csv",
            staged_msdial_config_path=contract_dir / "msdial_config.txt",
            nextflow_config_override_path=contract_dir / "nextflow.override.config",
            artifact_manifest_path=contract_dir / "artifact-manifest.json",
        )

    def create_directories(self) -> None:
        for directory in (self.staged_input_dir, self.contract_dir, self.results_path, self.logs_path, self.work_path):
            directory.mkdir(parents=True, exist_ok=True)

    def path_fields(self) -> dict[str, str]:
        """These paths as text, keyed by their `MaterializedRun` field names."""

        return {item.name: str(getattr(self, item.name)) for item in fields(self)}


class RunWorkspaceBuilder:
    """Create the stable per-job directory structure used by worker services."""

    def __init__(self, workflow_runs_root: str, local_runs_root: str = ""):
        self.workflow_runs_root = workflow_runs_root
        self.local_runs_root = local_runs_root

    def build(self, job: WorkflowJob) -> RunWorkspace:
        """Create directories and return all well-known paths for `job`."""

        root = self.local_runs_root if getattr(job, 'execution_profile', None) == 'local_docker' and self.local_runs_root else self.workflow_runs_root
        workspace = RunWorkspace.at(
            Path(root).expanduser().resolve() / f"job_{job.id}",
            f"std{job.study_id}_assay{job.assay_id}_job{job.id}",
        )
        workspace.create_directories()
        return workspace
