from datetime import datetime
import time

import dash
from dash import ALL, Input, Output, State

from constants import STUDY_STATUS_ORDER
from callbacks.clicks import real_click
from services import study_service
from services.api_client import APIError
from services.page_cache import build_page_cache, page_cache_matches
from ui.components import build_selection_reminder, render_scrollable_table
from ui.formatting import pretty, resolve_page_slug, status_label
from ui.rows import assay_rows, study_rows


STUDY_TABLE_HEADERS = ["Select", "ID", "Identifier", "Title", "Samples", "Assays", "Status", "Created", ""]
ASSAY_TABLE_HEADERS = ["Select", "ID", "Name", "Kind", "Status", "Default", "Updated"]


def _context_rows(rows, context, token, *, study_id=None) -> list:
    if not page_cache_matches(context, token, study_id=study_id):
        return []
    return rows if isinstance(rows, list) else []


def _cache_rows_after_fetch_failure(rows, context, token, *, study_id=None):
    if page_cache_matches(context, token, study_id=study_id):
        return _context_rows(rows, context, token, study_id=study_id), dash.no_update
    return [], build_page_cache([], token, study_id=study_id)


def _reuse_rows_on_route(triggered_id, rows, context, token, *, study_id=None) -> bool:
    return triggered_id == "url" and bool(_context_rows(rows, context, token, study_id=study_id))


def _study_table(rows, selected_study_id):
    return render_scrollable_table(STUDY_TABLE_HEADERS, study_rows(rows, selected_study_id))


def _assay_table(rows, selected_assay_id):
    return render_scrollable_table(ASSAY_TABLE_HEADERS, assay_rows(rows, selected_assay_id))


def preferred_assay_id(assays: list[dict]) -> int | None:
    """The assay a newly selected study should open on.

    The one with the newest live workflow job, since that is where work is
    happening; then the study's default assay; then the first. Applies wherever
    a study is selected, so the choice never depends on which page it was.
    """
    def job_time(assay):
        value = assay.get("last_workflow_job_at")
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
        except ValueError:
            return None

    with_jobs = [(job_time(a), a) for a in assays if job_time(a) is not None]
    if with_jobs:
        return int(max(with_jobs, key=lambda pair: pair[0])[1]["id"])
    default_assay = next((a for a in assays if a.get("is_default")), None)
    if default_assay is not None:
        return int(default_assay["id"])
    return int(assays[0]["id"]) if assays else None


