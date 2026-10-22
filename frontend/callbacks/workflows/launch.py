"""Workflow selection, libraries, parameter template, and launch form state."""

import inspect
from dash import ALL, Input, Output, State, dcc, html
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches, should_reuse_page_cache
from services.workflow_service import workflow_engine, workflow_template_parameter


# The launch callback reads the whole workflow form. Dash passes State
# values positionally, so declaring them here as (name, component id, property)
# keeps the argument order and the field names in one place: adding a field is a
# single entry, and a mismatch raises instead of silently shifting every value.
# Continuing a paused run moved to /jobs/<id>/isqc, so the five states that
# existed only for that branch are gone from here.
LAUNCH_FORM_STATES = (
    ("pathname", "url", "pathname"),
    ("token", "token-store", "data"),
    ("study_id", "study-select-store", "data"),
    ("assay_id", "assay-select-store", "data"),
    ("wf_id", "wf-id", "value"),
    ("wf_params", "wf-params", "value"),
    ("ms1_library", "wf-ms1-library", "value"),
    ("ms2_library", "wf-ms2-library", "value"),
    ("msdial_template", "wf-msdial-template", "value"),
    ("msdial_template_text", "wf-msdial-template-editor", "value"),
    ("template_source", "wf-msdial-template-source-store", "data"),
    ("reference_file_id", "wf-reference-file", "value"),
    ("execution_target", "wf-execution-target", "value"),
    ("review_stages", "wf-review-stages", "value"),
    ("analysis_mode", "wf-analysis-mode", "value"),
    ("enable_sirius", "wf-enable-sirius", "value"),
    ("sirius_annotation_mode", "wf-sirius-annotation-mode", "value"),
    ("selection_store", "workflow-file-selection-store", "data"),
    ("mutation_signal", "workflow-mutation-signal", "data"),
    ("definitions_store", "wf-definitions-store", "data"),
    ("metabolensr_profile", "wf-metabolensr-profile", "value"),
    ("metabolensr_rule_context", "wf-curation-rule-context", "data"),
    ("metabolensr_statistics_rules", "wf-curation-statistics", "value"),
    ("metabolensr_annotation_rules", "wf-curation-annotation", "value"),
)


def _launch_form_states() -> list:
    return [State(component_id, prop) for _, component_id, prop in LAUNCH_FORM_STATES]


def _assert_launch_form_signature(callback, leading_inputs: int) -> None:
    """Refuse to register a launch callback whose parameters have drifted.

    Dash binds State values positionally. Without this check, adding a State to
    one list and not the other shifts every later argument by one and the launch
    silently uses the wrong values instead of failing.
    """
    parameters = list(inspect.signature(callback).parameters)[leading_inputs:]
    expected = [name for name, _, _ in LAUNCH_FORM_STATES]
    if parameters != expected:
        raise RuntimeError(
            f"{callback.__name__} parameters do not match LAUNCH_FORM_STATES.\n"
            f"  expected: {expected}\n"
            f"  found:    {parameters}"
        )


def _sirius_mode_disabled(enable_sirius: bool | None) -> bool:
    return not bool(enable_sirius)


def _workflow_options(rows: list[dict] | None) -> list[dict]:
    options: list[dict] = []
    for row in rows or []:
        workflow_id = row.get("id")
        if workflow_id is None:
            continue
        name = row.get("name") or "Workflow"
        version = row.get("version")
        label = f"{name} v{version} (#{workflow_id})" if version else f"{name} (#{workflow_id})"
        options.append({"label": label, "value": workflow_id})
    return options


def _selected_workflow_id(options: list[dict], current_value: int | str | None) -> int | None:
    values = {int(option["value"]) for option in options if option.get("value") is not None}
    if current_value is not None:
        try:
            current_id = int(current_value)
        except (TypeError, ValueError):
            current_id = None
        if current_id in values:
            return current_id
    return int(options[0]["value"]) if options else None


def _library_options(rows: list[dict] | None) -> list[dict]:
    return [{"label": row.get("label"), "value": row.get("value")} for row in (rows or [])]


