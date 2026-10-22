"""Callbacks for editable downstream sample metadata on Files."""

import dash
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, html

from constants import SAMPLE_TYPE_OPTIONS
from callbacks.clicks import real_click
from services import sample_service
from services.api_client import APIError
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches, should_reuse_page_cache
from ui.formatting import resolve_page_slug


SAMPLE_TABLE_HEADERS = [
    "ID",
    "Sample",
    "Sample Type",
    "Matrix",
    "Group Label",
    "Remove",
]
SAMPLE_METADATA_PAGE_SIZE = 25


def _should_reuse_samples_cache(triggered_id, cache, token, study_id) -> bool:
    return should_reuse_page_cache(
        cache,
        token,
        triggered_id=triggered_id,
        passive_trigger_ids={"url"},
        study_id=study_id,
    )


def cache_samples_after_fetch_failure(current_store, token, study_id, exc):
    warning = f"Study samples refresh failed: {exc}"
    if page_cache_matches(current_store, token, study_id=study_id):
        cache = dict(current_store)
        cache.pop("error", None)
        cache["warning"] = warning
        return cache

    cache = build_page_cache([], token, study_id=study_id)
    cache["error"] = f"Study samples load failed: {exc}"
    return cache


def render_cached_samples_table(samples_store, token, study_id, page: int = 1):
    if not token or not study_id:
        return html.Div("Login and select a study to load study samples.", className="section-helper compact mb-0")

    if not page_cache_matches(samples_store, token, study_id=study_id):
        return html.Div("Study samples are loading.", className="section-helper compact mb-0")

    if samples_store.get("error"):
        return html.Div(samples_store["error"], className="section-helper compact mb-0")

    table = render_editable_sample_metadata_table(page_cache_data(samples_store, []), page=page)
    if samples_store.get("warning"):
        return html.Div(
            [dbc.Alert(samples_store["warning"], color="warning", className="mb-2"), table]
        )
    return table


def register_callbacks(app):
    """Render and save study-scoped sample metadata required downstream."""

    @app.callback(
        Output("samples-page-store", "data"),
        Input("url", "pathname"),
        Input("token-store", "data"),
        Input("sample-list-btn", "n_clicks"),
        Input("study-select-store", "data"),
        Input("sample-upload-refresh-signal", "data"),
        State("samples-page-store", "data"),
    )
    def load_study_samples_table(pathname, token, _refresh_clicks, study_id, _upload_refresh_signal, current_store):
        normalized, _ = resolve_page_slug(pathname)
        if normalized != "files":
            return dash.no_update

        if _should_reuse_samples_cache(dash.ctx.triggered_id, current_store, token, study_id):
            return dash.no_update

        if not token or not study_id:
            return build_page_cache([], token, study_id=study_id)

        try:
            samples = sample_service.list_study_samples(token, study_id)
            return build_page_cache(samples, token, study_id=study_id)
        except (APIError, ValueError) as exc:
            return cache_samples_after_fetch_failure(current_store, token, study_id, exc)

    @app.callback(
        Output("sample-list-table", "children"),
        Output("sample-metadata-page-summary", "children"),
        Output("sample-metadata-pagination", "max_value"),
        Output("sample-metadata-pagination", "style"),
        Output("sample-metadata-pagination", "active_page"),
        Input("samples-page-store", "data"),
        Input("url", "pathname"),
        Input("token-store", "data"),
        Input("study-select-store", "data"),
        Input("sample-metadata-pagination", "active_page"),
    )
    def render_study_samples_table(samples_store, pathname, token, study_id, active_page):
        normalized, _ = resolve_page_slug(pathname)
        if normalized != "files":
            return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update

        samples = page_cache_data(samples_store, []) if page_cache_matches(samples_store, token, study_id=study_id) else []
        active_page, _, _, page_count = _sample_metadata_page_bounds(len(samples), active_page)
        return (
            render_cached_samples_table(samples_store, token, study_id, page=active_page),
            sample_metadata_page_summary(len(samples), page=active_page),
            page_count,
            {} if page_count > 1 else {"display": "none"},
            active_page,
        )


    @app.callback(
        Output("sample-metadata-save-status", "children"),
        Output("sample-upload-refresh-signal", "data", allow_duplicate=True),
        Input("sample-metadata-save-btn", "n_clicks"),
        State("url", "pathname"),
        State("token-store", "data"),
        State("study-select-store", "data"),
        State({"type": "sample-matrix-input", "sample_id": ALL}, "id"),
        State({"type": "sample-type-input", "sample_id": ALL}, "value"),
        State({"type": "sample-matrix-input", "sample_id": ALL}, "value"),
        State({"type": "sample-group-label-input", "sample_id": ALL}, "value"),
        State("sample-upload-refresh-signal", "data"),
        prevent_initial_call=True,
    )
    def save_sample_metadata(_, pathname, token, study_id, sample_ids, sample_types, matrices, group_labels, refresh_signal):
        if not real_click():
            return dash.no_update, dash.no_update
        normalized, _ = resolve_page_slug(pathname)
        if normalized != "files":
            return dash.no_update, dash.no_update
        if not token or not study_id:
            return "Login and select a study before saving sample metadata.", dash.no_update
        if not sample_ids:
            return "No samples to update.", dash.no_update

        try:
            rows = []
            for idx, sample_id_payload in enumerate(sample_ids):
                rows.append(
                    {
                        "sample_id": int(sample_id_payload["sample_id"]),
                        "sample_type": choose_value(sample_types, idx),
                        "matrix": choose_value(matrices, idx),
                        "group_label": choose_value(group_labels, idx),
                    }
                )
            summary = sample_service.update_sample_metadata_rows(token, study_id, rows)
            updated = int(summary.get("updated_count") or 0)
            skipped = int(summary.get("skipped_missing_count") or 0)
            if skipped:
                return f"Updated downstream metadata for {updated} sample(s). Skipped {skipped} stale row(s); the table has been refreshed.", int(refresh_signal or 0) + 1
            return f"Updated downstream metadata for {updated} sample(s).", int(refresh_signal or 0) + 1
        except (APIError, ValueError, KeyError) as exc:
            return f"Sample metadata update failed: {exc}", dash.no_update

    @app.callback(
        Output("sample-metadata-save-status", "children", allow_duplicate=True),
        Output("sample-upload-refresh-signal", "data", allow_duplicate=True),
        Input({"type": "sample-delete-btn", "sample_id": ALL}, "n_clicks"),
        State("url", "pathname"),
        State("token-store", "data"),
        State("study-select-store", "data"),
        State({"type": "sample-delete-btn", "sample_id": ALL}, "id"),
        State("sample-upload-refresh-signal", "data"),
        prevent_initial_call=True,
    )
    def delete_sample_from_study(clicks, pathname, token, study_id, button_ids, refresh_signal):
        normalized, _ = resolve_page_slug(pathname)
        if normalized != "files":
            return dash.no_update, dash.no_update

        sample_id = clicked_delete_sample_id(dash.ctx.triggered_id, button_ids, clicks)
        if sample_id is None:
            return dash.no_update, dash.no_update
        if not token or not study_id:
            return "Login and select a study before removing a sample.", dash.no_update

        try:
            summary = sample_service.delete_study_sample(token, study_id, sample_id)
            deleted_files = int(summary.get("deleted_raw_file_count") or 0)
            return f"Removed sample #{sample_id} and {deleted_files} RAW file registration(s).", int(refresh_signal or 0) + 1
        except (APIError, ValueError, KeyError) as exc:
            return f"Sample removal failed: {exc}", dash.no_update


