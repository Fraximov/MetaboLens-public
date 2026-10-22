"""Materialize the tiny SSH/SLURM/Singularity diagnostic workflow.

The HPC smoke job is deliberately not a scientific MS-DIAL workflow. It uses
the normal `WorkflowJob` queue and worker launcher contract so operators can
test the outside-MetaboLens-to-login-node path before real cluster execution is
enabled for production analyses.
"""

import json
from pathlib import Path
from typing import Any

from app.models.models import WorkflowDefinition, WorkflowJob

from config import WorkerSettings
from services.materialized_run import MaterializedRun, write_materialized_run_snapshot
from services.run_workspace import RunWorkspace, RunWorkspaceBuilder


SMOKE_JOB_KIND = "hpc_smoke_test"
SMOKE_WORKFLOW_STAGE = "hpc_smoke"
SMOKE_NEXTFLOW_PROFILE = "embl_slurm"
SMOKE_EXECUTION_PROFILE = "slurm_singularity_ssh"


class HpcSmokeMaterializer:
    """Build a diagnostic run folder for the SSH SLURM profile runner.

    The backend queues only intent and permissions. This service is worker-side
    on purpose: it writes filesystem artifacts and cluster-facing Nextflow
    files where the long-running worker can inspect and launch them.
    """

    def __init__(self, settings: WorkerSettings):
        self.settings = settings
        self.workspace_builder = RunWorkspaceBuilder(settings.workflow_runs_root)

    @staticmethod
    def is_smoke_job(job: WorkflowJob) -> bool:
        """Return true when the queued job is the diagnostic smoke contract."""

        manifest = dict(job.input_manifest_json or {})
        return manifest.get("job_kind") == SMOKE_JOB_KIND

    def prepare(self, job: WorkflowJob, definition: WorkflowDefinition) -> MaterializedRun:
        """Create the diagnostic run folder for an app-triggered smoke job.

        Args:
            job: Queued `WorkflowJob` created by the backend smoke route.
            definition: Selected workflow definition, used only for identifiers
                and auditing. The tiny smoke workflow writes its own `main.nf`.

        Raises:
            ValueError: If the smoke container image is not configured for the
                worker deployment.
        """

        container_image = str(self.settings.workflow_slurm_smoke_container_image or "").strip()
        if not container_image:
            raise ValueError(
                "WORKFLOW_SLURM_SMOKE_CONTAINER_IMAGE is required for HPC smoke tests "
                "(set it to the Python SIF that worked manually on the cluster)"
            )

        workspace = self.workspace_builder.build(job)
        manifest = dict(job.input_manifest_json or {})
        nextflow_profile = str(manifest.get("nextflow_profile") or SMOKE_NEXTFLOW_PROFILE)
        return prepare_smoke_run(
            run_root=workspace.run_root,
            job_id=job.id,
            study_id=job.study_id,
            assay_id=job.assay_id,
            workflow_definition_id=definition.id,
            execution_profile=job.execution_profile or SMOKE_EXECUTION_PROFILE,
            nextflow_profile=nextflow_profile,
            container_image=container_image,
            run_name=f"hpc_smoke_job{job.id}",
            config_source="hpc_smoke_diagnostic",
            require_container=True,
        )


