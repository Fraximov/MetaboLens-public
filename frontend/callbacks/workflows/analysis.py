"""IS-QC review, RT correction, and the EIC viewer."""

import math
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, dcc, html
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches, should_reuse_page_cache
from ui.figures import build_eic_figure, build_feature_map_figure, build_is_qc_trace_figure, empty_feature_map_figure
from ui.formatting import format_bytes

from .shared import (
    TRACE_REVIEW_SUMMARY_ID,
    _execution_profile_label,
    _job_stage,
    _truthy,
)


ALL_IS_TARGETS_VALUE = "__all__"


IS_QC_TARGET_HEADERS = [
    "Use in RT model",
    "IS compound",
    "Samples detected",
    "Corrected RT (min)",
    "QC result",
    "View traces",
]


def _aligned_eic_review_is_visible(trace_review_clicks: int | None, active_tab: str | None) -> bool:
    return bool((trace_review_clicks or 0) % 2) and active_tab == "aligned-eic"


def _eic_archive_status(outputs, token, study_id, assay_id, archive_count: int, active_job_id: int | None = None) -> str:
    warning = outputs.get("warning") if page_cache_matches(outputs, token, study_id=study_id, assay_id=assay_id) else None
    if not archive_count:
        scope = f"job #{active_job_id}" if active_job_id is not None else "the active assay"
        status = f"No registered MS-DIAL EIC archive is available for {scope} yet."
    elif active_job_id is not None:
        status = f"{archive_count} EIC archive(s) available for job #{active_job_id}."
    else:
        status = f"{archive_count} EIC archive(s) available across all jobs for this assay."
    return f"{warning} {status}" if warning else status


def _is_qc_target_options(rows: list[dict] | None) -> list[dict]:
    names = []
    for row in rows or []:
        name = row.get("target_name")
        if name and name not in names:
            names.append(name)
    return [
        {"label": "All standards (RT overview)", "value": ALL_IS_TARGETS_VALUE},
        *[{"label": name, "value": name} for name in names],
    ]


def _selected_is_qc_target(options: list[dict], current_value: str | None) -> str | None:
    values = {option.get("value") for option in options}
    if current_value in values:
        return current_value
    return next(
        (
            option["value"]
            for option in options
            if option.get("value") != ALL_IS_TARGETS_VALUE
        ),
        options[0]["value"] if options else None,
    )


def _is_qc_rt_model_options(rows: list[dict] | None) -> list[dict]:
    options = []
    seen = set()
    for row in rows or []:
        target_id = str(row.get("target_id") or "")
        if not target_id or target_id in seen:
            continue
        seen.add(target_id)
        options.append({
            "label": row.get("target_name") or target_id,
            "value": target_id,
        })
    return options


def _selected_rt_model_targets(
    options: list[dict],
    current_value: list[str] | None,
) -> list[str]:
    available = [str(option.get("value")) for option in options]
    if current_value is None:
        return available
    selected = {str(target_id) for target_id in current_value}
    return [target_id for target_id in available if target_id in selected]


def _selected_rt_model_targets_from_rows(
    checked_values: list[bool] | None,
    row_ids: list[dict] | None,
) -> list[str]:
    return [
        str(row_id.get("target"))
        for checked, row_id in zip(checked_values or [], row_ids or [])
        if checked and row_id.get("target") not in (None, "")
    ]


def _rt_correction_control_state(
    job: dict | None,
    current_value: bool | None,
    selection_status: dict | None = None,
    *,
    active_job_id: int | None = None,
) -> tuple[bool, bool, str]:
    if not job:
        return True, False, "Select an IS-QC review job to choose RT correction."
    parameters = job.get("parameter_values_json") or {}
    status_payload = job.get("status_payload_json") or {}
    if _job_stage(job) != "is_qc_review":
        applied = _truthy(parameters.get("apply_rt_correction"))
        return True, applied, "RT correction choice is fixed after IS-QC continuation."
    correction = status_payload.get("rt_correction") or {}
    if selection_status and selection_status.get("job_id") == active_job_id:
        correction = selection_status
    if not correction.get("can_apply"):
        reasons = correction.get("blocking_reasons") or []
        return True, False, str(
            reasons[0] if reasons else "RT correction is not available for this run."
        )
    return False, bool(current_value), "RT correction is available after review."