def _library_default(options: list[dict], default_value: str | None, required: bool = True) -> str | None:
    values = {option.get("value") for option in options}
    if default_value in values:
        return default_value
    return options[0]["value"] if options and required else None


def _selected_library_value(options: list[dict], selected_value: str | None, default_value: str | None, required: bool = True) -> str | None:
    return _library_default(options, selected_value if selected_value is not None else default_value, required)


def _workflow_library_controls(libraries: dict, selections: dict, definition: dict | None = None) -> tuple:
    engine = workflow_engine(definition)
    template = workflow_template_parameter(definition)
    template_catalog = 'masscube_templates' if template == 'masscube_config_path' else 'msdial_templates'
    controls = []
    for catalog, selection, parameter in (
        ('ms1_libraries', 'ms1_library', 'ms1_library_path'),
        ('ms2_libraries', 'ms2_library', 'ms2_library_path'),
        (template_catalog, 'msdial_template', template),
    ):
        options = _library_options(libraries.get(catalog))
        required = not engine or parameter == template or parameter in engine.get('required_inputs', {})
        # An explicit clear of an optional input wins over a configured default.
        default = None if not required and selection in selections else libraries.get(f'default_{parameter}')
        controls.extend((options, _selected_library_value(options, selections.get(selection), default, required)))
    return tuple(controls)


def _workflow_library_key(workflow_id: int | str | None) -> str | None:
    try:
        return str(int(workflow_id))
    except (TypeError, ValueError):
        return None


def _workflow_library_cache_entry(store, token, workflow_id) -> dict | None:
    key = _workflow_library_key(workflow_id)
    if key is None or not page_cache_matches(store, token):
        return None
    entries = page_cache_data(store, {})
    entry = entries.get(key) if isinstance(entries, dict) else None
    return entry if isinstance(entry, dict) else None


def _store_workflow_libraries(store, token, workflow_id, libraries: dict) -> dict:
    key = _workflow_library_key(workflow_id)
    entries = page_cache_data(store, {}) if page_cache_matches(store, token) else {}
    entries = dict(entries) if isinstance(entries, dict) else {}
    if key is not None:
        entries[key] = dict(libraries or {})
    return build_page_cache(entries, token)


def _workflow_library_selections(store, token, workflow_id) -> dict:
    entry = _workflow_library_cache_entry(store, token, workflow_id)
    return entry if entry is not None else {}


def _store_workflow_library_selections(store, token, workflow_id, ms1_library, ms2_library, msdial_template) -> dict:
    return _store_workflow_libraries(
        store,
        token,
        workflow_id,
        {
            "ms1_library": ms1_library,
            "ms2_library": ms2_library,
            "msdial_template": msdial_template,
        },
    )


def _eligible_reference_files(files: list[dict] | None, selection_store: dict | None, study_id: int | None, assay_id: int | None) -> list[dict]:
    rows = [
        row for row in (files or [])
        if row.get("id") is not None
        and row.get("file_role") == "thermo_raw"
        and row.get("status") in {"registered", "uploaded", "validated"}
    ]
    if selection_store and selection_store.get("study_id") == study_id and selection_store.get("assay_id") == assay_id and selection_store.get("mode") == "selected":
        selected_ids = {int(file_id) for file_id in (selection_store.get("selected_file_ids") or [])}
        rows = [row for row in rows if int(row.get("id")) in selected_ids]
    return rows


def _reference_options(rows: list[dict]) -> list[dict]:
    options: list[dict] = []
    for row in rows:
        file_id = row.get("id")
        sample_name = row.get("file_sample_name") or ", ".join(row.get("dataset_sample_names") or []) or "No sample"
        filename = row.get("original_filename") or "unnamed file"
        status = row.get("status") or "unknown"
        options.append({"label": f"{sample_name} - {filename} (#{file_id}, {status})", "value": file_id})
    return options


def _reference_default(options: list[dict], current_value: int | str | None) -> int | None:
    values = {int(option["value"]) for option in options if option.get("value") is not None}
    if current_value is not None:
        try:
            current_id = int(current_value)
        except (TypeError, ValueError):
            current_id = None
        if current_id in values:
            return current_id
    return int(options[0]["value"]) if options else None
