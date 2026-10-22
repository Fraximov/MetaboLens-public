"""Callbacks for the lightweight dashboard landing page.

The dashboard is intentionally read-heavy. It fetches compact overview data for
the active study/assay and only mutates shared selection stores for explicit
user actions such as opening a recent study or resuming the last browser
context.
"""

import time

import dash
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, html

from services import study_service, summary_service, workflow_service
from services.api_client import APIError
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches
from callbacks.clicks import real_click
from callbacks.job_workspace import job_href
from ui.components import render_scrollable_table
from ui.formatting import format_status_counts, resolve_page_slug, short_timestamp, state_badge, timestamp_cell


def _dashboard_cache_matches(cache, token, study_id, assay_id, revision) -> bool:
    return (
        isinstance(cache, dict)
        and cache.get("revision") == revision
        and page_cache_matches(cache, token, study_id=study_id, assay_id=assay_id)
    )


def _build_dashboard_cache(data, token, study_id, assay_id, revision, *, loaded_at=None) -> dict:
    cache = build_page_cache(data, token, study_id=study_id, assay_id=assay_id, loaded_at=loaded_at)
    cache["revision"] = revision
    return cache


def _should_reuse_dashboard_cache(triggered_id, cache, token, study_id, assay_id, revision) -> bool:
    return (
        triggered_id == "url"
        and isinstance(cache, dict)
        and not cache.get("refresh_failed")
        and _dashboard_cache_matches(cache, token, study_id, assay_id, revision)
    )


