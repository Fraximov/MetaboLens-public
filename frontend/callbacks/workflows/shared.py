"""State shared by more than one section of the workflows page.

The active job and the workflow-output cache are the real seams of the
page: monitoring chooses the active job, analysis consumes it. Helpers
that both sides need live here so neither owns them."""

import dash
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches, should_reuse_page_cache


ALL_JOBS_VALUE = "__all__"


TRACE_REVIEW_SUMMARY_ID = "wf-trace-review-summary"


OUTPUT_REFRESH_TRIGGERS = {
    "wf-outputs-details",
    "wf-trace-review-details",
    TRACE_REVIEW_SUMMARY_ID,
    "wf-output-refresh-btn",
    "wf-launch-btn",
    "wf-continue-btn",
    "wf-jobs-btn",
    "wf-jobs-refresh-interval",
    "wf-jobs-store",
    "wf-active-job",
    "token-store",
    "study-select-store",
    "assay-select-store",
}


def _workflow_page_is_active(pathname: str | None) -> bool:
    return str(pathname or "").rstrip("/") == "/workflows"


def _workflow_cache_rows(cache, token, *, study_id=None, assay_id=None) -> list[dict]:
    if not page_cache_matches(cache, token, study_id=study_id, assay_id=assay_id):
        return []
    rows = page_cache_data(cache, [])
    return rows if isinstance(rows, list) else []


def _should_reuse_workflow_cache(triggered_id, cache, token, *, passive_trigger_ids, study_id=None, assay_id=None) -> bool:
    return should_reuse_page_cache(
        cache,
        token,
        triggered_id=triggered_id,
        passive_trigger_ids=passive_trigger_ids,
        study_id=study_id,
        assay_id=assay_id,
    )


def _workflow_output_cache(data, token, study_id, assay_id, jobs_revision, active_job_id) -> dict:
    cache = build_page_cache(data, token, study_id=study_id, assay_id=assay_id)
    cache["jobs_revision"] = jobs_revision
    cache["active_job_id"] = active_job_id
    return cache


def _workflow_output_cache_matches(cache, token, study_id, assay_id, active_job_id) -> bool:
    return (
        isinstance(cache, dict)
        and page_cache_matches(cache, token, study_id=study_id, assay_id=assay_id)
        and "active_job_id" in cache
        and cache.get("active_job_id") == active_job_id
    )


def _should_reuse_output_cache(
    triggered_id,
    cache,
    token,
    study_id,
    assay_id,
    outputs_open,
    trace_review_open,
    jobs_revision,
    active_job_id,
) -> bool:
    if not _workflow_output_cache_matches(cache, token, study_id, assay_id, active_job_id):
        return False
    if cache.get("warning") or cache.get("error") or cache.get("jobs_revision") != jobs_revision or "jobs_revision" not in cache:
        return False
    passive_trigger_ids = {"url", "wf-page-ready", "wf-active-job"}
    if outputs_open:
        passive_trigger_ids.add("wf-outputs-details")
    if trace_review_open:
        passive_trigger_ids.update({"wf-trace-review-details", TRACE_REVIEW_SUMMARY_ID})
    return _should_reuse_workflow_cache(
        triggered_id,
        cache,
        token,
        passive_trigger_ids=passive_trigger_ids,
        study_id=study_id,
        assay_id=assay_id,
    )


def _outputs_for_active_job(outputs: list[dict], active_job_id: int | None) -> list[dict]:
    if active_job_id is None:
        return outputs
    return [output for output in outputs if output.get("workflow_job_id") == active_job_id]


def _should_refresh_outputs(trigger: str | None, outputs_open: bool | None, eic_open: bool | None) -> bool:
    """Avoid fetching workflow artifacts while their panels are collapsed."""

    if trigger in {"wf-launch-btn", "wf-continue-btn", "wf-active-job"}:
        return True
    if trigger == "wf-outputs-details":
        return bool(outputs_open)
    if trigger == "wf-trace-review-details":
        return bool(eic_open)
    if trigger == TRACE_REVIEW_SUMMARY_ID:
        return bool(eic_open)
    return bool(outputs_open or eic_open) and trigger in OUTPUT_REFRESH_TRIGGERS


def cache_workflow_outputs_after_fetch_failure(current_store, token, study_id, assay_id, jobs_revision, active_job_id, exc):
    warning = f"Workflow output refresh failed: {exc}"
    if _workflow_output_cache_matches(current_store, token, study_id, assay_id, active_job_id):
        cache = dict(current_store)
        cache.pop("error", None)
        cache["warning"] = warning
        return cache

    cache = _workflow_output_cache([], token, study_id, assay_id, jobs_revision, active_job_id)
    cache["error"] = f"Workflow output load failed: {exc}"
    return cache


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _job_stage(job: dict) -> str | None:
    parameter_values = job.get("parameter_values_json") or {}
    status_payload = job.get("status_payload_json") or {}
    return parameter_values.get("workflow_stage") or status_payload.get("workflow_stage")


def _job_analysis_mode(job: dict) -> str | None:
    parameter_values = job.get("parameter_values_json") or {}
    status_payload = job.get("status_payload_json") or {}
    return parameter_values.get("analysis_mode") or status_payload.get("analysis_mode")


def _dropdown_value_present(options: list[dict], current_value) -> bool:
    if current_value is None:
        return False
    return any(str(option.get("value")) == str(current_value) for option in options)


def _stable_dropdown_value_update(options: list[dict], current_value, fallback_value):
    if _dropdown_value_present(options, current_value):
        return dash.no_update
    return fallback_value


def _active_job_id(active_job_value: int | str | None) -> int | None:
    if active_job_value in (None, "", ALL_JOBS_VALUE, "all"):
        return None
    try:
        return int(active_job_value)
    except (TypeError, ValueError):
        return None


def _job_by_id(jobs: list[dict] | None, active_job_value: int | str | None) -> dict | None:
    active_job_id = _active_job_id(active_job_value)
    if active_job_id is None:
        return None
    for job in jobs or []:
        try:
            job_id = int(job.get("id"))
        except (TypeError, ValueError):
            continue
        if job_id == active_job_id:
            return job
    return None


def _workflow_output_headers() -> list[str]:
    return ["Job ID", "Artifact Role", "File ID", "Dataset Kind", "Dataset Samples", "Filename", "Role", "Size", "Status", "Created", "Storage URI"]


def _execution_profile_label(execution_profile: str | None) -> str:
    labels = {
        "local_docker": "Local Docker",
        "slurm_singularity": "HPC SLURM",
        "slurm_singularity_ssh": "HPC SSH",
    }
    return labels.get(execution_profile or "", execution_profile or "Workflow")
