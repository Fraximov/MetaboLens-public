"""Job listing, Recent Runs paging, and active-job selection."""

import json
import math
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches, should_reuse_page_cache
from ui.formatting import short_timestamp
from ui.workflow_studio import RECENT_RUNS_PAGE_SIZE, format_command_preview, recent_runs_table

from .shared import (
    ALL_JOBS_VALUE,
    TRACE_REVIEW_SUMMARY_ID,
    _job_analysis_mode,
    _job_stage,
    _should_reuse_workflow_cache,
    _truthy,
)


RECENT_RUNS_PAGE_TRIGGERS = {"wf-recent-runs-prev", "wf-recent-runs-next"}


def _should_reuse_job_cache(
    triggered_id,
    cache,
    token,
    study_id,
    assay_id,
    recent_runs_open,
    outputs_open,
    trace_review_open,
) -> bool:
    passive_trigger_ids = {"url", "wf-page-ready"}
    if recent_runs_open:
        passive_trigger_ids.add("wf-recent-runs-details")
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


def _workflow_jobs_revision(jobs_store, token, study_id, assay_id) -> str | None:
    """Return a stable output-relevant revision for an exact job-cache envelope."""

    if not page_cache_matches(jobs_store, token, study_id=study_id, assay_id=assay_id):
        return None
    jobs = page_cache_data(jobs_store, [])
    if not isinstance(jobs, list):
        return None
    revision_rows = [
        {
            "id": str(job.get("id")),
            "status": job.get("status"),
            "finished_or_updated_at": job.get("finished_at") or job.get("updated_at"),
        }
        for job in jobs
        if isinstance(job, dict) and job.get("id") is not None
    ]
    return json.dumps(sorted(revision_rows, key=lambda row: row["id"]), sort_keys=True, separators=(",", ":"), default=str)


def _should_refresh_jobs(
    trigger: str | None,
    recent_runs_open: bool | None,
    outputs_open: bool | None,
    trace_review_open: bool | None,
    active_tab: str | None,
) -> bool:
    """Avoid loading job history until a job-aware panel is actually needed."""

    if trigger in {
        "wf-jobs-btn",
        "wf-delete-job-result-store",
        "workflow-mutation-signal",
        "token-store",
        "study-select-store",
        "assay-select-store",
    }:
        return True
    if trigger == "wf-jobs-refresh-interval":
        return bool(recent_runs_open or outputs_open or trace_review_open or active_tab == "compute")
    if trigger == "wf-recent-runs-details":
        return bool(recent_runs_open)
    if trigger == "wf-outputs-details":
        return bool(outputs_open)
    if trigger in {"wf-trace-review-details", TRACE_REVIEW_SUMMARY_ID}:
        return bool(trace_review_open)
    if trigger in {None, "url", "wf-page-ready"}:
        return active_tab == "pipeline"
    return trigger == "wf-config-tabs" and active_tab in {"pipeline", "compute"}


def _recent_runs_page_count(jobs: list[dict] | None) -> int:
    return max(1, math.ceil(len(jobs or []) / RECENT_RUNS_PAGE_SIZE))


def _recent_runs_page_index(page, jobs: list[dict] | None) -> int:
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 0
    return max(0, min(page, _recent_runs_page_count(jobs) - 1))


def _navigate_recent_runs_page(page, jobs: list[dict] | None, trigger: str | None) -> int:
    page = _recent_runs_page_index(page, jobs)
    if trigger == "wf-recent-runs-prev":
        return max(0, page - 1)
    if trigger == "wf-recent-runs-next":
        return min(_recent_runs_page_count(jobs) - 1, page + 1)
    return page


def _opened_job_id(triggered_id) -> int | None:
    if not isinstance(triggered_id, dict) or triggered_id.get("type") != "wf-open-job-btn":
        return None
    try:
        return int(triggered_id.get("index"))
    except (TypeError, ValueError):
        return None


def _deleted_job_id(triggered_id) -> int | None:
    if not isinstance(triggered_id, dict) or triggered_id.get("type") != "wf-delete-job-confirm":
        return None
    try:
        return int(triggered_id.get("index"))
    except (TypeError, ValueError):
        return None


def _job_selector_options(jobs: list[dict] | None) -> list[dict]:
    options: list[dict] = []
    options.append({"label": "All jobs", "value": ALL_JOBS_VALUE})
    for job in jobs or []:
        job_id = job.get("id")
        if job_id is None:
            continue
        status = job.get("status") or "unknown"
        details = [status]
        stage = _job_stage(job)
        analysis_mode = _job_analysis_mode(job)
        if stage:
            details.append(stage)
        if analysis_mode:
            details.append(analysis_mode)
        timestamp = job.get("finished_at") or job.get("started_at") or job.get("created_at")
        label = f"Job #{job_id} - {' | '.join(details)}"
        if timestamp:
            label = f"{label} ({short_timestamp(timestamp)})"
        options.append({"label": label, "value": job_id})
    return options


def _default_active_job_value(options: list[dict]) -> int | str | None:
    return options[1]["value"] if len(options) > 1 else (options[0]["value"] if options else None)


def _selected_active_job_value(options: list[dict], current_value) -> int | str | None:
    for option in options:
        if str(option.get("value")) == str(current_value):
            return option.get("value")
    return _default_active_job_value(options)


def _job_can_resume(job: dict | None) -> tuple[bool, str]:
    if not job:
        return False, "Select a specific job to enable Nextflow resume."
    job_id = job.get("id")
    if not job.get("run_root"):
        return False, f"Job #{job_id} has no run directory and cannot be resumed."
    status = job.get("status")
    parameter_values = job.get("parameter_values_json") or {}
    status_payload = job.get("status_payload_json") or {}
    review_stage = _job_stage(job)
    engine = (job.get('input_manifest_json') or {}).get('engine')
    if not engine:
        return False, f"Job #{job_id} used the retired MS-DIAL workflow; its results stay available, but it cannot be continued."
    transitions = (engine.get('descriptor') or {}).get('review_transitions', {})
    is_review_job = review_stage in transitions
    is_failed_resume_retry = status == "failed" and (
        _truthy(parameter_values.get("nextflow_resume")) or _truthy(status_payload.get("nextflow_resume"))
    )
    if status == "succeeded" and is_review_job:
        if review_stage == 'qc_preview':
            return True, f'Job #{job_id} has a QC preview and can finalize using automatic decisions.'
        if review_stage == "is_qc_review":
            return True, f"Job #{job_id} is paused after IS-QC and can continue with Nextflow resume."
        return True, f"Job #{job_id} is paused at {review_stage.replace('_', ' ')} and can continue with Nextflow resume."
    if is_failed_resume_retry:
        return True, f"Job #{job_id} failed during a resumed run and can be retried with Nextflow resume."
    if status in {"queued", "preparing", "submitted", "running"}:
        return False, f"Job #{job_id} is still {status}; wait for it to finish before resuming."
    if status == "failed":
        return False, f"Job #{job_id} failed before a resumable continuation was available."
    return False, f"Job #{job_id} is not a paused review job."


def _is_failed_is_qc_job(job: dict | None) -> bool:
    if not job or _job_stage(job) != "is_qc_review":
        return False
    status_payload = job.get("status_payload_json") or {}
    status = str(status_payload.get("is_qc_status") or "").strip().lower()
    if status in {"fail", "failed"}:
        return True
    summary = status_payload.get("is_qc_summary") or {}
    if isinstance(summary, dict):
        return str(summary.get("overall_status") or "").strip().lower() in {"fail", "failed"}
    return False
