import base64
import json
from urllib.parse import urlencode

from constants import (
    DEFAULT_WORKFLOW_EXECUTION_TARGET,
    WORKFLOW_EXECUTION_TARGETS,
)
from services.api_client import call_api, call_api_bytes


FORM_LABELS = {'full': 'Full pipeline', 'qc_only': 'QC and preprocessing', 'discovery': 'Discovery'}


def workflow_engine(definition: dict | None) -> dict | None:
    return ((definition or {}).get('static_input_config_json') or {}).get('engine')


def workflow_template_parameter(definition: dict | None) -> str:
    properties = ((definition or {}).get('parameter_schema_json') or {}).get('properties', {})
    return 'masscube_config_path' if 'masscube_config_path' in properties else 'msdial_config_path'


def workflow_form_controls(definition: dict | None) -> dict:
    """Describe existing form controls from the selected workflow's contract."""
    engine = workflow_engine(definition) or {}
    properties = ((definition or {}).get('parameter_schema_json') or {}).get('properties') or {}
    controls = {'analysis_mode': [
        {'label': FORM_LABELS.get(value, str(value).replace('_', ' ')), 'value': value}
        for value in properties.get('analysis_mode', {}).get('enum', [])
    ]}
    controls['execution_target'] = [
        {'label': target['label'], 'value': key}
        for key, target in WORKFLOW_EXECUTION_TARGETS.items()
        if target['execution_profile'] in engine.get('execution_profiles', [])
    ]
    for key in ('msdial_config_path', 'ms1_library_path', 'ms2_library_path', 'reference_file_id', 'enable_sirius', 'sirius_annotation_mode'):
        controls[key] = key in properties
    controls['parameter_template'] = workflow_template_parameter(definition) in properties
    return controls


def form_selection(options: list[dict], current, default=None):
    values = [option['value'] for option in options]
    return current if current in values else default if default in values else next(iter(values), None)


def workflow_review_options(definition: dict | None) -> list[dict]:
    """Order pause controls by transitions, independent of JSON object order."""
    transitions = (workflow_engine(definition) or {}).get('review_transitions', {})
    starts = set(transitions) - set(transitions.values())
    stage = next(iter(sorted(starts)), None)
    stages = []
    while stage in transitions and stage not in stages:
        stages.append(stage)
        stage = transitions[stage]
    labels = {
        'is_qc_review': 'Pause after IS-QC / RT review',
        'qc_preview': 'Pause for feature review',
    }
    return [{'label': labels.get(stage, f"Pause at {stage.replace('_', ' ')}"), 'value': stage}
            for stage in stages]


def workflow_review_selection(definition: dict | None, current: list[str] | None) -> list[str] | None:
    if not workflow_engine(definition):
        return None
    stages = [option['value'] for option in workflow_review_options(definition)]
    if current is None:
        default = ((definition or {}).get('parameter_schema_json') or {}).get('properties', {}).get('workflow_stage', {}).get('default')
        return stages[stages.index(default):] if default in stages else []
    return [stage for stage in stages if stage in current]


def _review_initial_stage(definition: dict, selected: list[str]) -> str:
    if selected:
        return selected[0]
    options = workflow_review_options(definition)
    return workflow_engine(definition)['review_transitions'][options[-1]['value']] if options else 'full'


def engine_form_parameters(definition: dict, params: dict, selections: dict, execution_target: str | None) -> dict:
    properties = definition['parameter_schema_json'].get('properties', {})
    values = {key: value for key, value in params.items() if key in properties}
    for key, value in selections.items():
        if key not in properties:
            continue
        field = properties[key]
        if 'enum' in field and value not in field['enum']:
            value = field.get('default')
        if value is not None and value != '':
            values[key] = value
        elif key not in workflow_engine(definition).get('required_inputs', {}):
            values.pop(key, None)
    if execution_target:
        target = execution_target_parameters(execution_target)
        if target['execution_profile'] not in workflow_engine(definition)['execution_profiles']:
            raise ValueError('This workflow does not support the selected execution target.')
        values.update(target)
    return values


