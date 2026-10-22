import time

import dash
from dash import Input, Output, State

from services import file_service, study_service, summary_service
from services.api_client import APIError
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches
from ui.components import render_field_value_table, render_scrollable_table
from ui.figures import build_study_structure_figure, empty_structure_figure
from ui.formatting import (
    assay_kind_label,
    dataset_sample_label,
    format_activity_payload,
    format_status_counts,
    metabolomics_assay_type_label,
    resolve_page_slug,
    state_badge,
    timestamp_cell,
)


def _summary_cache_matches(cache, token, study_id, assay_id, revision) -> bool:
    return page_cache_matches(
        cache,
        token,
        study_id=study_id,
        assay_id=assay_id,
    ) and cache.get("revision") == revision


def _build_summary_cache(data, token, study_id, assay_id, revision) -> dict:
    cache = build_page_cache(data, token, study_id=study_id, assay_id=assay_id)
    cache["revision"] = revision
    return cache


def _should_reuse_summary_cache(triggered_id, cache, token, study_id, assay_id, revision) -> bool:
    return triggered_id == "url" and isinstance(cache, dict) and not cache.get("refresh_failed") and _summary_cache_matches(
        cache,
        token,
        study_id,
        assay_id,
        revision,
    ) and "files" not in page_cache_data(cache, {})  # written before datasets were summarized


def summarize_datasets(files: list[dict] | None) -> list[dict]:
    """One row per dataset, which is all the dataset table shows.

    The store keeps these rather than the file records. Dash uploads a store
    with every callback that reads it, and the file list grows with the study
    while the dataset rows barely do.
    """
    datasets_by_id: dict[int, dict] = {}
    for file_item in files or []:
        dataset_id = file_item.get("dataset_id")
        if dataset_id is None:
            continue
        entry = datasets_by_id.setdefault(
            int(dataset_id),
            {
                "dataset_id": dataset_id,
                "dataset_name": file_item.get("dataset_name"),
                "dataset_kind": file_item.get("dataset_kind"),
                "dataset_type": file_item.get("dataset_type"),
                "dataset_sample_ids": file_item.get("dataset_sample_ids") or [],
                "dataset_sample_names": file_item.get("dataset_sample_names") or [],
                "file_count": 0,
            },
        )
        entry["file_count"] += 1
    return [entry for _, entry in sorted(datasets_by_id.items())]


def _summary_refresh_failure(current_store, token, study_id, assay_id, revision, exc):
    status = f"Summary load failed: {exc}"
    if page_cache_matches(current_store, token, study_id=study_id, assay_id=assay_id):
        data = dict(page_cache_data(current_store, {}))
        data["status"] = status
        failed_cache = _build_summary_cache(data, token, study_id, assay_id, revision)
        failed_cache["refresh_failed"] = True
        return failed_cache

    return _build_summary_cache(
        {
            "status": status,
            "summary": None,
            "structure": None,
            "assays": [],
            "datasets": [],
            "activity": [],
            "study_id": study_id,
            "assay_id": assay_id,
            "loaded_at": time.time(),
        },
        token,
        study_id,
        assay_id,
        revision,
    )


