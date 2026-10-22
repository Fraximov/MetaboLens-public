"""Workflow diagram asset selection helpers."""

from __future__ import annotations

WORKFLOW_METRO_MAP = {
    "src": "/assets/metabolens-workflow-metro.svg",
    "alt": (
        "MetaboLens route map showing the optional IS-QC pause, operator decision, "
        "and resumed MS-DIAL path; optional SIRIUS annotations registered separately; "
        "and QC-only, discovery, and full analysis roads."
    ),
}

MODULAR_WORKFLOW_DAGS = {
    "full": {
        "src": "/assets/msdial5-workflow-dag-v2-full.svg",
        "alt": "MS-DIAL 5 Nextflow DAG with the modular StatPipeline v2 full analysis graph.",
    },
    "qc_only": {
        "src": "/assets/msdial5-workflow-dag-v2-qc-only.svg",
        "alt": "MS-DIAL 5 Nextflow DAG with the modular StatPipeline v2 QC-only graph.",
    },
    "discovery": {
        "src": "/assets/msdial5-workflow-dag-v2-discovery.svg",
        "alt": "MS-DIAL 5 Nextflow DAG with the modular StatPipeline v2 discovery graph.",
    },
}


# Nextflow's own DAG for an engine entrypoint, generated with `-preview
# -with-dag` (see docs/DEVELOPMENT.md). Keyed by the definition's engine_id; an
# engine without one keeps the declared stage list.
ENGINE_TECHNICAL_DAGS = {
    "masscube": {
        "src": "/assets/masscube-workflow-dag.svg",
        "alt": (
            "MassCube Nextflow DAG: acquisition preparation, MASSCUBE_PROCESS with its "
            "configuration and optional MS2 library, then MetaboLensR preview and finalization."
        ),
    },
}


def engine_technical_dag_asset(engine: dict | None) -> dict[str, str] | None:
    return ENGINE_TECHNICAL_DAGS.get(str((engine or {}).get("engine_id") or ""))


def workflow_overview_asset(analysis_mode: str | None) -> dict[str, str]:
    return WORKFLOW_METRO_MAP


def workflow_technical_dag_asset(analysis_mode: str | None) -> dict[str, str]:
    return MODULAR_WORKFLOW_DAGS.get(str(analysis_mode or "full"), MODULAR_WORKFLOW_DAGS["full"])


def workflow_diagram_asset(analysis_mode: str | None) -> dict[str, str]:
    return workflow_technical_dag_asset(analysis_mode)
