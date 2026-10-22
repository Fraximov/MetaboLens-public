"""Render `contract/nextflow.override.config`, the per-run Nextflow settings file.

Local Docker runs get the operator's CPU and memory limits for the processes the
engine adapter declares. Cluster runs rely on the profile committed in the
workflow's `nextflow.config`, so their override only records which profile ran.
"""

from config import WorkerSettings


def local_resource_override(settings: WorkerSettings, process_groups: dict[str, str]) -> str:
    """Apply operator-configured local limits to adapter-declared processes."""

    groups = {
        'default': (settings.workflow_local_default_cpus, settings.workflow_local_max_memory_gb),
        'prepare': (settings.workflow_local_default_cpus, settings.workflow_local_prepare_memory_gb),
        'peak': (settings.workflow_local_peak_cpus, settings.workflow_local_peak_memory_gb),
    }
    lines = [
        'params {',
        f'    max_cpus = {max(settings.workflow_local_default_cpus, settings.workflow_local_peak_cpus)}',
        f'    max_memory = {settings.workflow_local_max_memory_gb}.GB',
        '}',
        'process {',
    ]
    for process, group in process_groups.items():
        cpus, memory = groups[group]
        memory = min(memory, settings.workflow_local_max_memory_gb)
        lines.extend([
            f'    withName: {process} {{',
            f'        cpus = {cpus}',
            f"        memory = '{memory} GB'",
            '    }',
        ])
    return '\n'.join([*lines, '}', ''])


def slurm_singularity_override(profile: str) -> str:
    """Render the per-run override used alongside the committed SLURM profile."""

    return (
        "// MetaboLens per-run Nextflow override.\n"
        f"// Profile '{profile}' is defined in the workflow's nextflow.config.\n"
        "// Keep this file for run-specific overrides and auditability.\n"
    )