def register_callbacks(app):
    @app.callback(
        Output("study-status", "children"),
        Output("study-list-table", "children"),
        Output("studies-store", "data"),
        Output("studies-context-store", "data"),
        Output("page-data-mutation-signal", "data", allow_duplicate=True),
        Input("url", "pathname"),
        Input("token-store", "data"),
        Input("study-refresh-btn", "n_clicks"),
        Input("study-create-btn", "n_clicks"),
        Input("study-transition-refresh-signal", "data"),
        State("study-title", "value"),
        State("study-description", "value"),
        State("study-select-store", "data"),
        State("studies-store", "data"),
        State("studies-context-store", "data"),
        State("page-data-mutation-signal", "data"),
        prevent_initial_call=True,
    )
    def create_or_refresh_studies(pathname, token, _refresh_clicks, create_clicks, _transition_refresh_signal, title, desc, selected_study_id, current_studies, current_context, mutation_signal):
        trigger = dash.ctx.triggered_id
        normalized, _ = resolve_page_slug(pathname)
        if trigger == "url" and normalized != "studies":
            return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update
        if _reuse_rows_on_route(trigger, current_studies, current_context, token):
            studies = _context_rows(current_studies, current_context, token)
            return "", _study_table(studies, selected_study_id), dash.no_update, dash.no_update, dash.no_update
        if not token:
            return "Login first", _study_table([], selected_study_id), [], build_page_cache([], token), dash.no_update
        create_clicks = create_clicks or 0
        mutation_update = dash.no_update
        try:
            if trigger == "study-create-btn" and create_clicks > 0:
                study_service.create_study(token, title, desc)
                mutation_update = int(mutation_signal or 0) + 1
            studies = study_service.list_studies(token)
            table = _study_table(studies, selected_study_id)
            context = build_page_cache([], token)
            if trigger == "study-transition-refresh-signal":
                return f"Refreshed {len(studies)} studies after status change", table, studies, context, dash.no_update
            return "", table, studies, context, mutation_update
        except APIError as exc:
            studies, context = _cache_rows_after_fetch_failure(current_studies, current_context, token)
            return f"Study operation failed: {exc}", _study_table(studies, selected_study_id), (dash.no_update if context is dash.no_update else studies), context, mutation_update

    @app.callback(
        Output("study-select-store", "data"),
        Input({"type": "study-select-btn", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def select_study_from_list(_):
        triggered = dash.ctx.triggered
        if not triggered:
            return dash.no_update
        if (triggered[0].get("value") or 0) < 1:
            return dash.no_update
        trigger = dash.ctx.triggered_id
        if not isinstance(trigger, dict):
            return dash.no_update
        selected_id = trigger.get("index")
        if selected_id is None:
            return dash.no_update
        return int(selected_id)

    @app.callback(
        Output("study-select-store", "data", allow_duplicate=True),
        Input("studies-store", "data"),
        Input("studies-context-store", "data"),
        Input("token-store", "data"),
        State("study-select-store", "data"),
        prevent_initial_call=True,
    )
    def clear_invalid_study_selection(studies_data, studies_context, token, selected_study_id):
        if not selected_study_id:
            return dash.no_update
        studies = _context_rows(studies_data, studies_context, token)
        valid_ids = {study.get("id") for study in studies if study.get("id") is not None}
        if selected_study_id in valid_ids:
            return dash.no_update
        return None

    @app.callback(
        Output("study-list-table", "children", allow_duplicate=True),
        Input("study-select-store", "data"),
        Input("token-store", "data"),
        Input("studies-context-store", "data"),
        State("studies-store", "data"),
        prevent_initial_call=True,
    )
    def refresh_study_table_selection(selected_study_id, token, studies_context, studies_data):
        studies = _context_rows(studies_data, studies_context, token)
        return _study_table(studies, selected_study_id)

    @app.callback(
        Output("sidebar-selection-reminder", "children"),
        Input("study-select-store", "data"),
        Input("studies-store", "data"),
        Input("studies-context-store", "data"),
        Input("assay-select-store", "data"),
        Input("assays-store", "data"),
        Input("assays-context-store", "data"),
        Input("token-store", "data"),
    )
    def render_selected_study(selected_study_id, studies_data, studies_context, selected_assay_id, assays_data, assays_context, token):
        studies = _context_rows(studies_data, studies_context, token)
        assays = _context_rows(assays_data, assays_context, token, study_id=selected_study_id)
        return build_selection_reminder(selected_study_id, studies, selected_assay_id, assays, compact=True)

    @app.callback(
        Output("assay-status", "children"),
        Output("assay-list-table", "children"),
        Output("assays-store", "data"),
        Output("assays-context-store", "data"),
        Output("page-data-mutation-signal", "data", allow_duplicate=True),
        Input("url", "pathname"),
        Input("token-store", "data"),
        Input("study-select-store", "data"),
        Input("assay-refresh-btn", "n_clicks"),
        Input("assay-create-btn", "n_clicks"),
        State("assay-name", "value"),
        State("assay-type", "value"),
        State("assay-select-store", "data"),
        State("assays-store", "data"),
        State("assays-context-store", "data"),
        State("page-data-mutation-signal", "data"),
        prevent_initial_call=True,
    )
    def create_or_refresh_assays(pathname, token, study_id, _refresh_clicks, create_clicks, assay_name, assay_type, selected_assay_id, current_assays, current_context, mutation_signal):
        trigger = dash.ctx.triggered_id
        empty_table = _assay_table([], selected_assay_id)
        normalized, _ = resolve_page_slug(pathname)
        if trigger == "url" and normalized != "studies":
            return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update
        if _reuse_rows_on_route(trigger, current_assays, current_context, token, study_id=study_id):
            assays = _context_rows(current_assays, current_context, token, study_id=study_id)
            return "", _assay_table(assays, selected_assay_id), dash.no_update, dash.no_update, dash.no_update
        if not token or not study_id:
            return "Login and select a study first.", empty_table, [], build_page_cache([], token, study_id=study_id), dash.no_update

        create_clicks = create_clicks or 0
        mutation_update = dash.no_update
        try:
            if trigger == "assay-create-btn" and create_clicks > 0:
                study_service.create_study_assay(token, study_id, assay_name, assay_type)
                mutation_update = int(mutation_signal or 0) + 1
            assays = study_service.list_study_assays(token, study_id)
            return "", _assay_table(assays, selected_assay_id), assays, build_page_cache([], token, study_id=study_id), mutation_update
        except (APIError, ValueError) as exc:
            assays, context = _cache_rows_after_fetch_failure(current_assays, current_context, token, study_id=study_id)
            return f"Assay operation failed: {exc}", _assay_table(assays, selected_assay_id), (dash.no_update if context is dash.no_update else assays), context, mutation_update

    @app.callback(
        Output("assay-select-store", "data"),
        Input({"type": "assay-select-btn", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def select_assay_from_list(_):
        triggered = dash.ctx.triggered
        if not triggered:
            return dash.no_update
        if (triggered[0].get("value") or 0) < 1:
            return dash.no_update
        trigger = dash.ctx.triggered_id
        if not isinstance(trigger, dict):
            return dash.no_update
        selected_id = trigger.get("index")
        if selected_id is None:
            return dash.no_update
        return int(selected_id)

    @app.callback(
        Output("assay-select-store", "data", allow_duplicate=True),
        Input("study-select-store", "data"),
        Input("assays-store", "data"),
        Input("assays-context-store", "data"),
        Input("token-store", "data"),
        State("assay-select-store", "data"),
        prevent_initial_call=True,
    )
    def ensure_valid_selected_assay(study_id, assays_data, assays_context, token, selected_assay_id):
        if not study_id:
            return None
        assays = [
            assay for assay in _context_rows(assays_data, assays_context, token, study_id=study_id)
            if assay.get("study_id") == study_id
        ]
        if not assays:
            return None
        valid_ids = {assay.get("id") for assay in assays if assay.get("id") is not None}
        if selected_assay_id in valid_ids:
            return dash.no_update
        return preferred_assay_id(assays)

    @app.callback(
        Output("assay-list-table", "children", allow_duplicate=True),
        Input("assay-select-store", "data"),
        Input("study-select-store", "data"),
        Input("token-store", "data"),
        Input("assays-context-store", "data"),
        State("assays-store", "data"),
        prevent_initial_call=True,
    )
    def refresh_assay_table_selection(selected_assay_id, study_id, token, assays_context, assays_data):
        assays = _context_rows(assays_data, assays_context, token, study_id=study_id)
        return _assay_table(assays, selected_assay_id)

    @app.callback(
        Output("study-transition", "options"),
        Output("study-transition", "value"),
        Output("study-transition-help", "children"),
        Output("study-transition-btn", "disabled"),
        Input("study-select-store", "data"),
        Input("studies-store", "data"),
        Input("studies-context-store", "data"),
        Input("study-transition-refresh-signal", "data"),
        Input("token-store", "data"),
    )
    def update_transition_choices(selected_study_id, studies_data, studies_context, _, token):
        studies = _context_rows(studies_data, studies_context, token)
        current_status = None
        status_fetch_error = None

        selected_study = next((study for study in studies if study.get("id") == selected_study_id), None)
        if not selected_study_id or selected_study is None:
            return [], None, "Select a study to see allowed transitions.", True

        if token:
            try:
                current_status = study_service.get_study_status(token, selected_study_id)
                current_status = (current_status or "").strip().lower()
            except APIError as exc:
                status_fetch_error = str(exc)

        if not current_status:
            current_status = (selected_study.get("status") or "").strip().lower()
        if not current_status:
            return [], None, "Refresh studies to load current status and transition choices.", True

        transition_targets = [status for status in STUDY_STATUS_ORDER if status != current_status]
        options = [{"label": f"{status_label(target)} ({target})", "value": target} for target in transition_targets]
        if not options:
            base = f"Current status: {status_label(current_status)}. No transition target is available."
            if status_fetch_error:
                base = f"{base} Status refresh warning: {status_fetch_error}"
            return [], None, base, True

        help_text = (
            f"Current status: {status_label(current_status)}. "
            "Operator/Admin can transition to any other status."
        )
        if status_fetch_error:
            help_text = f"{help_text} Status refresh warning: {status_fetch_error}"
        return options, transition_targets[0], help_text, False

    @app.callback(
        Output("study-action-status", "children"),
        Output("study-transition-refresh-signal", "data"),
        Input("study-submit-btn", "n_clicks"),
        Input("study-reopen-btn", "n_clicks"),
        Input("study-transition-btn", "n_clicks"),
        State("token-store", "data"),
        State("study-select-store", "data"),
        State("study-transition", "value"),
        prevent_initial_call=True,
    )
    def study_actions(_, __, ___, token, study_id, transition_status):
        # Anything but Submit or Reopen falls through to a transition request.
        if not real_click():
            return dash.no_update, dash.no_update
        if not token or not study_id:
            return "Login and select a study", dash.no_update
        try:
            if dash.ctx.triggered_id == "study-submit-btn":
                res = study_service.submit_study(token, study_id)
            elif dash.ctx.triggered_id == "study-reopen-btn":
                res = study_service.reopen_study(token, study_id)
            else:
                res = study_service.transition_study(token, study_id, transition_status)
            return pretty(res), time.time_ns()
        except (APIError, ValueError) as exc:
            return f"Study action failed: {exc}", dash.no_update