def _corrected_rt_summary(rows: list[dict]) -> str:
    values = []
    for row in rows:
        try:
            value = float(row.get("corrected_rt_min"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    if not values:
        return "-"
    formatted = sorted({f"{value:.3f}" for value in values})
    return formatted[0] if len(formatted) == 1 else f"{formatted[0]}–{formatted[-1]}"


def _is_qc_target_table_rows(
    rows: list[dict] | None,
    selected_target_ids: list[str] | None = None,
) -> list[list[object]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows or []:
        target_id = str(row.get("target_id") or "")
        target_name = str(row.get("target_name") or "")
        grouped.setdefault(target_id or f"name:{target_name}", []).append(row)

    selected = (
        {str(target_id) for target_id in selected_target_ids}
        if selected_target_ids is not None
        else {str(row.get("target_id")) for row in rows or [] if row.get("target_id") not in (None, "")}
    )
    table_rows: list[list[object]] = []
    for row_index, compound_rows in enumerate(grouped.values()):
        first = compound_rows[0]
        target_id = str(first.get("target_id") or "")
        target_name = first.get("target_name") or target_id
        sample_names = {
            str(row.get("sample_name") or row.get("input_file"))
            for row in compound_rows
            if row.get("sample_name") or row.get("input_file")
        }
        sample_count = len(sample_names) or len(compound_rows)
        passing = sum(str(row.get("status") or "").lower() == "pass" for row in compound_rows)

        model_checkbox = dbc.Checkbox(
            id={"type": "wf-is-qc-rt-model-row", "target": target_id},
            value=target_id in selected,
            disabled=not target_id,
            label=f"Use {target_name} in RT correction model",
            label_class_name="visually-hidden",
        )

        trace_button = dbc.Button(
            "View traces",
            id={"type": "wf-is-qc-trace-btn", "index": row_index, "target": target_name or "",
            },
            n_clicks=0,
            color="outline-secondary",
            size="sm",
            disabled=not target_name,
        )

        table_rows.append(
            [
                html.Div(model_checkbox, style={"textAlign": "center"}),
                target_name,
                sample_count,
                _corrected_rt_summary(compound_rows),
                f"{passing} / {len(compound_rows)} passing",
                html.Div(trace_button, style={"textAlign": "center"}),
            ]
        )
    return table_rows


def _eic_archive_options(outputs: list[dict] | None) -> list[dict]:
    options: list[dict] = []
    for output in outputs or []:
        if output.get("artifact_role") != "msdial_eic_archive":
            continue
        file_id = output.get("file_id")
        if file_id is None:
            continue
        execution_label = _execution_profile_label(output.get("execution_profile"))
        label = f"Job #{output.get('workflow_job_id')} | {execution_label} | {output.get('original_filename')} | {format_bytes(output.get('file_size_bytes'))}"
        options.append({"label": label, "value": file_id})
    return options


def _selected_eic_archive_id(options: list[dict], current_value: int | str | None) -> int | None:
    values = {int(option["value"]) for option in options if option.get("value") is not None}
    if current_value is not None:
        try:
            current_id = int(current_value)
        except (TypeError, ValueError):
            current_id = None
        if current_id in values:
            return current_id
    return int(options[0]["value"]) if options else None


def _eic_feature_options(features: list[dict] | None, search_value: str | None = None) -> list[dict]:
    # The server already applied semantic filters (reference/unmatched/lowscore).
    # Keep Dash's second, textual filter from hiding those returned matches.
    return [{"label": feature.get("label"),
             "value": feature.get("feature_id", feature.get("feature_index")),
             **({"search": f"{search_value} {feature.get('label') or ''}"} if search_value else {})}
            for feature in (features or [])]


def _eic_slider_marks(feature_count: int) -> dict[int, str]:
    if feature_count <= 1:
        return {0: "0"}
    last = feature_count - 1
    middle = last // 2
    return {0: "0", middle: str(middle), last: str(last)}


def _eic_graph(payload: dict):
    return dcc.Graph(id="wf-eic-graph", figure=build_eic_figure(payload), config={"displayModeBar": True, "responsive": True})


def _feature_index_from_map_click(click_data: dict | None) -> int | None:
    points = (click_data or {}).get("points") or []
    if not points:
        return None
    customdata = points[0].get("customdata")
    if isinstance(customdata, list) and customdata:
        candidate = customdata[0]
    else:
        candidate = customdata
    try:
        return int(candidate)
    except (TypeError, ValueError):
        return None


def _feature_index_from_controls(
    triggered_id: str | None,
    dropdown_feature_index,
    slider_feature_index,
    typed_feature_index,
    feature_disabled,
    slider_disabled,
    map_click_data: dict | None,
) -> int | None:
    if triggered_id == "wf-eic-feature-map":
        return _feature_index_from_map_click(map_click_data)
    if triggered_id == "wf-eic-feature" and not feature_disabled and dropdown_feature_index is not None:
        return int(dropdown_feature_index)
    if triggered_id == "wf-eic-feature-slider" and not slider_disabled and slider_feature_index is not None:
        return int(slider_feature_index)
    if triggered_id == "wf-eic-feature-load-btn" and typed_feature_index is not None:
        return int(typed_feature_index)
    return None


def _feature_index_for_trace_request(
    triggered_id,
    dropdown_feature_index,
    slider_feature_index,
    typed_feature_index,
    feature_disabled,
    slider_disabled,
    map_click_data: dict | None,
) -> int | None:
    if triggered_id in {"wf-eic-archive", "wf-trace-review-details", TRACE_REVIEW_SUMMARY_ID}:
        return None
    return _feature_index_from_controls(
        triggered_id,
        dropdown_feature_index,
        slider_feature_index,
        typed_feature_index,
        feature_disabled,
        slider_disabled,
        map_click_data,
    )
