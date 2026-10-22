"""Dash callback registration for the workflows page."""

import json
from datetime import datetime
import dash
from dash import ALL, Input, Output, State, dcc, html
from callbacks.clicks import real_click
from services import file_service
from services import workflow_diagram_service
from services import workflow_service
from services import metro_service
from services.api_client import APIError
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches, should_reuse_page_cache
from ui.components import render_table
from ui.curation_profiles import curation_rule_list, curation_rule_options
from ui.figures import build_eic_figure, build_feature_map_figure, build_is_qc_trace_figure, empty_feature_map_figure
from ui.rows import workflow_output_rows
from ui.workflow_studio import RECENT_RUNS_PAGE_SIZE, format_command_preview, recent_runs_table

from .shared import (
    _active_job_id,
    _dropdown_value_present,
    _job_analysis_mode,
    _job_by_id,
    _job_stage,
    _outputs_for_active_job,
    _should_refresh_outputs,
    _should_reuse_output_cache,
    _should_reuse_workflow_cache,
    _stable_dropdown_value_update,
    _workflow_cache_rows,
    _workflow_output_cache,
    _workflow_output_cache_matches,
    _workflow_output_headers,
    _workflow_page_is_active,
    cache_workflow_outputs_after_fetch_failure,
)
from .launch import (
    _assert_launch_form_signature,
    _eligible_reference_files,
    _launch_form_states,
    _reference_default,
    _reference_options,
    _selected_workflow_id,
    _sirius_mode_disabled,
    _store_workflow_libraries,
    _store_workflow_library_selections,
    _workflow_library_cache_entry,
    _workflow_library_controls,
    _workflow_library_key,
    _workflow_library_selections,
    _workflow_options,
)
from .monitoring import (
    RECENT_RUNS_PAGE_TRIGGERS,
    _default_active_job_value,
    _deleted_job_id,
    _is_failed_is_qc_job,
    _job_can_resume,
    _job_selector_options,
    _navigate_recent_runs_page,
    _opened_job_id,
    _recent_runs_page_index,
    _selected_active_job_value,
    _should_refresh_jobs,
    _should_reuse_job_cache,
    _workflow_jobs_revision,
)
from .analysis import (
    ALL_IS_TARGETS_VALUE,
    IS_QC_TARGET_HEADERS,
    _aligned_eic_review_is_visible,
    _eic_archive_options,
    _eic_archive_status,
    _eic_feature_options,
    _eic_graph,
    _eic_slider_marks,
    _feature_index_for_trace_request,
    _feature_index_from_controls,
    _is_qc_rt_model_options,
    _is_qc_target_options,
    _is_qc_target_table_rows,
    _rt_correction_control_state,
    _selected_eic_archive_id,
    _selected_is_qc_target,
    _selected_rt_model_targets,
    _selected_rt_model_targets_from_rows,
)