def prepare_smoke_run(
    *,
    run_root: Path,
    job_id: int,
    study_id: int,
    assay_id: int,
    workflow_definition_id: int,
    execution_profile: str,
    nextflow_profile: str,
    container_image: str = "",
    run_name: str | None = None,
    config_source: str = "hpc_smoke_diagnostic",
    require_container: bool = False,
) -> MaterializedRun:
    """Write a tiny reusable Nextflow run contract and return `MaterializedRun`.

    The generated workflow uppercases two text files through a Python container.
    That is enough to validate SSH, `sbatch`, Nextflow, SLURM task scheduling,
    and Singularity image execution without touching RAW data or MS-DIAL.
    """

    container_image = str(container_image or "").strip()
    if require_container and not container_image:
        raise ValueError("A Singularity/Apptainer container image is required for the HPC smoke test")

    root = run_root.expanduser().resolve()
    final_run_name = run_name or f"hpc_smoke_job{job_id}"
    workspace = RunWorkspace.at(root, final_run_name)
    workspace.create_directories()
    libraries_dir = root / "libraries"
    libraries_dir.mkdir(parents=True, exist_ok=True)

    input_dir = workspace.staged_input_dir
    results_dir = workspace.results_path
    logs_dir = workspace.logs_path
    msdial_config_path = workspace.staged_msdial_config_path
    samplesheet_path = workspace.samplesheet_path
    nextflow_config_path = workspace.nextflow_config_override_path
    params_path = workspace.params_path
    artifact_manifest_path = workspace.artifact_manifest_path
    sample_a = input_dir / "sample_a.txt"
    sample_b = input_dir / "sample_b.txt"
    reference_path = input_dir / "reference.raw"
    ms1_library_path = libraries_dir / "ms1.txt"
    ms2_library_path = libraries_dir / "ms2.msp"
    is_library_path = libraries_dir / "is.txt"

    write_if_missing(sample_a, "alpha\nbeta\n")
    write_if_missing(sample_b, "gamma\ndelta\n")
    write_if_missing(reference_path, "placeholder reference for diagnostic job\n")
    write_if_missing(msdial_config_path, "placeholder MS-DIAL config for diagnostic job\n")
    write_if_missing(
        samplesheet_path,
        "sample_id,filename\nsample_a,sample_a.txt\nsample_b,sample_b.txt\n",
    )
    write_if_missing(ms1_library_path, "placeholder MS1 library for diagnostic job\n")
    write_if_missing(ms2_library_path, "placeholder MS2 library for diagnostic job\n")
    write_if_missing(is_library_path, "Name\tm/z(Da)\tRT (min)\nIS: diagnostic\t100.0\t1.0\n")

    write_smoke_nextflow(root / "main.nf")
    write_smoke_config(nextflow_config_path, profile=nextflow_profile, container_image=container_image)

    nextflow_params = {
        "input_dir": str(input_dir),
        "ref": str(reference_path),
        "msdial_config": str(msdial_config_path),
        "ms1_library": str(ms1_library_path),
        "ms2_library": str(ms2_library_path),
        "is_library": str(is_library_path),
        "samplesheet": str(samplesheet_path),
        "stats_factor_column": "sample_id",
        "outdir": str(results_dir),
        "workflow_stage": SMOKE_WORKFLOW_STAGE,
        "analysis_mode": "ssh_slurm_smoke",
        "output_prefix": "hpc_smoke",
        "nextflow_resume": "false",
    }
    params_payload: dict[str, Any] = {
        "job_id": job_id,
        "study_id": study_id,
        "assay_id": assay_id,
        "workflow_definition_id": workflow_definition_id,
        "execution_profile": execution_profile,
        "pipeline_root": str(root),
        "entrypoint": str(root / "main.nf"),
        "nextflow_profile": nextflow_profile,
        "run_name": final_run_name,
        "selected_files": [],
        "reference_file_asset_id": 0,
        "metadata_submission_id": None,
        "metadata_snapshot": {},
        "method_metadata": {"diagnostic": True, "job_kind": SMOKE_JOB_KIND},
        "diagnostic_inputs": [str(sample_a), str(sample_b)],
        "libraries": {
            "ms1_library": str(ms1_library_path),
            "ms2_library": str(ms2_library_path),
            "is_library": str(is_library_path),
        },
        "msdial_config": {
            "config_source": config_source,
            "source_path": str(msdial_config_path),
            "staged_path": str(msdial_config_path),
            "generation_payload": {},
        },
        "nextflow_params": nextflow_params,
        "samplesheet_path": str(samplesheet_path),
        "nextflow_config_override_path": str(nextflow_config_path),
        "artifact_manifest_path": str(artifact_manifest_path),
        "log_paths": {
            "execution_log": str(logs_dir / "execution.log"),
            "success_report": str(logs_dir / "success_report.txt"),
            "error_report": str(logs_dir / "error.txt"),
        },
    }
    params_path.write_text(json.dumps(params_payload, indent=2, sort_keys=True), encoding="utf-8")

    run = MaterializedRun(
        **workspace.path_fields(),
        job_id=job_id,
        study_id=study_id,
        assay_id=assay_id,
        workflow_definition_id=workflow_definition_id,
        execution_profile=execution_profile,
        pipeline_root=str(root),
        entrypoint_path=str(root / "main.nf"),
        nextflow_profile=nextflow_profile,
        reference_file_asset_id=0,
        reference_path=str(reference_path),
        ms1_library_path=str(ms1_library_path),
        ms2_library_path=str(ms2_library_path),
        is_library_path=str(is_library_path),
        config_source=config_source,
        metadata_snapshot={},
        method_metadata={"diagnostic": True, "job_kind": SMOKE_JOB_KIND},
        selected_files=[],
        nextflow_params=nextflow_params,
    )

    return write_materialized_run_snapshot(run)


def write_if_missing(path: Path, content: str) -> None:
    """Create a placeholder file while preserving manual rerun inspection edits."""

    if not path.exists():
        path.write_text(content, encoding="utf-8")


def write_smoke_nextflow(path: Path) -> None:
    """Write the tiny DSL2 workflow shared by CLI and app smoke tests."""

    path.write_text(
        '''nextflow.enable.dsl=2

params.input_dir = params.input_dir ?: 'inputs/raw_data'
params.outdir = params.outdir ?: 'results'

process PYTHON_UPPERCASE {
    publishDir params.outdir, mode: 'copy'

    input:
    path input_file

    output:
    path "*.upper.txt"

    script:
    def stem = input_file.baseName
    """
    python -c 'from pathlib import Path; inp = Path("${input_file}"); out = Path("${stem}.upper.txt"); out.write_text(inp.read_text().upper(), encoding="utf-8")'
    """
}

workflow {
    inputs = Channel.fromPath("${params.input_dir}/*.txt")
    PYTHON_UPPERCASE(inputs)
}
''',
        encoding="utf-8",
    )


def write_smoke_config(path: Path, *, profile: str, container_image: str) -> None:
    """Write the minimal SLURM/Singularity profile used by the smoke workflow."""

    container_line = ""
    if container_image:
        escaped_container = container_image.replace("\\", "\\\\").replace("'", "\\'")
        container_line = f"        process.container = '{escaped_container}'\n"
    path.write_text(
        f"""profiles {{
    {profile} {{
        process.executor = 'slurm'
        singularity.enabled = true
        singularity.autoMounts = true
{container_line}    }}
}}
""",
        encoding="utf-8",
    )
