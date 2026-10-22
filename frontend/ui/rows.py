import dash_bootstrap_components as dbc
from dash import dcc, html

from ui.components import icon
from ui.formatting import assay_kind_label, dataset_sample_label, file_sample_label, format_bytes, sample_type_label, state_badge, timestamp_cell


def study_rows(studies: list[dict], selected_study_id: int | None = None) -> list[list[object]]:
    rows: list[list[object]] = []
    for study in studies:
        study_id = study.get("id")
        is_selected = study_id == selected_study_id
        select_button = dbc.Button(
            "Selected" if is_selected else "Select",
            id={"type": "study-select-btn", "index": study_id},
            n_clicks=0,
            color="primary" if is_selected else "outline-secondary",
            size="sm",
            className="w-100",
            disabled=is_selected,
        )
        rows.append(
            [
                html.Div(select_button),
                study_id,
                study.get("public_or_internal_identifier"),
                study.get("title"),
                study.get("sample_count", 0),
                study.get("assay_count", 0),
                state_badge(study.get("status")),
                timestamp_cell(study.get("created_at")),
                dcc.Link([icon("package"), html.Span("ISA packaging", className="visually-hidden")],
                         href=f"/studies/{study_id}/packaging", title="ISA packaging", className="icon-action"),
            ]
        )
    return rows


def assay_rows(assays: list[dict], selected_assay_id: int | None = None) -> list[list[object]]:
    rows: list[list[object]] = []
    for assay in assays:
        assay_id = assay.get("id")
        is_selected = assay_id == selected_assay_id
        select_button = dbc.Button(
            "Selected" if is_selected else "Select",
            id={"type": "assay-select-btn", "index": assay_id},
            n_clicks=0,
            color="primary" if is_selected else "outline-secondary",
            size="sm",
            className="w-100",
            disabled=is_selected,
        )
        rows.append(
            [
                html.Div(select_button),
                assay_id,
                assay.get("name"),
                assay_kind_label(assay),
                state_badge(assay.get("status")),
                "Yes" if assay.get("is_default") else "No",
                timestamp_cell(assay.get("updated_at")),
            ]
        )
    return rows


def sample_rows(samples: list[dict]) -> list[list[object]]:
    rows: list[list[object]] = []
    for sample in samples:
        annotations = sample.get("annotations_json") or {}
        rows.append(
            [
                sample.get("id"),
                sample.get("name"),
                sample.get("description"),
                sample.get("replicate_number"),
                sample.get("dilution_factor"),
                sample_type_label(sample.get("sample_type")),
                sample.get("organism"),
                sample.get("matrix"),
                annotations.get("group_label"),
                annotations.get("batch_id"),
                annotations.get("injection_order"),
            ]
        )
    return rows


def worker_input_file_rows(files: list[dict], selected_ids: set[int] | None) -> list[list[object]]:
    rows: list[list[object]] = []
    for file in files:
        file_id = file.get("id")
        is_selected = selected_ids is None or file_id in selected_ids
        rows.append(
            [
                file_id,
                file.get("original_filename"),
                file.get("file_role"),
                file.get("dataset_kind"),
                state_badge(file.get("status")),
                file_sample_label(file),
                dataset_sample_label(file.get("dataset_sample_names"), file.get("dataset_sample_ids")),
                dbc.Badge("Selected", color="success") if is_selected else dbc.Badge("Not selected", color="light", text_color="dark"),
            ]
        )
    return rows


def workflow_rows(jobs: list[dict]) -> list[list[object]]:
    rows: list[list[object]] = []
    for job in jobs:
        rows.append(
            [
                job.get("id"),
                job.get("workflow_definition_id"),
                state_badge(job.get("status")),
                timestamp_cell(job.get("started_at")),
                timestamp_cell(job.get("finished_at")),
                job.get("error_message"),
            ]
        )
    return rows


def workflow_output_rows(outputs: list[dict]) -> list[list[object]]:
    rows: list[list[object]] = []
    for output in outputs:
        rows.append(
            [
                output.get("workflow_job_id"),
                output.get("artifact_role"),
                output.get("file_id"),
                output.get("dataset_kind"),
                dataset_sample_label(output.get("dataset_sample_names"), output.get("dataset_sample_ids")),
                output.get("original_filename"),
                output.get("file_role"),
                format_bytes(output.get("file_size_bytes")),
                state_badge(output.get("status")),
                timestamp_cell(output.get("created_at")),
                output.get("storage_uri"),
            ]
        )
    return rows
