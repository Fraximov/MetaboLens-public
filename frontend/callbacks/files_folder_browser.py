"""Callbacks for browsing and importing server-visible RAW folders.

Dash only asks the backend to list folders or import RAW files from a selected
folder. Backend services own filesystem validation, duplicate handling, and
database writes; these callbacks only coordinate Files page UI state.
"""

import dash
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, html

from callbacks.clicks import real_click
from services import sample_service
from services.api_client import APIError, call_api
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches
from dash import dash_table
from ui.components import REVIEW_TABLE_STYLE
from ui.components import render_table


def register_callbacks(app):
    """Wire folder browse and import callbacks for RAW folder loading."""

    @app.callback(
        Output("sample-folder-browser-store", "data"),
        Output("sample-scan-folder-path", "value"),
        Output("sample-scan-status", "children", allow_duplicate=True),
        Input("sample-browse-folder-btn", "n_clicks"),
        Input("sample-browse-up-btn", "n_clicks"),
        Input({"type": "sample-folder-entry-btn", "path": ALL}, "n_clicks"),
        State("token-store", "data"),
        State("study-select-store", "data"),
        State("sample-scan-folder-path", "value"),
        State("sample-folder-browser-store", "data"),
        prevent_initial_call=True,
    )
    def browse_sample_source_folders(_, __, ___, token, study_id, folder_path, browser_store):
        """Browse folders through the API and keep the selected path in sync."""

        if not token or not study_id:
            return dash.no_update, dash.no_update, "Login and select a study before browsing folders."

        trigger = dash.ctx.triggered_id
        target_path = folder_path
        if isinstance(trigger, dict):
            target_path = trigger.get("path")
        elif trigger == "sample-browse-up-btn":
            target_path = (browser_store or {}).get("parent_path")

        try:
            browser_data = sample_service.browse_raw_folders(token, study_id, target_path)
            current_path = browser_data.get("current_path")
            if current_path:
                status = f"Browsing {current_path!r}. Choose a subfolder or click Preview RAW Folder to review the files from the current folder."
                return browser_data, current_path, status
            return browser_data, dash.no_update, "Choose a browsable root, then drill down to the folder that contains the RAW files."
        except (APIError, ValueError) as exc:
            return dash.no_update, dash.no_update, f"Folder browse failed: {exc}"

    @app.callback(
        Output("sample-folder-browser", "children"),
        Input("sample-folder-browser-store", "data"),
    )
    def render_sample_folder_browser(browser_store):
        """Render the backend-provided directory listing without extra logic."""

        store = browser_store or {}
        entries = store.get("entries") or []
        current_path = store.get("current_path")

        if not entries:
            if current_path:
                return dbc.Alert("No subdirectories found in the current folder.", color="light", className="mb-0")
            return dbc.Alert("Use Browse Folders to select a server-visible directory root.", color="light", className="mb-0")

        rows = []
        for entry in entries:
            rows.append(
                [
                    entry.get("name"),
                    entry.get("path"),
                    entry.get("raw_file_count"),
                    dbc.Button(
                        "Open",
                        id={"type": "sample-folder-entry-btn", "path": entry.get("path")},
                        n_clicks=0,
                        size="sm",
                        color="outline-secondary",
                    ),
                ]
            )
        title = f"Folder Browser: {current_path}" if current_path else "Folder Browser: available roots"
        return dbc.Card(
            dbc.CardBody(
                [
                    html.H6(title, className="mb-2"),
                    render_table(["Name", "Path", "Top-level Input Files", "Open"], rows),
                ]
            ),
            className="mb-0",
        )

    @app.callback(Output("sample-scan-status", "children"), Output("sample-folder-preview-store", "data"),
        Input("sample-scan-folder-btn", "n_clicks"), State("token-store", "data"),
        State("study-select-store", "data"), State("assay-select-store", "data"),
        State("sample-scan-folder-path", "value"), prevent_initial_call=True)
    def preview(_, token, study_id, assay_id, folder_path):
        if not real_click():
            return dash.no_update, dash.no_update
        if not token or not study_id or not assay_id:
            return "Login and select a study and assay before previewing a folder.", None
        try:
            entries = call_api("POST", f"/studies/{study_id}/raw-paths/scan", token=token,
                               payload={"folder_path": folder_path})
            samples = sample_service.list_study_samples(token, study_id)
            rows = sample_service.build_folder_preview_rows(entries, samples)
            cache = build_page_cache({"rows": rows, "samples": samples}, token, study_id=study_id, assay_id=assay_id)
            return f"Previewing {len(rows)} new acquisition(s); skipped {len(entries) - len(rows)} registered path(s). Choose the study sample for each row before importing.", cache
        except (APIError, ValueError) as exc:
            return f"Folder preview failed: {exc}", None

    @app.callback(Output("sample-folder-preview", "children"), Input("sample-folder-preview-store", "data"),
        Input("study-select-store", "data"), Input("assay-select-store", "data"), Input("token-store", "data"))
    def render_preview(cache, study_id, assay_id, token):
        if not page_cache_matches(cache, token, study_id=study_id, assay_id=assay_id):
            return None
        data = page_cache_data(cache)
        if not data["rows"]:
            return html.Div("No new files to import.")
        return render_folder_preview(data)

    @app.callback(Output("sample-folder-import-status", "children"),
        Output("sample-upload-refresh-signal", "data", allow_duplicate=True),
        Output("sample-folder-preview-store", "data", allow_duplicate=True),
        Input("sample-confirm-folder-btn", "n_clicks"), State("folder-preview-table", "data"),
        State("sample-folder-preview-store", "data"), State("token-store", "data"),
        State("study-select-store", "data"), State("assay-select-store", "data"),
        State("sample-upload-refresh-signal", "data"), prevent_initial_call=True)
    def import_preview(_, rows, cache, token, study_id, assay_id, signal):
        if not real_click():
            return dash.no_update, dash.no_update, dash.no_update
        if not page_cache_matches(cache, token, study_id=study_id, assay_id=assay_id):
            return "Preview the folder again for the selected study and assay.", dash.no_update, dash.no_update
        try:
            imported = sample_service.register_samples_with_paths(token, study_id, assay_id, rows)
            return f"Imported {len(imported)} acquisition(s).", int(signal or 0) + 1, None
        except (APIError, ValueError) as exc:
            return f"Folder import failed: {exc}", dash.no_update, dash.no_update


def render_folder_preview(data):
    return html.Div([
        dash_table.DataTable(id="folder-preview-table", data=data["rows"], page_size=25, editable=True,
            columns=[{"id": key, "name": label, "editable": editable,
                      **({"presentation": "dropdown"} if key == "existing_sample_id" else {})}
                     for key, label, editable in [("filename", "File", False),
                         ("existing_sample_id", "Study sample", True), ("sample_code", "New sample name", True),
                         ("batch_id", "Batch", True), ("injection_order", "Injection order", True)]],
            dropdown={"existing_sample_id": {"options": sample_service.sample_dropdown_options(data["samples"])}},
            **REVIEW_TABLE_STYLE),
        dbc.Button("Import previewed acquisitions", id="sample-confirm-folder-btn", n_clicks=0,
                   color="primary", className="mt-2")])