def list_workflows(token: str) -> list[dict]:
    return call_api("GET", "/workflows", token=token)


def list_workflow_libraries(token: str, workflow_id: int | None) -> dict:
    if not workflow_id:
        raise ValueError("Set workflow ID to load libraries")
    return call_api("GET", f"/workflows/{int(workflow_id)}/libraries", token=token)


def get_workflow_template_content(token: str, workflow_id: int | None, path: str | None, definition: dict | None = None) -> dict:
    if not workflow_id or not path:
        raise ValueError("Select a workflow and parameter template")
    query = urlencode({"path": path})
    engine = 'masscube' if workflow_template_parameter(definition) == 'masscube_config_path' else 'msdial'
    return call_api("GET", f"/workflows/{int(workflow_id)}/{engine}-template?{query}", token=token)


def selected_workflow_definition(workflow_definitions: list[dict] | None, workflow_id: int | str | None) -> dict | None:
    if workflow_id in (None, ""):
        return None
    try:
        selected_id = int(workflow_id)
    except (TypeError, ValueError):
        return None
    for definition in workflow_definitions or []:
        try:
            definition_id = int(definition.get("id"))
        except (TypeError, ValueError):
            continue
        if definition_id == selected_id:
            return definition
    return None


def execution_target_details(execution_target: str | None) -> dict:
    return WORKFLOW_EXECUTION_TARGETS.get(execution_target or DEFAULT_WORKFLOW_EXECUTION_TARGET, WORKFLOW_EXECUTION_TARGETS[DEFAULT_WORKFLOW_EXECUTION_TARGET])


def execution_target_parameters(execution_target: str | None) -> dict[str, str]:
    target = execution_target_details(execution_target)
    return {
        "execution_profile": target["execution_profile"],
        "nextflow_profile": target["nextflow_profile"],
    }


def workflow_preview_metadata(
    workflow_definitions: list[dict] | None,
    workflow_id: int | str | None,
    workflow_params: str | None,
    execution_target: str | None = None,
) -> dict:
    definition = selected_workflow_definition(workflow_definitions, workflow_id)
    target = execution_target_details(execution_target) if execution_target else None
    try:
        params = json.loads(workflow_params or "{}")
    except json.JSONDecodeError:
        params = {}
    if not isinstance(params, dict):
        params = {}

    if definition:
        version = definition.get("version")
        pipeline = definition.get("name") or f"Workflow #{definition.get('id')}"
        if version:
            pipeline = f"{pipeline} v{version}"
        profile = (target or {}).get("nextflow_profile") or params.get("nextflow_profile") or params.get("profile") or definition.get("default_nextflow_profile") or definition.get("container_profile")
        container = (target or {}).get("runtime_label") or definition.get("container_profile") or "configured runtime"
        executor = (target or {}).get("execution_profile") or definition.get("execution_profile") or definition.get("execution_backend") or "configured executor"
        entrypoint = definition.get("nextflow_entrypoint") or definition.get("name") or "<workflow>"
    else:
        pipeline = "Select workflow"
        profile = (target or {}).get("nextflow_profile") or params.get("nextflow_profile") or params.get("profile") or "-"
        container = (target or {}).get("runtime_label") or "-"
        executor = (target or {}).get("execution_profile") or "Not selected"
        entrypoint = "<select-workflow>"

    return {
        "pipeline": pipeline,
        "profile": profile or "-",
        "container": container,
        "executor": executor,
        "entrypoint": entrypoint,
    }