def register_callbacks(app):
    """Wire dashboard reads plus explicit resume/open selection actions."""

    @app.callback(
        Output("last-context-store", "data"),
        Input("study-select-store", "data"),
        Input("assay-select-store", "data"),
        Input("dashboard-store", "data"),
        State("studies-store", "data"),
        State("assays-store", "data"),
        State("last-context-store", "data"),
        prevent_initial_call=True,
    )
    def persist_last_context(selected_study_id, selected_assay_id, dashboard_store, studies_data, assays_data, current_store):
        """Persist the last meaningful browser context without requiring backend state."""

        dashboard_data = page_cache_data(dashboard_store, {}) or {}
        studies = studies_data or dashboard_data.get("studies") or []
        assays = assays_data or dashboard_data.get("assays") or []
        record = build_last_context_record(
            study_id=selected_study_id,
            assay_id=selected_assay_id,
            studies_data=studies,
            assays_data=assays,
            previous_store=current_store,
        )
        if record is None:
            return dash.no_update
        if current_store and same_last_context(current_store, record):
            return dash.no_update
        record["saved_at"] = time.time()
        return record

    @app.callback(
        Output("dashboard-store", "data"),
        Input("url", "pathname"),
        Input("dashboard-refresh-btn", "n_clicks"),
        Input("study-select-store", "data"),
        Input("assay-select-store", "data"),
        Input("token-store", "data"),
        Input("page-data-revision-store", "data"),
        State("dashboard-store", "data"),
    )
    def load_dashboard_data(pathname, _refresh_clicks, selected_study_id, selected_assay_id, token, revision, current_store):
        """Load lightweight dashboard data from existing study/workflow endpoints."""

        normalized, _ = resolve_page_slug(pathname)
        if normalized != "dashboard":
            return dash.no_update

        existing = current_store or {}
        if _should_reuse_dashboard_cache(
            dash.ctx.triggered_id,
            existing,
            token,
            selected_study_id,
            selected_assay_id,
            revision,
        ):
            return dash.no_update

        if not token:
            return _build_dashboard_cache({
                "status": "Login to load dashboard data.",
                "studies": [],
                "recent_studies": [],
                "summary": None,
                "assays": [],
                "activity": [],
                "workflow_jobs": [],
                "study_id": selected_study_id,
                "assay_id": selected_assay_id,
                "loaded_at": time.time(),
            }, token, selected_study_id, selected_assay_id, revision)

        try:
            studies = study_service.list_studies(token)
            recent = sorted_recent_studies(studies)
            study_ids = {int(study.get("id")) for study in studies if study.get("id") is not None}

            summary = None
            assays = []
            activity = []
            workflow_jobs = []
            status_text = ""
            active_study_id = normalize_optional_int(selected_study_id)
            active_study_id = active_study_id if active_study_id in study_ids else None
            active_assay_id = None

            if selected_study_id and active_study_id is None:
                status_text = "Selected study is no longer available. Open a recent study or use the resume action."

            if active_study_id is not None:
                summary = summary_service.get_study_summary(token, active_study_id)
                assays = summary_service.list_study_assays(token, active_study_id)
                activity = summary_service.list_study_activity(token, active_study_id, limit=8)

                assay_ids = {int(assay.get("id")) for assay in assays if assay.get("id") is not None}
                active_assay_id = normalize_optional_int(selected_assay_id)
                active_assay_id = active_assay_id if active_assay_id in assay_ids else None
                if active_assay_id is not None:
                    workflow_jobs = dashboard_job_summaries(workflow_service.list_workflow_jobs(token, active_assay_id))

            return _build_dashboard_cache({
                "status": status_text,
                "studies": studies,
                "recent_studies": recent,
                "summary": summary,
                "assays": assays,
                "activity": activity,
                "workflow_jobs": workflow_jobs,
                "study_id": active_study_id,
                "assay_id": active_assay_id,
                "loaded_at": time.time(),
            }, token, selected_study_id, selected_assay_id, revision)
        except (APIError, ValueError) as exc:
            cached_data = page_cache_data(existing, {}) if page_cache_matches(
                existing, token, study_id=selected_study_id, assay_id=selected_assay_id
            ) else {}
            failed_data = dict(cached_data)
            failed_data["status"] = f"Dashboard load failed: {exc}"
            failed_cache = _build_dashboard_cache(
                failed_data,
                token,
                selected_study_id,
                selected_assay_id,
                revision,
                loaded_at=existing.get("loaded_at"),
            )
            failed_cache["refresh_failed"] = True
            return failed_cache

    @app.callback(
        Output("dashboard-status", "children"),
        Output("dashboard-resume-copy", "children"),
        Output("dashboard-resume-status", "children"),
        Output("dashboard-resume-btn", "disabled"),
        Output("dashboard-kpi-strip", "children"),
        Output("dashboard-activity-table", "children"),
        Output("dashboard-workflow-table", "children"),
        Output("dashboard-recent-studies-table", "children"),
        Output("dashboard-assays-table", "children"),
        Input("dashboard-store", "data"),
        Input("last-context-store", "data"),
        Input("token-store", "data"),
        Input("study-select-store", "data"),
        Input("assay-select-store", "data"),
        Input("page-data-revision-store", "data"),
        Input("url", "pathname"),
    )
    def render_dashboard(dashboard_store, last_context_store, token, selected_study_id, selected_assay_id, revision, pathname):
        """Render the dashboard from the aggregated session store."""

        normalized, _ = resolve_page_slug(pathname)
        if normalized != "dashboard":
            return (
                dash.no_update,
                dash.no_update,
                dash.no_update,
                dash.no_update,
                dash.no_update,
                dash.no_update,
                dash.no_update,
                dash.no_update,
                dash.no_update,
            )

        store = page_cache_data(dashboard_store, {}) if _dashboard_cache_matches(
            dashboard_store,
            token,
            selected_study_id,
            selected_assay_id,
            revision,
        ) else {}
        studies = store.get("studies") or []
        assays = store.get("assays") or []
        summary = store.get("summary")
        activity = store.get("activity") or []
        workflow_jobs = store.get("workflow_jobs") or []
        recent_studies = store.get("recent_studies") or []
        selected_study_id = store.get("study_id")
        selected_assay_id = store.get("assay_id")

        resume_copy, resume_disabled, resume_status = describe_resume_context(
            token=token,
            last_context=last_context_store,
            studies=studies,
        )

        kpi_strip = build_dashboard_kpi_strip(summary=summary, assay_count=len(assays), active_assay_id=selected_assay_id)
        activity_table = render_scrollable_table(
            ["Time", "Actor", "Action", "Target"],
            dashboard_activity_rows(activity),
            max_height="320px",
        )
        workflow_table = render_scrollable_table(
            ["Open", "Job", "Status", "Started", "Finished", "Error"],
            dashboard_workflow_rows(workflow_jobs),
            max_height="320px",
        )
        recent_studies_table = render_scrollable_table(
            ["Open", "ID", "Title", "Status", "Assays", "Updated"],
            dashboard_recent_study_rows(recent_studies, selected_study_id),
            max_height="320px",
        )
        assays_table = render_scrollable_table(
            ["Open", "ID", "Name", "Status", "Updated"],
            dashboard_assay_rows(assays, selected_assay_id),
            max_height="320px",
        )
        return (
            store.get("status", "Refresh the dashboard to load data."),
            resume_copy,
            resume_status,
            resume_disabled,
            kpi_strip,
            activity_table,
            workflow_table,
            recent_studies_table,
            assays_table,
        )

    @app.callback(
        Output("study-select-store", "data", allow_duplicate=True),
        Output("dashboard-resume-pending-store", "data", allow_duplicate=True),
        Output("assays-store", "data", allow_duplicate=True),
        Output("assays-context-store", "data", allow_duplicate=True),
        Input({"type": "dashboard-study-open-btn", "index": ALL}, "n_clicks"),
        State("token-store", "data"),
        prevent_initial_call=True,
    )
    def open_recent_study(_, token):
        """Select a recent study, clear any pending resume handoff, and load its assays.

        The assay list is otherwise loaded only on the Studies page; without it
        the study's assay could not be chosen here.
        """

        if not real_click():
            return dash.no_update, dash.no_update, dash.no_update, dash.no_update
        trigger = dash.ctx.triggered_id
        if not isinstance(trigger, dict) or trigger.get("index") is None:
            return dash.no_update, dash.no_update, dash.no_update, dash.no_update
        study_id = int(trigger["index"])
        return (study_id, {}, *study_assays_cache(token, study_id))

    @app.callback(
        Output("assay-select-store", "data", allow_duplicate=True),
        Input({"type": "dashboard-assay-open-btn", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def open_assay(_):
        """Make an assay of the open study the active one."""

        if not real_click():
            return dash.no_update
        trigger = dash.ctx.triggered_id
        if not isinstance(trigger, dict) or trigger.get("index") is None:
            return dash.no_update
        return int(trigger["index"])

    @app.callback(
        Output("study-select-store", "data", allow_duplicate=True),
        Output("dashboard-resume-pending-store", "data"),
        Output("assays-store", "data", allow_duplicate=True),
        Output("assays-context-store", "data", allow_duplicate=True),
        Input("dashboard-resume-btn", "n_clicks"),
        State("last-context-store", "data"),
        State("dashboard-store", "data"),
        State("token-store", "data"),
        prevent_initial_call=True,
    )
    def resume_last_context(_, last_context_store, dashboard_store, token=None):
        """Resume the last saved study immediately and hand the assay off once its assays load."""

        studies = (page_cache_data(dashboard_store, {}) or {}).get("studies") or []
        study_ids = {int(study.get("id")) for study in studies if study.get("id") is not None}
        last_context = last_context_store or {}
        study_id = last_context.get("study_id")
        if study_id is None or int(study_id) not in study_ids:
            return dash.no_update, {}, dash.no_update, dash.no_update
        pending = {
            "study_id": int(study_id),
            "assay_id": int(last_context["assay_id"]) if last_context.get("assay_id") not in (None, "") else None,
        }
        return (int(study_id), pending, *study_assays_cache(token, int(study_id)))

    @app.callback(
        Output("assay-select-store", "data", allow_duplicate=True),
        Output("dashboard-resume-pending-store", "data", allow_duplicate=True),
        Input("assays-store", "data"),
        Input("dashboard-resume-pending-store", "data"),
        State("study-select-store", "data"),
        State("assay-select-store", "data"),
        prevent_initial_call=True,
    )
    def restore_pending_assay(assays_data, pending_store, selected_study_id, current_assay_id):
        """Apply the saved assay after the normal study-to-assay refresh completes."""

        pending = pending_store or {}
        pending_study_id = pending.get("study_id")
        pending_assay_id = pending.get("assay_id")
        if pending_study_id is None:
            return dash.no_update, dash.no_update
        if selected_study_id != pending_study_id:
            return dash.no_update, dash.no_update

        assays = assays_data or []
        valid_ids = {int(assay.get("id")) for assay in assays if assay.get("id") is not None}
        if pending_assay_id in valid_ids and pending_assay_id != current_assay_id:
            return int(pending_assay_id), {}
        return dash.no_update, {}


def build_last_context_record(*, study_id, assay_id, studies_data, assays_data, previous_store) -> dict | None:
    """Build a compact browser-local study/assay context record."""

    if study_id in (None, ""):
        return None
    normalized_study_id = int(study_id)
    normalized_assay_id = normalize_optional_int(assay_id)
    selected_study = next((study for study in (studies_data or []) if study.get("id") == normalized_study_id), None)
    selected_assay = next((assay for assay in (assays_data or []) if assay.get("id") == normalized_assay_id), None)
    previous = previous_store or {}
    previous_study_title = previous.get("study_title") if previous.get("study_id") == normalized_study_id else None
    previous_assay_name = previous.get("assay_name") if previous.get("assay_id") == normalized_assay_id else None
    return {
        "study_id": normalized_study_id,
        "assay_id": normalized_assay_id,
        "study_title": selected_study.get("title") if selected_study else previous_study_title,
        "assay_name": selected_assay.get("name") if selected_assay else previous_assay_name,
    }


def same_last_context(current_store: dict, next_store: dict) -> bool:
    """Compare stored context payloads without the transient save timestamp."""

    return (
        current_store.get("study_id") == next_store.get("study_id")
        and current_store.get("assay_id") == next_store.get("assay_id")
        and current_store.get("study_title") == next_store.get("study_title")
        and current_store.get("assay_name") == next_store.get("assay_name")
    )


def sorted_recent_studies(studies: list[dict] | None, limit: int = 5) -> list[dict]:
    """Return the most recently updated studies for the lightweight landing page."""

    def sort_key(study: dict) -> str:
        return str(study.get("updated_at") or study.get("created_at") or "")

    filtered = [study for study in (studies or []) if study.get("id") is not None]
    return sorted(filtered, key=sort_key, reverse=True)[:limit]


def describe_resume_context(*, token: str | None, last_context: dict | None, studies: list[dict] | None) -> tuple[str, bool, str]:
    """Describe whether the saved browser context can be restored from the dashboard."""

    if not token:
        return "Login to restore the last browser context.", True, ""

    context = last_context or {}
    study_id = context.get("study_id")
    if study_id in (None, ""):
        return "No saved browser context yet. Select a study and assay to make resume available.", True, ""

    normalized_study_id = normalize_optional_int(study_id)
    if normalized_study_id is None:
        return "The saved study is not available in your current access list.", True, "Saved context unavailable."

    study = next((row for row in (studies or []) if row.get("id") == normalized_study_id), None)
    if study is None:
        return "The saved study is not available in your current access list.", True, "Saved context unavailable."

    study_title = context.get("study_title") or study.get("title") or f"Study #{study_id}"
    assay_id = context.get("assay_id")
    if assay_id not in (None, ""):
        assay_name = context.get("assay_name") or f"Assay #{assay_id}"
        return f"Restore {study_title} > {assay_name} from this browser.", False, ""
    return f"Restore {study_title} from this browser.", False, ""


def build_dashboard_kpi_strip(*, summary: dict | None, assay_count: int, active_assay_id: int | None):
    """Render a compact KPI strip from the existing study summary payload."""

    if not summary:
        return html.Div(
            "Select a study to load summary, metadata, and workflow counts.",
            className="section-helper dashboard-empty-strip",
        )

    study = summary.get("study") or {}
    cards = [
        dashboard_metric_card("Study Status", state_badge(study.get("status")), helper=study.get("title") or f"Study #{study.get('id')}"),
        dashboard_metric_card("Active Assay", f"#{active_assay_id}" if active_assay_id else "-", helper=f"{assay_count} assay record(s)"),
        dashboard_metric_card("Latest Metadata", summary.get("latest_metadata_status") or "-", helper=short_timestamp(summary.get("latest_metadata_submitted_at")) if summary.get("latest_metadata_submitted_at") else "No metadata submission yet"),
        dashboard_metric_card("Workflow Jobs", str(summary.get("workflow_job_count", 0)), helper=format_status_counts(summary.get("workflow_status_counts"))),
        dashboard_metric_card("Samples", str(summary.get("sample_count", 0)), helper=f"{summary.get('dataset_file_count', 0)} dataset file(s)"),
    ]
    return html.Div(cards, className="dashboard-metric-grid")


def dashboard_metric_card(label: str, value, helper: str | None = None) -> html.Div:
    """Render one lightweight metric tile."""

    return html.Div(
        [
            html.Div(label, className="dashboard-metric-label"),
            html.Div(value, className="dashboard-metric-value"),
            html.Div(helper or "", className="dashboard-metric-helper"),
        ],
        className="dashboard-metric-card",
    )


def dashboard_activity_rows(activity: list[dict] | None, limit: int = 8) -> list[list[object]]:
    """Build compact recent-activity rows for the dashboard table."""

    rows: list[list[object]] = []
    for event in (activity or [])[:limit]:
        actor = event.get("actor_email") or (f"user#{event.get('actor_user_id')}" if event.get("actor_user_id") else "system")
        target = f"{event.get('target_type')}:{event.get('target_id')}"
        rows.append(
            [
                timestamp_cell(event.get("created_at")),
                actor,
                event.get("event_type"),
                target,
            ]
        )
    return rows


DASHBOARD_JOB_LIMIT = 5


def dashboard_job_summaries(jobs: list[dict] | None, limit: int = DASHBOARD_JOB_LIMIT) -> list[dict]:
    """Keep only the jobs and fields the workflow table draws.

    Full job records carry manifests and status payloads this page never reads,
    and Dash uploads the dashboard store with every callback that takes it.
    """

    return [
        {
            "id": job.get("id"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "error_message": short_error_message(job.get("error_message")) if job.get("error_message") else None,
        }
        for job in (jobs or [])[:limit]
    ]


def dashboard_workflow_rows(jobs: list[dict] | None, limit: int = DASHBOARD_JOB_LIMIT) -> list[list[object]]:
    """Build compact workflow-status rows for the active assay, each linking to its job page."""

    rows: list[list[object]] = []
    for job in (jobs or [])[:limit]:
        rows.append(
            [
                html.Div(
                    dbc.Button(
                        "Open",
                        href=job_href(job.get("id")),
                        color="outline-secondary",
                        size="sm",
                        className="w-100",
                    )
                ),
                f"#{job.get('id')}",
                state_badge(job.get("status")),
                timestamp_cell(job.get("started_at") or job.get("created_at")),
                timestamp_cell(job.get("finished_at")),
                short_error_message(job.get("error_message")),
            ]
        )
    return rows


def study_assays_cache(token: str | None, study_id: int) -> tuple:
    """A study's assays in the shared assays-store shape, or no_update when they cannot load.

    Filling the same cache the Studies page fills lets the usual rule choose
    the study's assay (see callbacks.studies.preferred_assay_id).
    """
    if not token:
        return dash.no_update, dash.no_update
    try:
        return study_service.list_study_assays(token, study_id), build_page_cache([], token, study_id=study_id)
    except (APIError, ValueError):
        return dash.no_update, dash.no_update


def dashboard_assay_rows(assays: list[dict] | None, selected_assay_id: int | None) -> list[list[object]]:
    """Assays of the open study, each with an action that makes it active."""

    rows: list[list[object]] = []
    for assay in assays or []:
        assay_id = assay.get("id")
        is_selected = assay_id == selected_assay_id
        rows.append(
            [
                html.Div(
                    dbc.Button(
                        "Selected" if is_selected else "Open",
                        id={"type": "dashboard-assay-open-btn", "index": assay_id},
                        n_clicks=0,
                        color="primary" if is_selected else "outline-secondary",
                        size="sm",
                        className="w-100",
                        disabled=is_selected,
                    )
                ),
                assay_id,
                assay.get("name"),
                state_badge(assay.get("status")),
                timestamp_cell(assay.get("updated_at") or assay.get("created_at")),
            ]
        )
    return rows


def dashboard_recent_study_rows(studies: list[dict] | None, selected_study_id: int | None) -> list[list[object]]:
    """Build recent-study rows with an explicit open action."""

    rows: list[list[object]] = []
    for study in studies or []:
        study_id = study.get("id")
        is_selected = study_id == selected_study_id
        rows.append(
            [
                html.Div(
                    dbc.Button(
                        "Selected" if is_selected else "Open",
                        id={"type": "dashboard-study-open-btn", "index": study_id},
                        n_clicks=0,
                        color="primary" if is_selected else "outline-secondary",
                        size="sm",
                        className="w-100",
                        disabled=is_selected,
                    )
                ),
                study_id,
                study.get("title"),
                state_badge(study.get("status")),
                study.get("assay_count", 0),
                timestamp_cell(study.get("updated_at") or study.get("created_at")),
            ]
        )
    return rows


def short_error_message(message: str | None, limit: int = 72) -> str:
    """Trim long workflow errors so the dashboard table stays readable."""

    if not message:
        return "-"
    text = str(message).strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def normalize_optional_int(value) -> int | None:
    """Normalize optional numeric IDs from Dash stores and API payloads."""

    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