def _sample_metadata_page_bounds(total: int, page: int) -> tuple[int, int, int, int]:
    page_count = max(1, (total + SAMPLE_METADATA_PAGE_SIZE - 1) // SAMPLE_METADATA_PAGE_SIZE)
    active_page = min(max(int(page or 1), 1), page_count)
    start = (active_page - 1) * SAMPLE_METADATA_PAGE_SIZE
    return active_page, start, min(start + SAMPLE_METADATA_PAGE_SIZE, total), page_count


def sample_metadata_page_summary(total: int, page: int = 1) -> str:
    if total <= 0:
        return "No samples to display."
    _, start, end, _ = _sample_metadata_page_bounds(total, page)
    return f"Showing samples {start + 1}–{end} of {total}. Save this page before changing pages."


def render_editable_sample_metadata_table(samples: list[dict], page: int = 1) -> html.Div:
    if not samples:
        return html.Div("No samples registered for this study yet.", className="section-helper compact mb-0")

    _, page_start, page_end, _ = _sample_metadata_page_bounds(len(samples), page)
    body_rows = []
    for sample in samples[page_start:page_end]:
        sample_id = sample.get("id")
        annotations = sample.get("annotations_json") or {}
        body_rows.append(
            html.Tr(
                [
                    html.Td(sample_id),
                    html.Td(sample.get("name")),
                    html.Td(
                        dbc.Select(
                            id={"type": "sample-type-input", "sample_id": sample_id},
                            options=SAMPLE_TYPE_OPTIONS,
                            value=sample_service.normalize_sample_type_value(sample.get("sample_type")),
                            className="sample-metadata-control",
                        )
                    ),
                    html.Td(
                        dbc.Input(
                            id={"type": "sample-matrix-input", "sample_id": sample_id},
                            type="text",
                            value=str(sample.get("matrix") or ""),
                            placeholder="plasma, serum, liver...",
                            className="sample-metadata-control",
                        )
                    ),
                    html.Td(
                        dbc.Input(
                            id={"type": "sample-group-label-input", "sample_id": sample_id},
                            type="text",
                            value=str(annotations.get("group_label") or ""),
                            placeholder="control, treated...",
                            className="sample-metadata-control",
                        )
                    ),
                    html.Td(
                        dbc.Button(
                            "Remove",
                            id={"type": "sample-delete-btn", "sample_id": sample_id},
                            n_clicks=0,
                            color="outline-danger",
                            size="sm",
                            className="sample-remove-btn",
                        )
                    ),
                ]
            )
        )

    return html.Div(
        dbc.Table(
            [
                html.Thead(html.Tr([html.Th(header) for header in SAMPLE_TABLE_HEADERS])),
                html.Tbody(body_rows),
            ],
            bordered=True,
            striped=True,
            hover=True,
            responsive=True,
            className="embl-table align-middle mb-0 sample-metadata-table",
        ),
        className="table-scroll-panel",
        style={"maxHeight": "320px"},
    )


def choose_value(values, index: int):
    return values[index] if index < len(values or []) else None


def clicked_delete_sample_id(triggered_id, button_ids, click_counts) -> int | None:
    if not triggered_id or not isinstance(triggered_id, dict):
        return None
    try:
        sample_id = int(triggered_id["sample_id"])
    except (KeyError, TypeError, ValueError):
        return None

    for idx, button_id in enumerate(button_ids or []):
        if not isinstance(button_id, dict):
            continue
        try:
            if int(button_id.get("sample_id")) != sample_id:
                continue
        except (TypeError, ValueError):
            continue
        click_count = choose_value(click_counts, idx)
        if click_count and int(click_count) > 0:
            return sample_id
        return None
    return None