def workflow_launch_readiness(
    token: str | None,
    assay_id: int | str | None,
    workflow_id: int | str | None,
    workflow_params: str | None,
    ms1_library_path: str | None,
    ms2_library_path: str | None,
    msdial_config_path: str | None,
    reference_file_id: int | str | None,
    selection_store: dict | None,
    study_id: int | str | None = None,
    workflow_definition: dict | None = None,
) -> dict:
    missing: list[str] = []
    if not token:
        missing.append("login session")
    if not study_id:
        missing.append("study")
    if not assay_id:
        missing.append("assay")
    if not workflow_id:
        missing.append("pipeline")
    static = (workflow_definition or {}).get('static_input_config_json') or {}
    requirements = (static.get('engine') or {}).get('required_inputs', {})
    template_key = workflow_template_parameter(workflow_definition)
    values = {**static, template_key: msdial_config_path or static.get(template_key),
              'ms1_library_path': ms1_library_path or static.get('ms1_library_path'),
              'ms2_library_path': ms2_library_path or static.get('ms2_library_path'),
              'reference_file_id': reference_file_id}
    try:
        params = json.loads(workflow_params or "{}")
    except json.JSONDecodeError:
        missing.append("valid parameters JSON")
    else:
        if not isinstance(params, dict):
            missing.append("parameters JSON object")
        else:
            values = {**params, **{k: v for k, v in values.items() if v not in (None, '')}}
    for key, label in requirements.items():
        if values.get(key) in (None, ''):
            missing.append(label)

    if (
        selection_store
        and selection_store.get("study_id") == study_id
        and selection_store.get("assay_id") == assay_id
        and selection_store.get("mode") == "selected"
        and not (selection_store.get("selected_file_ids") or [])
    ):
        missing.append("selected input files")

    return {
        "ready": not missing,
        "missing": missing,
        "message": "All required inputs are valid. You're good to go!" if not missing else f"Complete required fields: {', '.join(missing)}.",
    }


def workflow_curation_profiles(definition: dict | None) -> dict:
    engine = workflow_engine(definition) or {}
    return engine.get('package', {}).get('curation_profiles') or {}


def workflow_curation_selection(definition: dict | None, selected: str | None) -> str | None:
    profiles = workflow_curation_profiles(definition)
    if not profiles:
        return None
    if selected in profiles:
        return selected
    default = workflow_engine(definition)['package']['config'].get('profile')
    return default if default in profiles else next(iter(profiles))


def workflow_curation_context(definition: dict | None, selected: str | None) -> dict | None:
    package = (workflow_engine(definition) or {}).get('package', {})
    profile = workflow_curation_selection(definition, selected)
    if not package.get('supports_rule_selection') or profile is None:
        return None
    return {'workflow_id': definition['id'], 'package_commit': package.get('commit'),
            'package_image': package['container_image'],
            'profile': workflow_curation_profiles(definition)[profile]}


def workflow_curation_rule_selection(definition: dict | None, selected: str | None,
                                     context: dict | None, statistics, annotation) -> dict | None:
    expected = workflow_curation_context(definition, selected)
    if context != expected:
        raise ValueError('The rule controls are updating. Wait for the selected profile to load.')
    if expected is None:
        return None
    selection = {}
    for domain, ids in (('statistics', statistics), ('annotation', annotation)):
        available = [rule['rule_id'] for rule in expected['profile'][domain]]
        if (not isinstance(ids, list) or any(not isinstance(rule, str) for rule in ids)
                or len(set(ids)) != len(ids) or set(ids) - set(available)):
            raise ValueError(f'Invalid {domain} rule selection; reset to the preset.')
        if set(ids) != set(available):
            selection[domain] = [rule for rule in available if rule in ids]
    # Omit the override for the unchanged preset; never collapse an empty domain.
    return selection or None


