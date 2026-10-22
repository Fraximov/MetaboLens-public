"""Edit assay acquisition metadata independently of study samples."""
import dash
from dash import Input, Output, State, dash_table, html

from callbacks.clicks import real_click
from services.api_client import APIError, call_api
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches
from ui.components import REVIEW_TABLE_STYLE
from ui.formatting import resolve_page_slug


def render_acquisitions(acquisitions):
    rows = [dict(id=a["id"], sample=f"#{a['sample_id']} {a['sample_name']}",
                 filename=", ".join(a["filenames"]),
                 review="Needs metadata correction" if a.get("metadata_review_required") else "",
                 **{key: (a.get("annotations_json") or {}).get(key, "")
                    for key in ("batch_id", "injection_order", "vial", "acquired_at", "injection_volume_ul")})
            for a in acquisitions]
    return dash_table.DataTable(id="acquisition-metadata-table", data=rows, page_size=25, editable=True,
        columns=[{"name": label, "id": key, "editable": editable, **({"type": "numeric"} if key in {"injection_order", "injection_volume_ul"} else {})}
                 for key, label, editable in [("id", "Acquisition", False), ("sample", "Study sample", False),
                    ("filename", "File", False), ("batch_id", "Batch", True), ("injection_order", "Injection order", True),
                    ("vial", "Vial", True), ("acquired_at", "Acquisition time", True),
                    ("injection_volume_ul", "Injection volume (µL)", True), ("review", "Review", False)]],
        **REVIEW_TABLE_STYLE)


def changed_acquisition_patches(rows, acquisitions):
    original = {a["id"]: a for a in acquisitions}
    patches = []
    for row in rows or []:
        acquisition = original.get(row["id"])
        if acquisition is None:
            raise ValueError("Acquisition list changed; refresh before saving")
        annotations = acquisition.get("annotations_json") or {}
        changes = {key: row.get(key) for key in ("batch_id", "injection_order", "vial", "acquired_at", "injection_volume_ul")
                   if row.get(key) != annotations.get(key, "")}
        if changes:
            patches.append((row["id"], {"annotations_json": changes}))
    return patches


def register_callbacks(app):
    @app.callback(Output("acquisitions-store", "data"), Input("url", "pathname"),
        Input("study-select-store", "data"), Input("assay-select-store", "data"), Input("token-store", "data"),
        Input("sample-upload-refresh-signal", "data"))
    def load(pathname, study_id, assay_id, token, _):
        if resolve_page_slug(pathname)[0] != "files":
            return dash.no_update
        try:
            data = call_api("GET", f"/assays/{assay_id}/acquisitions", token=token) if token and study_id and assay_id else []
            return build_page_cache(data, token, study_id=study_id, assay_id=assay_id)
        except APIError as exc:
            return {**build_page_cache([], token, study_id=study_id, assay_id=assay_id), "error": str(exc)}

    @app.callback(Output("acquisitions-table", "children"), Input("acquisitions-store", "data"),
        Input("study-select-store", "data"), Input("assay-select-store", "data"), Input("token-store", "data"))
    def render(cache, study_id, assay_id, token):
        if not page_cache_matches(cache, token, study_id=study_id, assay_id=assay_id):
            return html.Div("Select a study and assay to load acquisitions.")
        if cache.get("error"):
            return html.Div(f"Acquisition load failed: {cache['error']}")
        return render_acquisitions(page_cache_data(cache, []))

    @app.callback(Output("acquisitions-status", "children"),
        Output("sample-upload-refresh-signal", "data", allow_duplicate=True), Input("acquisitions-save", "n_clicks"),
        State("acquisition-metadata-table", "data"), State("acquisitions-store", "data"),
        State("study-select-store", "data"), State("assay-select-store", "data"), State("token-store", "data"),
        State("sample-upload-refresh-signal", "data"), prevent_initial_call=True)
    def save(_, rows, cache, study_id, assay_id, token, signal):
        if not real_click():
            return dash.no_update, dash.no_update
        if not page_cache_matches(cache, token, study_id=study_id, assay_id=assay_id):
            return "Refresh acquisitions before saving.", dash.no_update
        try:
            patches = changed_acquisition_patches(rows, page_cache_data(cache, []))
            for acquisition_id, payload in patches:
                call_api("PATCH", f"/assays/{assay_id}/acquisitions/{acquisition_id}", token=token, payload=payload)
            return f"Updated {len(patches)} acquisition(s).", int(signal or 0) + 1
        except (APIError, ValueError) as exc:
            return f"Acquisition save failed: {exc}", dash.no_update
