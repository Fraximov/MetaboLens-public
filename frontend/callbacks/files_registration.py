"""Callbacks for secondary CSV sample/path registration on Files."""

import dash
from dash import Input, Output, State

from services import sample_service
from services.api_client import APIError
from ui.formatting import resolve_page_slug


def register_callbacks(app):
    """Wire CSV upload and template download callbacks."""

    @app.callback(
        Output("sample-csv-status", "children"),
        Output("sample-upload-refresh-signal", "data"),
        Input("sample-csv-upload", "contents"),
        State("sample-csv-upload", "filename"),
        State("url", "pathname"),
        State("token-store", "data"),
        State("study-select-store", "data"),
        State("assay-select-store", "data"),
        State("sample-upload-refresh-signal", "data"),
        prevent_initial_call=True,
    )
    def import_csv_sample_paths(contents, filename, pathname, token, study_id, assay_id, refresh_signal):
        """Register strict sample/path rows from a CSV upload."""

        normalized, _ = resolve_page_slug(pathname)
        if normalized != "files" or not contents:
            return dash.no_update, dash.no_update
        if not token or not study_id:
            return "Login and select a study before importing CSV rows.", dash.no_update
        if not assay_id:
            return "Select an assay before importing CSV rows.", dash.no_update
        try:
            rows = sample_service.parse_sample_import_csv(contents)
            created_samples = sample_service.register_samples_with_paths(
                token=token,
                study_id=study_id,
                assay_id=assay_id,
                sample_rows=rows,
            )
            source_name = filename or "CSV"
            return f"Registered {len(created_samples)} sample-path pair(s) from {source_name}.", int(refresh_signal or 0) + 1
        except (APIError, ValueError) as exc:
            return f"CSV import failed: {exc}", dash.no_update

    @app.callback(
        Output("sample-template-download", "data"),
        Input("sample-template-download-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def download_sample_template(_):
        """Serve the canonical CSV import template from the sample service."""

        return {
            "content": sample_service.sample_import_template_csv(),
            "filename": "sample_import_template.csv",
            "type": "text/csv",
        }