def build_nextflow_command_preview(
    workflow_definitions: list[dict] | None,
    workflow_id: int | str | None,
    workflow_params: str | None,
    ms1_library_path: str | None,
    ms2_library_path: str | None,
    msdial_config_path: str | None,
    reference_file_id: int | str | None,
    analysis_mode: str | None,
    execution_target: str | None = None,
    enable_sirius: bool = False,
    sirius_annotation_mode: str | None = "formulas",
    review_stages: list[str] | None = None,
    metabolensr_profile: str | None = None,
    metabolensr_enabled_rules: dict | None = None,
) -> str:
    """Preview the launch intent the page will submit.

    The worker later freezes the engine contract, stages the inputs and resolves
    the engine-specific Nextflow arguments, so only the submitted parameters are
    shown here.
    """

    metadata = workflow_preview_metadata(workflow_definitions, workflow_id, workflow_params, execution_target)
    definition = selected_workflow_definition(workflow_definitions, workflow_id)
    try:
        params = json.loads(workflow_params or "{}")
    except json.JSONDecodeError as exc:
        params = {}
        parse_error = f"# Parameters JSON is invalid: {exc}"
    else:
        parse_error = None
        if not isinstance(params, dict):
            params = {}
            parse_error = "# Parameters JSON must be an object."

    profile = metadata["profile"] if metadata["profile"] != "-" else "configured-profile"
    engine = workflow_engine(definition)
    if not engine:
        return '# Select a workflow to preview its launch.'
    workflow_stage = None
    if review_stages is not None:
        review_stages = workflow_review_selection(definition, review_stages)
        workflow_stage = _review_initial_stage(definition, review_stages)
    values = engine_form_parameters(definition, params, {
        'workflow_stage': workflow_stage, 'analysis_mode': analysis_mode,
        workflow_template_parameter(definition): msdial_config_path, 'ms1_library_path': ms1_library_path,
        'ms2_library_path': ms2_library_path, 'reference_file_id': reference_file_id,
        'enable_sirius': bool(enable_sirius), 'sirius_annotation_mode': sirius_annotation_mode,
    }, None)
    return '\n'.join([
        '# Engine launch intent — the worker resolves engine-specific arguments.',
        f"# Entrypoint: <workflow-run-root>/contract/workflow/{engine.get('entrypoint', metadata['entrypoint'])}",
        f'# Execution profile: {profile}',
        *([f'# MetaboLensR profile: {workflow_curation_selection(definition, metabolensr_profile)}']
          if workflow_curation_profiles(definition) else []),
        *([f'# MetaboLensR enabled rules (other domains use preset): {json.dumps(metabolensr_enabled_rules)}']
          if metabolensr_enabled_rules is not None else []),
        '# MetaboLensR preview → automatic finalization (unless paused for review).',
        *([f'# Review pauses: {json.dumps(review_stages)}'] if review_stages is not None else []),
        '# Submitted parameters:',
        *['# ' + line for line in json.dumps(values, indent=2).splitlines()],
        parse_error or '',
    ])