def register_callbacks(app):
    @app.callback(
        Output("summary-store", "data"),
        Input("summary-refresh-btn", "n_clicks"),
        Input("url", "pathname"),
        Input("study-select-store", "data"),
        Input("assay-select-store", "data"),
        Input("token-store", "data"),
        Input("page-data-revision-store", "data"),
        State("summary-store", "data"),
    )
    def fetch_study_summary(_, pathname, study_id, assay_id, token, revision, current_summary_store):
        normalized, _ = resolve_page_slug(pathname)
        if normalized != "summary":
            return dash.no_update

        existing = current_summary_store or {}
        if _should_reuse_summary_cache(dash.ctx.triggered_id, existing, token, study_id, assay_id, revision):
            return dash.no_update

        if not token or not study_id:
            return _build_summary_cache(
                {
                    "status": "Login and select a study from Study List.",
                    "summary": None,
                    "structure": None,
                    "assays": [],
                    "datasets": [],
                    "activity": [],
                    "study_id": study_id,
                    "assay_id": assay_id,
                    "loaded_at": time.time(),
                },
                token,
                study_id,
                assay_id,
                revision,
            )

        try:
            summary = summary_service.get_study_summary(token, study_id)
            structure = summary_service.get_study_structure(token, study_id)
            assays = study_service.list_study_assays(token, study_id)
            files = file_service.list_assay_files(token, assay_id) if assay_id else []
            activity = summary_service.list_study_activity(token, study_id, limit=100)
            return _build_summary_cache(
                {
                    "status": "",
                    "summary": summary,
                    "structure": structure,
                    "assays": assays,
                    "datasets": summarize_datasets(files),
                    "activity": activity,
                    "study_id": study_id,
                    "assay_id": assay_id,
                    "loaded_at": time.time(),
                },
                token,
                study_id,
                assay_id,
                revision,
            )
        except (APIError, ValueError) as exc:
            return _summary_refresh_failure(existing, token, study_id, assay_id, revision, exc)

    @app.callback(
        Output("summary-status", "children"),
        Output("summary-overview-table", "children"),
        Output("summary-structure-chart", "figure"),
        Output("summary-assay-table", "children"),
        Output("summary-dataset-table", "children"),
        Output("summary-activity-table", "children"),
        Input("summary-store", "data"),
        Input("url", "pathname"),
        Input("token-store", "data"),
        Input("study-select-store", "data"),
        Input("assay-select-store", "data"),
        Input("page-data-revision-store", "data"),
    )
    def render_study_summary(summary_store, pathname, token, study_id, assay_id, revision):
        normalized, _ = resolve_page_slug(pathname)
        if normalized != "summary":
            return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update

        store = page_cache_data(summary_store, {}) if _summary_cache_matches(
            summary_store,
            token,
            study_id,
            assay_id,
            revision,
        ) else {}
        status_text = store.get("status", "Click refresh to load summary.")
        summary = store.get("summary")
        structure = store.get("structure")
        assays = store.get("assays", [])
        datasets = store.get("datasets", [])
        activity = store.get("activity", [])
        active_assay_id = store.get("assay_id")

        if not summary:
            return (
                status_text,
                render_field_value_table([]),
                empty_structure_figure("Select a study to load the structure view."),
                render_field_value_table([]),
                render_scrollable_table(["Dataset ID", "Name", "Kind", "Type", "Samples", "Files"], [], max_height="360px"),
                render_scrollable_table(["Time", "Actor", "Action", "Target", "Details"], [], max_height="360px"),
            )

        study = summary.get("study", {})
        overview_rows = [
            ["Study ID", study.get("id")],
            ["Identifier", study.get("public_or_internal_identifier")],
            ["Project ID", study.get("project_id")],
            ["Title", study.get("title")],
            ["Status", state_badge(study.get("status"))],
            ["Description", study.get("description") or "-"],
            ["Default Assay ID", study.get("default_assay_id")],
            ["Created By", study.get("created_by")],
            ["Created At", timestamp_cell(study.get("created_at"))],
            ["Updated At", timestamp_cell(study.get("updated_at"))],
            ["Samples", summary.get("sample_count")],
            ["Dataset Files", summary.get("dataset_file_count")],
            ["Dataset Status Counts", format_status_counts(summary.get("dataset_status_counts"))],
            ["Latest Metadata Status", summary.get("latest_metadata_status") or "-"],
            ["Latest Metadata Submitted", timestamp_cell(summary.get("latest_metadata_submitted_at"))],
            ["Workflow Jobs", summary.get("workflow_job_count")],
            ["Workflow Status Counts", format_status_counts(summary.get("workflow_status_counts"))],
        ]
        overview_table = render_field_value_table(overview_rows)
        structure_figure = build_study_structure_figure(structure)

        assay_rows = []
        active_assay = next((assay for assay in assays if assay.get("id") == active_assay_id), None)
        if active_assay is not None:
            assay = active_assay
            method = assay.get("metabolomics_assay") or {}
            lcms_details = method.get("lcms_details") or {}
            gcms_details = method.get("gcms_details") or {}
            nmr_details = method.get("nmr_details") or {}
            metabolomics_type = method.get("assay_type")
            assay_prefix = f"Assay #{assay.get('id')}"
            assay_rows.extend(
                [
                    [f"{assay_prefix} Name", assay.get("name")],
                    [f"{assay_prefix} Kind", assay_kind_label(assay)],
                    [f"{assay_prefix} Platform", assay.get("platform")],
                    [f"{assay_prefix} Status", state_badge(assay.get("status"))],
                    [f"{assay_prefix} Default", "Yes" if assay.get("is_default") else "No"],
                    [f"{assay_prefix} Created By", assay.get("created_by")],
                    [f"{assay_prefix} Created At", timestamp_cell(assay.get("created_at"))],
                    [f"{assay_prefix} Updated At", timestamp_cell(assay.get("updated_at"))],
                    [f"{assay_prefix} Metabolomics Type", metabolomics_assay_type_label(metabolomics_type)],
                    [f"{assay_prefix} Technique", method.get("assay_technique")],
                    [f"{assay_prefix} Measurement Type", method.get("measurement_type")],
                    [f"{assay_prefix} Analysis Scope", method.get("analysis_scope")],
                    [f"{assay_prefix} Analyte Domain", method.get("analyte_domain")],
                    [f"{assay_prefix} Instrument Vendor", method.get("instrument_vendor")],
                    [f"{assay_prefix} Instrument Model", method.get("instrument_model")],
                    [f"{assay_prefix} MS Type", method.get("ms_type")],
                    [f"{assay_prefix} Acquisition Software", method.get("acquisition_software")],
                ]
            )
            if metabolomics_type == "LC_MS":
                assay_rows.extend(
                    [
                        [f"{assay_prefix} Polarity", lcms_details.get("polarity")],
                        [f"{assay_prefix} Column Type", lcms_details.get("column_type")],
                        [f"{assay_prefix} Guard Column Type", lcms_details.get("column_guard_type")],
                        [f"{assay_prefix} Chromatography Instrument", lcms_details.get("chromatography_instrument")],
                        [f"{assay_prefix} Column Model", lcms_details.get("column_model")],
                        [f"{assay_prefix} m/z Range", lcms_details.get("scan_mz_range")],
                        [f"{assay_prefix} Ion Source", lcms_details.get("ion_source")],
                        [f"{assay_prefix} Mass Analyzer", lcms_details.get("mass_analyzer")],
                    ]
                )
            elif metabolomics_type == "GC_MS":
                assay_rows.extend(
                    [
                        [f"{assay_prefix} Derivatization", gcms_details.get("derivatization")],
                        [f"{assay_prefix} Column Type", gcms_details.get("column_type")],
                        [f"{assay_prefix} Guard Column Type", gcms_details.get("column_guard_type")],
                        [f"{assay_prefix} Chromatography Instrument", gcms_details.get("chromatography_instrument")],
                        [f"{assay_prefix} Column Model", gcms_details.get("column_model")],
                        [f"{assay_prefix} m/z Range", gcms_details.get("scan_mz_range")],
                        [f"{assay_prefix} Ion Source", gcms_details.get("ion_source")],
                        [f"{assay_prefix} Mass Analyzer", gcms_details.get("mass_analyzer")],
                    ]
                )
            elif metabolomics_type == "NMR":
                assay_rows.extend(
                    [
                        [f"{assay_prefix} NMR Tube Type", nmr_details.get("nmr_tube_type")],
                        [f"{assay_prefix} Solvent", nmr_details.get("solvent")],
                        [f"{assay_prefix} Sample pH", nmr_details.get("sample_ph")],
                        [f"{assay_prefix} Temperature", nmr_details.get("temperature")],
                        [f"{assay_prefix} Probe", nmr_details.get("nmr_probe")],
                        [f"{assay_prefix} Number of Transients", nmr_details.get("number_of_transients")],
                        [f"{assay_prefix} Pulse Sequence", nmr_details.get("pulse_sequence_name")],
                        [f"{assay_prefix} Magnetic Field Strength", nmr_details.get("magnetic_field_strength")],
                    ]
                )
        assay_table = render_field_value_table(assay_rows)

        dataset_rows = [
            [
                entry.get("dataset_id"),
                entry.get("dataset_name"),
                entry.get("dataset_kind"),
                entry.get("dataset_type"),
                dataset_sample_label(entry.get("dataset_sample_names"), entry.get("dataset_sample_ids")),
                entry.get("file_count"),
            ]
            for entry in datasets
        ]
        dataset_table = render_scrollable_table(["Dataset ID", "Name", "Kind", "Type", "Samples", "Files"], dataset_rows, max_height="360px")

        activity_rows = []
        for event in activity:
            actor = event.get("actor_email") or (f"user#{event.get('actor_user_id')}" if event.get("actor_user_id") else "system")
            target = f"{event.get('target_type')}:{event.get('target_id')}"
            activity_rows.append(
                [
                    timestamp_cell(event.get("created_at")),
                    actor,
                    event.get("event_type"),
                    target,
                    format_activity_payload(event.get("payload_json")),
                ]
            )
        activity_table = render_scrollable_table(["Time", "Actor", "Action", "Target", "Details"], activity_rows, max_height="360px")
        return status_text, overview_table, structure_figure, assay_table, dataset_table, activity_table

    @app.callback(
        Output("assay-select-store", "data", allow_duplicate=True),
        Input("summary-structure-chart", "clickData"),
        State("assay-select-store", "data"),
        prevent_initial_call=True,
    )
    def select_assay_from_structure(click_data, current_assay_id):
        if not click_data:
            return dash.no_update
        points = click_data.get("points") or []
        if not points:
            return dash.no_update
        customdata = points[0].get("customdata") or []
        if len(customdata) < 2:
            return dash.no_update
        assay_id = customdata[1]
        if assay_id in (None, ""):
            return dash.no_update
        assay_id = int(assay_id)
        if assay_id == current_assay_id:
            return dash.no_update
        return assay_id
