import json

import dash
from dash import Input, Output, State

from services.page_cache import page_cache_data


def next_page_data_revision(current_revision) -> int:
    return int(current_revision or 0) + 1


def workflow_jobs_revision(jobs_store) -> str | None:
    """A fingerprint of the job list, whichever store shape carries it.

    The Workflows page held these in a page cache; the job workspace holds them
    as {"jobs": [...], "section": ...}. Both are accepted so this keeps working
    across the move, and so the pre-existing tests still describe it.
    """
    if not isinstance(jobs_store, dict):
        return None
    jobs = jobs_store.get("jobs")
    if not isinstance(jobs, list):
        jobs = page_cache_data(jobs_store, [])
    if not isinstance(jobs, list):
        return None
    rows = [
        {
            "id": str(job.get("id")),
            "status": job.get("status"),
            "finished_or_updated_at": job.get("finished_at") or job.get("updated_at"),
        }
        for job in jobs
        if isinstance(job, dict) and job.get("id") is not None
    ]
    payload = {
        "auth_fingerprint": jobs_store.get("auth_fingerprint"),
        "context": jobs_store.get("context"),
        "jobs": sorted(rows, key=lambda row: row["id"]),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def register_callbacks(app):
    @app.callback(
        Output("page-data-revision-store", "data"),
        Input("sample-upload-refresh-signal", "data"),
        Input("study-transition-refresh-signal", "data"),
        Input("page-data-mutation-signal", "data"),
        Input("workflow-mutation-signal", "data"),
        State("page-data-revision-store", "data"),
        prevent_initial_call=True,
    )
    def advance_page_data_revision(
        _sample_upload_signal,
        _study_transition_signal,
        _mutation_signal,
        _workflow_mutation_signal,
        current_revision,
    ):
        return next_page_data_revision(current_revision)

    @app.callback(
        Output("page-data-revision-store", "data", allow_duplicate=True),
        Output("workflow-jobs-revision-store", "data"),
        Input("jw-jobs-store", "data"),
        State("workflow-jobs-revision-store", "data"),
        State("page-data-revision-store", "data"),
        prevent_initial_call=True,
    )
    def advance_page_data_revision_for_workflow_jobs(jobs_store, previous_jobs_revision, current_revision):
        jobs_revision = workflow_jobs_revision(jobs_store)
        if jobs_revision is None:
            return dash.no_update, dash.no_update
        if previous_jobs_revision is None:
            return dash.no_update, jobs_revision
        if jobs_revision == previous_jobs_revision:
            return dash.no_update, dash.no_update
        return next_page_data_revision(current_revision), jobs_revision