def launch_workflow(
    token: str,
    assay_id: int | None,
    workflow_id: int | None,
    workflow_params: str | None,
    file_ids: list[int] | None = None,
    ms1_library_path: str | None = None,
    ms2_library_path: str | None = None,
    msdial_config_path: str | None = None,
    msdial_template_text: str | None = None,
    reference_file_id: int | str | None = None,
    analysis_mode: str | None = None,
    execution_target: str | None = None,
    enable_sirius: bool = False,
    sirius_annotation_mode: str | None = "formulas",
    workflow_definition: dict | None = None,
    review_stages: list[str] | None = None,
    metabolensr_profile: str | None = None,
    metabolensr_enabled_rules: dict | None = None,
) -> dict:
    if not assay_id or not workflow_id:
        raise ValueError("Select assay and set workflow ID")
    params = json.loads(workflow_params or "{}")
    if not isinstance(params, dict):
        raise ValueError('Parameters JSON must be an object.')
    engine = workflow_engine(workflow_definition)
    if not engine:
        raise ValueError('This workflow has no engine descriptor and cannot be launched.')
    workflow_stage = None
    if review_stages is not None:
        review_stages = workflow_review_selection(workflow_definition, review_stages)
        workflow_stage = _review_initial_stage(workflow_definition, review_stages)
    params = engine_form_parameters(workflow_definition, params, {
        'ms1_library_path': ms1_library_path, 'ms2_library_path': ms2_library_path,
        workflow_template_parameter(workflow_definition): msdial_config_path,
        'reference_file_id': int(reference_file_id) if reference_file_id else None,
        'workflow_stage': workflow_stage, 'analysis_mode': analysis_mode,
        'enable_sirius': bool(enable_sirius), 'sirius_annotation_mode': sirius_annotation_mode,
    }, execution_target)
    payload = {'parameters': params}
    selected_profile = workflow_curation_selection(workflow_definition, metabolensr_profile)
    if selected_profile is not None:
        payload['metabolensr_profile'] = selected_profile
        payload['metabolensr_expected_image'] = engine['package']['container_image']
    if metabolensr_enabled_rules is not None:
        payload['metabolensr_enabled_rules'] = metabolensr_enabled_rules
    if review_stages is not None:
        payload['review_stages'] = review_stages
    if file_ids is not None:
        payload['file_ids'] = file_ids
    if msdial_template_text is not None and workflow_form_controls(workflow_definition)['parameter_template']:
        field = workflow_template_parameter(workflow_definition).replace('_config_path', '_template_text')
        payload[field] = msdial_template_text
    return call_api('POST', f'/assays/{assay_id}/workflows/{workflow_id}/launch', token=token, payload=payload)


def list_workflow_jobs(token: str, assay_id: int | None) -> list[dict]:
    if not assay_id:
        raise ValueError("Select assay")
    return call_api("GET", f"/assays/{assay_id}/workflow-jobs", token=token)


def delete_workflow_job(token: str, assay_id: int | None, job_id: int | str | None) -> dict:
    if not assay_id:
        raise ValueError("Select assay")
    if not job_id:
        raise ValueError("Select workflow job")
    return call_api(
        "DELETE",
        f"/assays/{int(assay_id)}/workflow-jobs/{int(job_id)}",
        token=token,
    )


def continue_workflow_job(
    token: str,
    assay_id: int | None,
    job_id: int | str | None,
    operator_note: str | None = None,
    apply_rt_correction: bool | None = None,
    rt_correction_target_ids: list[str] | None = None,
    preview_file_id: int | None = None,
    expected_revision: int | None = None,
) -> dict:
    if not assay_id:
        raise ValueError("Select assay")
    if not job_id:
        raise ValueError("Select a paused review job to continue")
    payload = {}
    if preview_file_id is not None:
        payload.update(preview_file_id=preview_file_id, expected_revision=expected_revision)
    if operator_note and operator_note.strip():
        payload["operator_note"] = operator_note.strip()
    if apply_rt_correction is not None:
        payload["apply_rt_correction"] = bool(apply_rt_correction)
    if rt_correction_target_ids is not None:
        payload["rt_correction_target_ids"] = [str(target_id) for target_id in rt_correction_target_ids]
    if payload:
        return call_api("POST", f"/assays/{assay_id}/workflow-jobs/{int(job_id)}/continue", token=token, payload=payload)
    return call_api("POST", f"/assays/{assay_id}/workflow-jobs/{int(job_id)}/continue", token=token)


def list_workflow_output_files(token: str, assay_id: int | None, job_id: int | str | None = None) -> list[dict]:
    if not assay_id:
        raise ValueError("Select assay")
    path = f"/assays/{assay_id}/workflow-output-files"
    if job_id not in (None, "", "all", "__all__"):
        path = f"{path}?{urlencode({'job_id': int(job_id)})}"
    return call_api("GET", path, token=token)


def workflow_dag_output(outputs: list[dict] | None) -> dict | None:
    for output in outputs or []:
        if output.get("artifact_role") == "pipeline_dag" and str(output.get("original_filename") or "").lower().endswith(".svg"):
            return output
    return None


