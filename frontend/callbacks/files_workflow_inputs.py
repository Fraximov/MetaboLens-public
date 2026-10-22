"""Callbacks for selecting assay files as workflow worker inputs.

This module prepares the UI-side file selection contract. It does not launch
workflows; the Workflows page later sends selected IDs to the backend launch
API, and long-running execution remains in the worker.
"""

import dash
from dash import Input, Output, State

from services import file_service, workflow_service
from services.api_client import APIError
from ui.components import render_table
from ui.formatting import resolve_page_slug
from ui.rows import worker_input_file_rows


WORKFLOW_FILE_HEADERS = [
    "File ID",
    "Filename",
    "Role",
    "Dataset Kind",
    "Status",
    "Linked Sample",
    "Linked Samples",
    "Selection",
]


def register_callbacks(app):
    """Wire workflow input file hydration and selection-mode state."""

    @app.callback(
        Output("wf-file-select", "options"),
        Output("wf-file-select", "value"),
        Output("wf-file-list-table", "children"),
        Output("wf-file-selection-summary", "children"),
        Output("workflow-file-selection-store", "data"),
        Input("url", "pathname"),
        Input("study-select-store", "data"),
        Input("assay-select-store", "data"),
        Input("wf-file-refresh-btn", "n_clicks"),
        Input("sample-upload-refresh-signal", "data"),
        Input("wf-file-selection-mode", "value"),
        Input("wf-file-select", "value"),
        State("token-store", "data"),
        State("workflow-file-selection-store", "data"),
    )
    def sync_worker_input_files(pathname, study_id, assay_id, _refresh_clicks, _upload_refresh_signal, selection_mode, dropdown_values, token, stored_selection):
        """Load assay files and keep selected worker input IDs in session state."""

        normalized, _ = resolve_page_slug(pathname)
        if normalized != "files":
            return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update

        current_store = stored_selection or {}
        mode = selection_mode or current_store.get("mode", "all")

        if not token or not study_id or not assay_id:
            return empty_file_selection_response(study_id, assay_id, mode)

        trigger = dash.ctx.triggered_id
        should_fetch = should_refresh_files(trigger, current_store, study_id, assay_id)
        files = current_store.get("available_files", [])
        workflow_output_ids = set(current_store.get("workflow_output_file_ids", []))
        fetch_error = None
        if should_fetch:
            try:
                files = file_service.list_assay_files(
                    token,
                    assay_id,
                    workflow_inputs_only=True,
                )
                workflow_outputs = workflow_service.list_workflow_output_files(token, assay_id)
                workflow_output_ids = {int(item.get("file_id")) for item in workflow_outputs if item.get("file_id") is not None}
            except APIError as exc:
                fetch_error = f"File list refresh failed: {exc}"

        input_files = [
            file_item
            for file_item in files
            if file_item.get("id") not in workflow_output_ids
            and file_item.get("dataset_kind") == "raw"
            and file_item.get("file_role") == "thermo_raw"
            and file_item.get("status") in {"registered", "uploaded", "validated"}
            and file_item.get("sample_id") is not None
        ]
        options = workflow_file_options(input_files)
        available_file_ids = [option["value"] for option in options]
        selected_dropdown_ids = selected_dropdown_file_ids(
            dropdown_values=dropdown_values,
            current_store=current_store,
            study_id=study_id,
            available_file_ids=available_file_ids,
        )
        selected_file_ids, summary = selected_files_and_summary(mode, selected_dropdown_ids, available_file_ids)
        dropdown_selection = selected_dropdown_ids if mode == "selected" else []

        if workflow_output_ids:
            summary = f"{summary} Excluded {len(workflow_output_ids)} workflow output file(s)."
        if fetch_error:
            summary = f"{summary} {fetch_error}"

        table = render_table(
            WORKFLOW_FILE_HEADERS,
            worker_input_file_rows(input_files, None if selected_file_ids is None else set(selected_file_ids)),
        )
        new_store = workflow_file_selection_store(
            study_id=study_id,
            assay_id=assay_id,
            mode=mode,
            selected_file_ids=selected_file_ids,
            selected_dropdown_ids=dropdown_selection,
            input_files=input_files,
            workflow_output_ids=workflow_output_ids,
        )
        return options, dropdown_selection, table, summary, new_store


def empty_file_selection_response(study_id, assay_id, mode):
    """Return the empty contract used before login/study/assay selection."""

    empty_store = workflow_file_selection_store(
        study_id=study_id,
        assay_id=assay_id,
        mode=mode,
        selected_file_ids=[],
        selected_dropdown_ids=[],
        input_files=[],
        workflow_output_ids=set(),
    )
    empty_table = render_table(WORKFLOW_FILE_HEADERS, [])
    return [], [], empty_table, "Login and select a study and assay to load files.", empty_store


def should_refresh_files(trigger, current_store: dict, study_id, assay_id) -> bool:
    """Decide when the UI must refetch assay files from the API."""

    return (
        trigger in {"wf-file-refresh-btn", "sample-upload-refresh-signal"}
        or current_store.get("study_id") != study_id
        or current_store.get("assay_id") != assay_id
        or not current_store.get("available_files")
    )


def workflow_file_options(input_files: list[dict]) -> list[dict]:
    """Build dropdown choices from assay files that are not workflow outputs."""

    return [
        {
            "label": f"#{file_item.get('id')} {file_item.get('original_filename')} ({file_item.get('status')})",
            "value": file_item.get("id"),
        }
        for file_item in input_files
        if file_item.get("id") is not None
    ]


def selected_dropdown_file_ids(*, dropdown_values, current_store: dict, study_id, available_file_ids: list[int]) -> list[int]:
    """Preserve selected dropdown IDs across refreshes when they are still valid."""

    previous_dropdown_ids = current_store.get("selected_dropdown_ids", []) if current_store.get("study_id") == study_id else []
    incoming_dropdown_ids = dropdown_values if dropdown_values is not None else previous_dropdown_ids
    if not isinstance(incoming_dropdown_ids, list):
        incoming_dropdown_ids = [incoming_dropdown_ids]
    return [int(file_id) for file_id in incoming_dropdown_ids if file_id in available_file_ids]


def selected_files_and_summary(mode: str, selected_dropdown_ids: list[int], available_file_ids: list[int]) -> tuple[list[int] | None, str]:
    """Translate UI selection mode into the selected file IDs stored for launch."""

    if mode == "selected":
        selected_file_ids = selected_dropdown_ids
        if selected_file_ids:
            return selected_file_ids, f"{len(selected_file_ids)} file(s) selected for workflow workers."
        return selected_file_ids, "No worker input files selected. Choose one or more files, or switch to All files."

    return None, f"All eligible files selected for workers ({len(available_file_ids)} file(s))."


def workflow_file_selection_store(
    *,
    study_id,
    assay_id,
    mode: str,
    selected_file_ids: list[int] | None,
    selected_dropdown_ids: list[int],
    input_files: list[dict],
    workflow_output_ids: set[int],
) -> dict:
    """Build the session-store contract shared with workflow launch callbacks."""

    store = {
        "study_id": study_id,
        "assay_id": assay_id,
        "mode": mode,
        "selected_dropdown_ids": selected_dropdown_ids,
        "available_files": input_files,
        "workflow_output_file_ids": sorted(workflow_output_ids),
    }
    if mode == "selected":
        store["selected_file_ids"] = selected_file_ids or []
    return store