def register_callbacks(app):
    @app.callback(
        Output('wf-metabolensr-profile', 'options'), Output('wf-metabolensr-profile', 'value'),
        Output('wf-metabolensr-profile', 'disabled'), Output('wf-curation-profile-section', 'style'),
        Input('wf-id', 'value'), Input('wf-definitions-store', 'data'),
        State('token-store', 'data'), State('wf-metabolensr-profile', 'value'),
    )
    def configure_curation_profile(workflow_id, store, token, selected):
        definition = workflow_service.selected_workflow_definition(_workflow_cache_rows(store, token), workflow_id)
        profiles = workflow_service.workflow_curation_profiles(definition)
        options = [{'label': name.replace('_', ' ').capitalize(), 'value': name} for name in profiles]
        return (options, workflow_service.workflow_curation_selection(definition, selected),
                len(options) <= 1, {} if profiles else {'display': 'none'})

    @app.callback(
        Output('wf-curation-rules', 'children'), Output('wf-curation-rule-controls', 'style'),
        Output('wf-curation-statistics', 'options'), Output('wf-curation-statistics', 'value'),
        Output('wf-curation-annotation', 'options'), Output('wf-curation-annotation', 'value'),
        Output('wf-curation-rule-context', 'data'),
        Input('wf-metabolensr-profile', 'value'), Input('wf-id', 'value'),
        Input('wf-definitions-store', 'data'), Input('wf-curation-reset', 'n_clicks'),
        State('token-store', 'data'), State('wf-curation-rule-context', 'data'),
        State('wf-curation-statistics', 'value'), State('wf-curation-annotation', 'value'),
    )
    def configure_curation_rules(selected, workflow_id, store, _reset, token, context, statistics, annotation):
        definition = workflow_service.selected_workflow_definition(_workflow_cache_rows(store, token), workflow_id)
        name = workflow_service.workflow_curation_selection(definition, selected)
        profile = workflow_service.workflow_curation_profiles(definition).get(name)
        expected = workflow_service.workflow_curation_context(definition, selected)
        if expected is None:
            return curation_rule_list(profile), {'display': 'none'}, [], None, [], None, None
        keep = context == expected and dash.ctx.triggered_id != 'wf-curation-reset'
        values = []
        for domain, current in (('statistics', statistics), ('annotation', annotation)):
            options = curation_rule_options(profile, domain)
            defaults = [option['value'] for option in options]
            values.extend([options, current if keep and current is not None else defaults])
        return [], {}, *values, expected

    @app.callback(
        Output('wf-curation-statistics-status', 'children'), Output('wf-curation-annotation-status', 'children'),
        Output('wf-curation-selection-status', 'children'), Output('wf-curation-reset', 'disabled'),
        Input('wf-curation-statistics', 'value'), Input('wf-curation-annotation', 'value'),
        Input('wf-curation-rule-context', 'data'),
    )
    def show_curation_selection(statistics, annotation, context):
        if not context:
            return '', '', '', True
        messages = []
        customized = False
        for domain, ids in (('statistics', statistics), ('annotation', annotation)):
            available = [rule['rule_id'] for rule in context['profile'][domain]]
            selected = ids if ids is not None else available
            customized |= set(selected) != set(available)
            # Only the case that changes what the run does is worth a line.
            messages.append('' if selected else
                            'No automatic filtering: every feature starts included in this domain.')
        return *messages, 'Customized' if customized else 'Preset defaults', not customized

    @app.callback(
        Output('wf-review-stages', 'options'), Output('wf-review-stages', 'value'),
        Output('wf-review-stages-field', 'style'),
        Input('wf-id', 'value'), Input('wf-definitions-store', 'data'),
        State('token-store', 'data'), State('wf-review-stages', 'value'),
    )
    def configure_review_pauses(workflow_id, store, token, selected):
        definition = workflow_service.selected_workflow_definition(_workflow_cache_rows(store, token), workflow_id)
        engine = workflow_service.workflow_engine(definition)
        return (
            workflow_service.workflow_review_options(definition),
            workflow_service.workflow_review_selection(definition, selected),
            {} if engine else {'display': 'none'},
        )

    @app.callback(
        Output('wf-analysis-mode', 'options'), Output('wf-analysis-mode', 'value'),
        Output('wf-execution-target', 'options'), Output('wf-execution-target', 'value'),
        *[Output(f'{component}-field', 'style') for component in (
            'wf-msdial-template', 'wf-ms1-library', 'wf-ms2-library', 'wf-reference-file',
            'wf-enable-sirius', 'wf-sirius-annotation-mode')],
        Output('wf-template-tab', 'disabled'),
        Output('wf-template-label', 'children'), Output('wf-template-helper', 'children'),
        Input('wf-id', 'value'), Input('wf-definitions-store', 'data'),
        State('token-store', 'data'), State('wf-analysis-mode', 'value'),
        State('wf-execution-target', 'value'),
    )
    def configure_workflow_form(workflow_id, store, token, mode, target):
        definition = workflow_service.selected_workflow_definition(_workflow_cache_rows(store, token), workflow_id)
        controls = workflow_service.workflow_form_controls(definition)
        properties = ((definition or {}).get('parameter_schema_json') or {}).get('properties', {})
        selection = workflow_service.form_selection
        return (
            controls['analysis_mode'], selection(controls['analysis_mode'], mode, properties.get('analysis_mode', {}).get('default')),
            controls['execution_target'], selection(controls['execution_target'], target),
            *[{} if controls[key] else {'display': 'none'} for key in (
                'parameter_template', 'ms1_library_path', 'ms2_library_path', 'reference_file_id',
                'enable_sirius', 'sirius_annotation_mode')],
            not controls['parameter_template'],
            'MassCube parameter template (CSV)' if workflow_service.workflow_template_parameter(definition) == 'masscube_config_path' else 'MS-DIAL parameter template',
            ('Edit the value column for this run; Reset restores the preset and Download saves your edits. '
             'The comparison preset targets Orbitrap data. Read its descriptions for MS-DIAL differences. '
             'Fixed settings must keep their listed values. The MS2 library selector stays authoritative.'
             if workflow_service.workflow_template_parameter(definition) == 'masscube_config_path' else
             'This run uses the edited text. MS1, MS2, and reference selectors remain authoritative.'),
        )

    @app.callback(
        Output("wf-sirius-annotation-mode", "disabled"),
        Input("wf-enable-sirius", "value"),
    )
    def toggle_sirius_annotation_mode(enable_sirius):
        return _sirius_mode_disabled(enable_sirius)

    @app.callback(
        Output("wf-dag-image", "src"),
        Output("wf-dag-image", "alt"),
        Output("wf-overview-dag-image", "src"),
        Output("wf-overview-dag-image", "alt"),
        Output("wf-overview-dag-source", "children"),
        Input("wf-id", "value"),
        Input("wf-analysis-mode", "value"),
        Input('wf-definitions-store', 'data'),
        State('token-store', 'data'),
    )
    def update_workflow_diagram(workflow_id, analysis_mode, store=None, token=None):
        """The route map and the configured technical DAG for the chosen mode.

        This used to swap in a `pipeline_dag.svg` published by an earlier run,
        read from the monitoring outputs store. Inspecting a finished run belongs
        to /jobs/<id>, so launch shows the configured diagram.
        """
        definition = workflow_service.selected_workflow_definition(_workflow_cache_rows(store, token), workflow_id)
        engine = workflow_service.workflow_engine(definition)
        if engine and engine.get('stages'):
            stage_list = metro_service.engine_stage_data_uri(engine['stages'])
            label = 'Workflow stages, including MetaboLensR preview and finalization.'
            # The same drawn map /jobs/<id> colours for this engine's runs.
            drawn = metro_service.engine_map(engine['stages'])
            overview = f"/assets/{drawn[0].name}" if drawn else stage_list
            overview_label = (
                'Route map for the selected workflow, from acquisition files through its engine and '
                'MetaboLensR preview to finalization, with its optional review pauses and branches.'
                if drawn else label
            )
            technical = workflow_diagram_service.engine_technical_dag_asset(engine)
            if technical:
                stage_list, label = technical["src"], technical["alt"]
            return stage_list, label, overview, overview_label, ""
        technical_asset = workflow_diagram_service.workflow_technical_dag_asset(analysis_mode)
        overview_asset = workflow_diagram_service.workflow_overview_asset(analysis_mode)
        return (
            technical_asset["src"],
            technical_asset["alt"],
            overview_asset["src"],
            overview_asset["alt"],
            "",
        )

    @app.callback(
        Output("wf-id", "options"),
        Output("wf-id", "value"),
        Output("wf-definition-status", "children"),
        Output("wf-definitions-store", "data"),
        Input("url", "pathname"),
        Input("wf-page-ready", "data"),
        Input("wf-list-btn", "n_clicks"),
        Input("token-store", "data"),
        State("wf-id", "value"),
        State("wf-definitions-store", "data"),
    )
    def hydrate_workflow_definitions(pathname, _, __, token, current_workflow_id, current_store):
        if not _workflow_page_is_active(pathname):
            return dash.no_update, dash.no_update, dash.no_update, dash.no_update
        trigger = dash.ctx.triggered_id
        cached_workflows = _workflow_cache_rows(current_store, token)
        if _should_reuse_workflow_cache(
            trigger,
            current_store,
            token,
            passive_trigger_ids={"url", "wf-page-ready"},
        ):
            options = _workflow_options(cached_workflows)
            selected_id = _selected_workflow_id(options, current_workflow_id)
            status = "No workflow definitions are available." if not options else ""
            return options, selected_id, status, dash.no_update
        if not token:
            return [], None, "Login first, then list workflows.", build_page_cache([], token)
        try:
            workflows = workflow_service.list_workflows(token)
            options = _workflow_options(workflows)
            selected_id = _selected_workflow_id(options, current_workflow_id)
            if not options:
                return [], None, "No workflow definitions are available.", build_page_cache(workflows, token)
            return options, selected_id, "", build_page_cache(workflows, token)
        except (APIError, ValueError) as exc:
            if page_cache_matches(current_store, token):
                options = _workflow_options(cached_workflows)
                selected_id = _selected_workflow_id(options, current_workflow_id)
                return options, selected_id, f"Workflow definition load failed: {exc}", dash.no_update
            return [], None, f"Workflow definition load failed: {exc}", build_page_cache([], token)

    @app.callback(
        Output("wf-ms1-library", "options"),
        Output("wf-ms1-library", "value"),
        Output("wf-ms2-library", "options"),
        Output("wf-ms2-library", "value"),
        Output("wf-msdial-template", "options"),
        Output("wf-msdial-template", "value"),
        Output("wf-library-status", "children"),
        Output("wf-library-store", "data"),
        Input("wf-id", "value"),
        Input("wf-list-btn", "n_clicks"),
        Input("wf-definitions-store", "data"),
        State("token-store", "data"),
        State("wf-library-store", "data"),
        State("wf-library-selection-store", "data"),
    )
    def hydrate_workflow_libraries(workflow_id, _refresh_catalog, definitions_store, token, current_store, selection_store):
        if not token:
            return [], None, [], None, [], None, "Login first to load available support files.", build_page_cache({}, token)
        if not workflow_id:
            return [], None, [], None, [], None, "Select a workflow to load available support files.", dash.no_update
        definition = workflow_service.selected_workflow_definition(_workflow_cache_rows(definitions_store, token), workflow_id)
        try:
            libraries = None if dash.ctx.triggered_id == "wf-list-btn" else _workflow_library_cache_entry(current_store, token, workflow_id)
            updated_store = dash.no_update
            if libraries is None:
                libraries = workflow_service.list_workflow_libraries(token, workflow_id)
                updated_store = _store_workflow_libraries(current_store, token, workflow_id, libraries)
            selections = _workflow_library_selections(selection_store, token, workflow_id)
            controls = _workflow_library_controls(libraries, selections, definition)
            engine = workflow_service.workflow_engine(definition)
            counts = [f'{len(controls[index])} {label}' for index, label, key in (
                (4, 'template', workflow_service.workflow_template_parameter(definition)), (0, 'MS1', 'ms1_library_path'), (2, 'MS2', 'ms2_library_path'))
                if not engine or key in ((definition or {}).get('parameter_schema_json') or {}).get('properties', {})
                or key in engine.get('support_files', [])]
            return (
                *controls,
                "" if counts else 'No support files for this workflow.',
                updated_store,
            )
        except (APIError, ValueError) as exc:
            libraries = _workflow_library_cache_entry(current_store, token, workflow_id)
            if libraries is not None:
                selections = _workflow_library_selections(selection_store, token, workflow_id)
                return (
                    *_workflow_library_controls(libraries, selections, definition),
                    f"Support file refresh failed: {exc}. Showing cached options.",
                    dash.no_update,
                )
            return [], None, [], None, [], None, f"Support file load failed: {exc}", dash.no_update

    @app.callback(
        Output("wf-library-selection-store", "data"),
        Input("wf-ms1-library", "value"),
        Input("wf-ms2-library", "value"),
        Input("wf-msdial-template", "value"),
        State("token-store", "data"),
        State("wf-id", "value"),
        State("wf-library-selection-store", "data"),
        prevent_initial_call=True,
    )
    def persist_workflow_library_selections(ms1_library, ms2_library, msdial_template, token, workflow_id, current_store):
        if not token or not _workflow_library_key(workflow_id):
            return dash.no_update
        return _store_workflow_library_selections(current_store, token, workflow_id, ms1_library, ms2_library, msdial_template)

    @app.callback(
        Output("wf-msdial-template-editor", "value"),
        Output("wf-msdial-template-source-store", "data"),
        Output("wf-msdial-template-editor-status", "children"),
        Input("wf-msdial-template", "value"),
        Input("wf-msdial-template-reset", "n_clicks"),
        Input("wf-id", "value"),
        Input("token-store", "data"),
        Input("wf-definitions-store", "data"),
        State("wf-msdial-template-source-store", "data"),
    )
    def hydrate_msdial_template_editor(msdial_template, _reset_clicks, workflow_id, token, definitions_store, source_store):
        if dash.ctx.triggered_id == "wf-msdial-template-reset":
            source = page_cache_data(source_store, {}) if page_cache_matches(source_store, token) else {}
            if source.get('workflow_id') == workflow_id and source.get("path") == msdial_template:
                return source.get("content", ""), dash.no_update, "Restored the selected template."
            return dash.no_update, dash.no_update, "Select and load a template before resetting."
        if not token:
            return "", {}, "Login first to load the selected template."
        if not workflow_id or not msdial_template:
            return "", {}, "Select a parameter template to inspect or edit it."
        try:
            definition = workflow_service.selected_workflow_definition(_workflow_cache_rows(definitions_store, token), workflow_id)
            source = workflow_service.get_workflow_template_content(token, workflow_id, msdial_template, definition)
            cached = build_page_cache({**source, 'workflow_id': workflow_id}, token)
            return source.get("content", ""), cached, f"Loaded {source.get('path', msdial_template)} for this run."
        except (APIError, ValueError) as exc:
            return "", {}, f"Template load failed: {exc}"

    @app.callback(
        Output("wf-msdial-template-download", "data"),
        Input("wf-msdial-template-download-btn", "n_clicks"),
        State("wf-msdial-template-editor", "value"),
        State("wf-msdial-template", "value"),
        prevent_initial_call=True,
    )
    def download_msdial_template(_clicks, template_text, template_path):
        filename = str(template_path or "msdial_template.txt").rsplit("/", 1)[-1]
        return {
            "content": template_text or "",
            "filename": filename,
            "type": "text/csv" if filename.lower().endswith('.csv') else "text/plain",
        }

    @app.callback(
        Output("wf-summary-run-status", "children"),
        Output("wf-summary-run-helper", "children"),
        Output("wf-summary-pipeline", "children"),
        Output("wf-summary-pipeline-helper", "children"),
        Output("wf-summary-profile", "children"),
        Output("wf-summary-container", "children"),
        Output("wf-summary-resume", "children"),
        Output("wf-summary-last-validated", "children"),
        Output("wf-command-preview", "children"),
        Output("wf-validation-alert", "children"),
        Output("wf-validation-alert", "color"),
        Output("wf-validation-alert", "style"),
        Input("token-store", "data"),
        Input("study-select-store", "data"),
        Input("assay-select-store", "data"),
        Input("wf-definitions-store", "data"),
        Input("wf-id", "value"),
        Input("wf-params", "value"),
        Input("wf-execution-target", "value"),
        Input("wf-ms1-library", "value"),
        Input("wf-ms2-library", "value"),
        Input("wf-msdial-template", "value"),
        Input("wf-reference-file", "value"),
        Input("wf-review-stages", "value"),
        Input("wf-analysis-mode", "value"),
        Input("wf-enable-sirius", "value"),
        Input("wf-sirius-annotation-mode", "value"),
        Input("workflow-file-selection-store", "data"),
        Input("wf-metabolensr-profile", "value"),
        Input("wf-curation-rule-context", "data"),
        Input("wf-curation-statistics", "value"),
        Input("wf-curation-annotation", "value"),
    )
    def refresh_studio_preview(
        token,
        study_id,
        assay_id,
        workflow_definitions,
        workflow_id,
        workflow_params,
        execution_target,
        ms1_library,
        ms2_library,
        msdial_template,
        reference_file_id,
        review_stages,
        analysis_mode,
        enable_sirius,
        sirius_annotation_mode,
        selection_store,
        metabolensr_profile=None,
        metabolensr_rule_context=None,
        metabolensr_statistics_rules=None,
        metabolensr_annotation_rules=None,
    ):
        workflow_definitions = _workflow_cache_rows(workflow_definitions, token)
        definition = workflow_service.selected_workflow_definition(workflow_definitions, workflow_id)
        metadata = workflow_service.workflow_preview_metadata(workflow_definitions, workflow_id, workflow_params, execution_target)
        readiness = workflow_service.workflow_launch_readiness(
            token,
            assay_id,
            workflow_id,
            workflow_params,
            ms1_library,
            ms2_library,
            msdial_template,
            reference_file_id,
            selection_store,
            study_id,
            workflow_definition=definition,
        )
        resume_enabled = False
        try:
            enabled_rules = workflow_service.workflow_curation_rule_selection(
                definition, metabolensr_profile, metabolensr_rule_context,
                metabolensr_statistics_rules, metabolensr_annotation_rules)
        except ValueError as exc:
            enabled_rules = None
            readiness = {'ready': False, 'missing': ['rule selection'], 'message': str(exc)}
        command = workflow_service.build_nextflow_command_preview(
            workflow_definitions,
            workflow_id,
            workflow_params,
            ms1_library,
            ms2_library,
            msdial_template,
            reference_file_id,
            analysis_mode,
            execution_target=execution_target,
            enable_sirius=enable_sirius,
            sirius_annotation_mode=sirius_annotation_mode,
            review_stages=workflow_service.workflow_review_selection(definition, review_stages),
            metabolensr_profile=metabolensr_profile,
            metabolensr_enabled_rules=enabled_rules,
        )
        command_preview = format_command_preview(command)
        pipeline_helper = f"Definition #{definition.get('id')}" if definition else "Select from catalog"
        status_value = "READY" if readiness["ready"] else "SETUP"
        status_helper = "All checks passed" if readiness["ready"] else f"Missing {len(readiness['missing'])} item(s)"
        alert_color = "success" if readiness["ready"] else ("danger" if "valid parameters JSON" in readiness["missing"] else "warning")
        alert_message = "" if readiness["ready"] else readiness["message"]
        alert_style = {"display": "none"} if readiness["ready"] else {}
        last_validated = datetime.now().strftime("%Y-%m-%d %H:%M") if readiness["ready"] else "Pending"
        return (
            status_value,
            status_helper,
            metadata["pipeline"],
            pipeline_helper,
            metadata["profile"],
            metadata["container"],
            "Enabled" if resume_enabled else "Disabled",
            last_validated,
            command_preview,
            alert_message,
            alert_color,
            alert_style,
        )

    @app.callback(
        Output("wf-reference-file", "options"),
        Output("wf-reference-file", "value"),
        Output("wf-reference-status", "children"),
        Input("wf-page-ready", "data"),
        State("token-store", "data"),
        State("study-select-store", "data"),
        State("assay-select-store", "data"),
        State("workflow-file-selection-store", "data"),
        State("wf-reference-file", "value"),
    )
    def hydrate_reference_files(_, token, study_id, assay_id, selection_store, current_reference_file_id):
        if not token:
            return [], None, "Login first to load reference-file choices."
        if not study_id or not assay_id:
            return [], None, "Select a study and assay to choose a reference file."
        try:
            files = file_service.list_assay_files(token, assay_id)
            eligible_rows = _eligible_reference_files(files, selection_store, study_id, assay_id)
            options = _reference_options(eligible_rows)
            value = _reference_default(options, current_reference_file_id)
            if not options:
                return [], None, "No eligible RAW input file is available as reference for the active assay."
            if selection_store and selection_store.get("study_id") == study_id and selection_store.get("assay_id") == assay_id and selection_store.get("mode") == "selected":
                return options, value, f"Reference choices are limited to {len(options)} selected workflow input file(s)."
            return options, value, ""
        except (APIError, ValueError) as exc:
            return [], None, f"Reference file load failed: {exc}"

    @app.callback(
        Output("wf-selected-files-status", "children"),
        Input("workflow-file-selection-store", "data"),
        Input("study-select-store", "data"),
        Input("assay-select-store", "data"),
    )
    def workflow_selection_status(selection_store, study_id, assay_id):
        if not study_id:
            return "Workflow input files: select a study first."
        if not assay_id:
            return "Workflow input files: select an assay for the active study."
        # All eligible inputs is the normal case; only a subset is worth a line.
        if not selection_store or selection_store.get("study_id") != study_id or selection_store.get("assay_id") != assay_id:
            return ""
        if selection_store.get("mode") == "selected":
            selected_count = len(selection_store.get("selected_file_ids") or [])
            if selected_count:
                return f"Workflow input files: {selected_count} selected raw input(s) from Files > Workflow Input Files for the active assay."
            return "Workflow input files: selected mode is active, but no assay raw input is selected."
        return ""

    @app.callback(
        Output("wf-status", "children"),
        Output("workflow-mutation-signal", "data"),
        Output("wf-workflow-command-result-store", "data"),
        Input("wf-launch-btn", "n_clicks"),
        *_launch_form_states(),
        prevent_initial_call=True,
    )
    def execute_workflow_action(
        _,
        pathname,
        token,
        study_id,
        assay_id,
        wf_id,
        wf_params,
        ms1_library,
        ms2_library,
        msdial_template,
        msdial_template_text,
        template_source,
        reference_file_id,
        execution_target,
        review_stages,
        analysis_mode,
        enable_sirius,
        sirius_annotation_mode,
        selection_store,
        mutation_signal,
        definitions_store,
        metabolensr_profile,
        metabolensr_rule_context,
        metabolensr_statistics_rules,
        metabolensr_annotation_rules,
    ):
        if not _workflow_page_is_active(pathname):
            return dash.no_update, dash.no_update, dash.no_update
        if dash.ctx.triggered_id != "wf-launch-btn" or not real_click():
            return dash.no_update, dash.no_update, dash.no_update
        if not token:
            return "Login first", dash.no_update, dash.no_update
        if not assay_id:
            return "Select an assay before starting a workflow.", dash.no_update, dash.no_update
        mutation_update = int(mutation_signal or 0) + 1
        try:
            definition = workflow_service.selected_workflow_definition(
                _workflow_cache_rows(definitions_store, token), wf_id)
            if msdial_template and workflow_service.workflow_form_controls(definition)['parameter_template']:
                source = page_cache_data(template_source, {}) if page_cache_matches(template_source, token) else {}
                if source.get('workflow_id') != wf_id or source.get('path') != msdial_template:
                    raise ValueError('Wait for the selected parameter template to load before launching.')
            selected_file_ids = None
            if selection_store and selection_store.get("study_id") == study_id and selection_store.get("assay_id") == assay_id and selection_store.get("mode") == "selected":
                selected_file_ids = selection_store.get("selected_file_ids") or []
            launch = workflow_service.launch_workflow(
                token=token,
                assay_id=assay_id,
                workflow_id=wf_id,
                workflow_params=wf_params,
                file_ids=selected_file_ids,
                ms1_library_path=ms1_library,
                ms2_library_path=ms2_library,
                msdial_config_path=msdial_template,
                msdial_template_text=msdial_template_text,
                reference_file_id=reference_file_id,
                analysis_mode=analysis_mode,
                execution_target=execution_target,
                enable_sirius=enable_sirius,
                sirius_annotation_mode=sirius_annotation_mode,
                workflow_definition=definition,
                review_stages=workflow_service.workflow_review_selection(definition, review_stages),
                metabolensr_profile=metabolensr_profile,
                metabolensr_enabled_rules=workflow_service.workflow_curation_rule_selection(
                    definition, metabolensr_profile, metabolensr_rule_context,
                    metabolensr_statistics_rules, metabolensr_annotation_rules),
            )
            job_id = int(launch["id"])
            message = (
                f"Launched workflow job #{job_id} with all eligible raw inputs for the active assay"
                if selected_file_ids is None
                else f"Launched workflow job #{job_id} with {len(selected_file_ids)} selected assay raw input(s)"
            )
            return message, mutation_update, {"revision": mutation_update, "action": "launch", "assay_id": assay_id, "job_id": job_id}
        except (APIError, ValueError, json.JSONDecodeError) as exc:
            return f"Workflow action failed: {exc}", dash.no_update, dash.no_update

    _assert_launch_form_signature(execute_workflow_action, leading_inputs=1)