def workflow_output_bytes(token: str, assay_id: int, file_id: int) -> tuple[bytes, str]:
    return call_api_bytes('GET', f'/assays/{int(assay_id)}/workflow-output-files/{int(file_id)}/download', token=token)


def workflow_execution_trace(token: str, assay_id: int | str | None, file_id: int | str | None) -> str:
    """The Nextflow execution trace for a run, as text.

    Every task the run scheduled, unlike the eight rows kept in the job's status
    payload. Registered as the `execution_trace` artifact, so it is fetched the
    same way as any other workflow output.
    """
    if not assay_id or not file_id:
        raise ValueError("Select assay and workflow output file")
    content, _media_type = call_api_bytes(
        "GET", f"/assays/{int(assay_id)}/workflow-output-files/{int(file_id)}/download", token=token
    )
    return content.decode("utf-8", errors="ignore")


def workflow_samplesheet(token: str, assay_id: int | str | None, file_id: int | str | None) -> str:
    """The frozen `samplesheet.csv` a run was launched with, as text.

    Registered as the `run_samplesheet` artifact. This is what the pipeline
    actually consumed, so it reports the sample types and injection order as they
    were at launch rather than as the sample records read today.
    """
    if not assay_id or not file_id:
        raise ValueError("Select assay and workflow output file")
    content, _media_type = call_api_bytes(
        "GET", f"/assays/{int(assay_id)}/workflow-output-files/{int(file_id)}/download", token=token
    )
    return content.decode("utf-8", errors="ignore")


def workflow_output_file_data_uri(token: str, assay_id: int | str | None, file_id: int | str | None) -> str:
    if not assay_id or not file_id:
        raise ValueError("Select assay and workflow output file")
    content, media_type = call_api_bytes("GET", f"/assays/{int(assay_id)}/workflow-output-files/{int(file_id)}/download", token=token)
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _rt_correction_selection_query(target_ids: list[str] | None) -> list[tuple[str, str]]:
    if target_ids is None:
        return []
    values = [str(target_id) for target_id in target_ids]
    return [("rt_correction_target_id", value) for value in values] or [("rt_correction_target_id", "")]


def get_is_qc_summary(
    token: str,
    assay_id: int | None,
    job_id: int | str | None,
    rt_correction_target_ids: list[str] | None = None,
) -> dict:
    if not assay_id:
        raise ValueError("Select assay")
    if not job_id:
        raise ValueError("Select an IS-QC review job")
    query = urlencode(_rt_correction_selection_query(rt_correction_target_ids))
    suffix = f"?{query}" if query else ""
    return call_api("GET", f"/assays/{assay_id}/workflow-is-qc/{int(job_id)}/summary{suffix}", token=token)


def list_is_qc_targets(
    token: str,
    assay_id: int | None,
    job_id: int | str | None,
    rt_correction_target_ids: list[str] | None = None,
) -> list[dict]:
    if not assay_id:
        raise ValueError("Select assay")
    if not job_id:
        raise ValueError("Select an IS-QC review job")
    query = urlencode(_rt_correction_selection_query(rt_correction_target_ids))
    suffix = f"?{query}" if query else ""
    return call_api("GET", f"/assays/{assay_id}/workflow-is-qc/{int(job_id)}/targets{suffix}", token=token)


def get_is_qc_trace(
    token: str,
    assay_id: int | None,
    job_id: int | str | None,
    target_name: str | None,
    rt_correction_target_ids: list[str] | None = None,
) -> dict:
    if not assay_id:
        raise ValueError("Select assay")
    if not job_id:
        raise ValueError("Select an IS-QC review job")
    params = []
    if target_name:
        params.append(("target_name", target_name))
    params.extend(_rt_correction_selection_query(rt_correction_target_ids))
    params = urlencode(params)
    suffix = f"?{params}" if params else ""
    return call_api("GET", f"/assays/{assay_id}/workflow-is-qc/{int(job_id)}/traces{suffix}", token=token)


def list_eic_features(token: str, assay_id: int | None, eic_file_id: int | str | None) -> list[dict]:
    if not assay_id:
        raise ValueError("Select assay")
    if not eic_file_id:
        raise ValueError("Select an EIC archive")
    return call_api("GET", f"/assays/{assay_id}/workflow-eic/{int(eic_file_id)}/features", token=token)


def search_eic_features(
    token: str,
    assay_id: int | None,
    eic_file_id: int | str | None,
    query: str | None = None,
    *,
    limit: int = 50,
    include_feature_index: int | str | None = None,
) -> list[dict]:
    if not assay_id:
        raise ValueError("Select assay")
    if not eic_file_id:
        raise ValueError("Select an EIC archive")
    params: dict[str, str | int] = {"limit": int(limit)}
    if query:
        params["q"] = query
    if include_feature_index is not None:
        params["include_feature_index"] = int(include_feature_index)
    return call_api("GET", f"/assays/{assay_id}/workflow-eic/{int(eic_file_id)}/features/search?{urlencode(params)}", token=token)


def get_eic_summary(token: str, assay_id: int | None, eic_file_id: int | str | None) -> dict:
    if not assay_id:
        raise ValueError("Select assay")
    if not eic_file_id:
        raise ValueError("Select an EIC archive")
    return call_api("GET", f"/assays/{assay_id}/workflow-eic/{int(eic_file_id)}/summary", token=token)


def get_eic_trace(token: str, assay_id: int | None, eic_file_id: int | str | None, feature_index: int | str | None) -> dict:
    if not assay_id:
        raise ValueError("Select assay")
    if not eic_file_id:
        raise ValueError("Select an EIC archive")
    if feature_index is None:
        raise ValueError("Select a feature")
    return call_api("GET", f"/assays/{assay_id}/workflow-eic/{int(eic_file_id)}/features/{int(feature_index)}", token=token)


def list_package_features(token, assay_id, job_id, preview_file_id, *, query=None, page=1, page_size=200):
    params = {'preview_file_id': preview_file_id, 'page': page, 'page_size': page_size}
    if query:
        params['q'] = query
    return call_api('GET', f'/assays/{assay_id}/workflow-jobs/{job_id}/features?{urlencode(params)}', token=token)


def get_package_feature_map(token, assay_id, job_id, preview_file_id, *, query=None):
    params = urlencode({'preview_file_id': preview_file_id, **({'q': query} if query else {})})
    return call_api('GET', f'/assays/{assay_id}/workflow-jobs/{job_id}/features/map?{params}', token=token)


def get_package_feature_trace(token, assay_id, job_id, preview_file_id, feature_id):
    params = urlencode({'preview_file_id': preview_file_id, 'feature_id': feature_id})
    return call_api('GET', f'/assays/{assay_id}/workflow-jobs/{job_id}/features/trace?{params}', token=token)


def get_feature_review(token, assay_id, job_id, preview_file_id, *, queue='include', domain='statistics', query=None,
                       effective=None, has_ms2=None, page=1):
    params = {'preview_file_id':preview_file_id,'queue':queue,'domain':domain,'page':page}
    params.update({k:v for k,v in {'q':query,'effective':effective,'has_ms2':has_ms2}.items() if v is not None})
    return call_api('GET',f'/assays/{assay_id}/workflow-jobs/{job_id}/feature-review?{urlencode(params)}',token=token)


def patch_feature_review(token, assay_id, job_id, preview_file_id, *, expected_revision, changes):
    return call_api('PATCH',f'/assays/{assay_id}/workflow-jobs/{job_id}/feature-review',token=token,
                    payload={'preview_file_id':preview_file_id,'expected_revision':expected_revision,'changes':changes})
