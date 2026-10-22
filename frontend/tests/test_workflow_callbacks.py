import unittest
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import dash
from dash import ALL, Input, Output, State
import callbacks.workflows as workflow_callbacks

from callbacks.workflows import (
    ALL_JOBS_VALUE,
    _default_active_job_value,
    _dropdown_value_present,
    _eic_archive_status,
    _eic_archive_options,
    _feature_index_for_trace_request,
    _feature_index_from_controls,
    _feature_index_from_map_click,
    _job_can_resume,
    _job_selector_options,
    _opened_job_id,
    _selected_library_value,
    _selected_active_job_value,
    _should_refresh_jobs,
    _should_refresh_outputs,
    _should_reuse_job_cache,
    _should_reuse_output_cache,
    _sirius_mode_disabled,
    _stable_dropdown_value_update,
    _store_workflow_libraries,
    _store_workflow_library_selections,
    _workflow_page_is_active,
    _workflow_jobs_revision,
    _workflow_library_cache_entry,
    _workflow_library_controls,
    _workflow_library_selections,
    _workflow_output_cache,
    cache_workflow_outputs_after_fetch_failure,
    register_callbacks,
    IS_QC_TARGET_HEADERS,
    _is_qc_target_table_rows,
    _is_qc_target_options,
    _is_qc_rt_model_options,
    _selected_rt_model_targets,
    _rt_correction_control_state,
)
from services.page_cache import build_page_cache, page_cache_data, should_reuse_page_cache
from services.api_client import APIError


class _CallbackCapture:
    def __init__(self) -> None:
        self.dependencies = []
        self.callbacks = {}
        self.callback_dependencies = {}

    def callback(self, *dependencies, **_kwargs):
        self.dependencies.append(dependencies)

        def decorator(callback):
            self.callbacks[callback.__name__] = callback
            self.callback_dependencies[callback.__name__] = dependencies
            return callback

        return decorator


class LaunchButtonTests(unittest.TestCase):
    def test_rendering_the_page_does_not_launch_or_report_a_failure(self):
        """The page render fires the launch callback with n_clicks=0; that once
        showed 'Workflow action failed' before anything was clicked."""
        app = _CallbackCapture()
        register_callbacks(app)
        action = app.callbacks['execute_workflow_action']
        args = [0, '/workflows', 'tok'] + [None] * (len(inspect.signature(action).parameters) - 3)
        ctx = SimpleNamespace(triggered_id='wf-launch-btn',
                              triggered=[{'prop_id': 'wf-launch-btn.n_clicks', 'value': 0}])
        with patch('callbacks.workflows._register.dash.ctx', ctx), \
                patch('callbacks.workflows._register.workflow_service.launch_workflow') as launch:
            result = action(*args)
        self.assertEqual(result, (dash.no_update,) * 3)
        launch.assert_not_called()

    def test_a_real_click_still_launches(self):
        app = _CallbackCapture()
        register_callbacks(app)
        action = app.callbacks['execute_workflow_action']
        names = list(inspect.signature(action).parameters)
        values = {name: None for name in names}
        values.update({names[0]: 1, 'pathname': '/workflows', 'token': 'tok',
                       'study_id': 9, 'assay_id': 18, 'wf_id': 3, 'wf_params': '{}', 'mutation_signal': 0})
        ctx = SimpleNamespace(triggered_id='wf-launch-btn',
                              triggered=[{'prop_id': 'wf-launch-btn.n_clicks', 'value': 1}])
        with patch('callbacks.workflows._register.dash.ctx', ctx), \
                patch('callbacks.workflows._register.workflow_service.launch_workflow',
                      return_value={'id': 7}) as launch:
            message, *_ = action(*[values[name] for name in names])
        launch.assert_called_once()
        self.assertIn('#7', message)


class LaunchRouteMapTests(unittest.TestCase):
    def _diagram(self, stages, engine_id=None):
        app = _CallbackCapture()
        register_callbacks(app)
        engine = {'stages': stages, **({'engine_id': engine_id} if engine_id else {})}
        definition = {'id': 3, 'name': 'Engine', 'static_input_config_json': {'engine': engine}}
        return app.callbacks['update_workflow_diagram'](3, 'full', build_page_cache([definition], 'token'), 'token')

    def test_a_drawn_engine_shows_its_route_map(self):
        stages = [{'id': 'engine', 'label': 'MS-DIAL', 'patterns': ['peak_detection_msdial5']},
                  {'id': 'qc_preview', 'label': 'Preview', 'patterns': ['METABOLENSR_PREVIEW']}]
        _, _, overview, alt, _ = self._diagram(stages)
        self.assertEqual(overview, '/assets/metabolensr-workflow-metro.svg')
        self.assertIn('MetaboLensR preview', alt)

    def test_a_masscube_definition_shows_the_masscube_map(self):
        stages = [{'id': 'engine', 'label': 'MassCube', 'patterns': ['MASSCUBE_PROCESS']},
                  {'id': 'qc_preview', 'label': 'Preview', 'patterns': ['METABOLENSR_PREVIEW']}]
        dag, _, overview, _, _ = self._diagram(stages, 'masscube')
        self.assertEqual(overview, '/assets/masscube-workflow-metro.svg')
        self.assertEqual(dag, '/assets/masscube-workflow-dag.svg')

    def test_an_engine_with_no_drawn_map_keeps_the_stage_list(self):
        _, _, overview, _, _ = self._diagram([{'id': 'fixture_detect', 'label': 'Detect', 'patterns': ['x']}])
        self.assertTrue(overview.startswith('data:image/svg+xml'))

    def test_shared_stage_ids_do_not_select_another_engines_drawn_map(self):
        from urllib.parse import unquote
        stages = [{'id': 'prepare', 'label': 'Prepare acquisitions', 'patterns': ['prepare_acquisition_input']},
                  {'id': 'engine', 'label': 'Third engine', 'patterns': ['THIRD_ENGINE_PROCESS']},
                  {'id': 'qc_preview', 'label': 'Preview', 'patterns': ['METABOLENSR_PREVIEW']},
                  {'id': 'finalize', 'label': 'Finalize', 'patterns': ['METABOLENSR_FINALIZE']}]
        technical, _, overview, alt, _ = self._diagram(stages)
        self.assertEqual(overview, technical)
        self.assertTrue(overview.startswith('data:image/svg+xml'))
        self.assertIn('Third engine', unquote(overview))
        self.assertNotIn('MS-DIAL', alt)


class WorkflowCallbackHelperTests(unittest.TestCase):
    def test_masscube_template_selector_preview_and_download(self):
        app = _CallbackCapture()
        register_callbacks(app)
        definition = {'id': 3, 'name': 'MassCube', 'static_input_config_json': {'engine': {
            'required_inputs': {}, 'execution_profiles': ['local_docker'], 'package': {'config': {}}}},
            'parameter_schema_json': {'properties': {'masscube_config_path': {'type': 'string'}}}}
        definitions = build_page_cache([definition], 'token')
        form = app.callbacks['configure_workflow_form'](3, definitions, 'token', None, None)
        self.assertEqual(form[4], {})
        self.assertFalse(form[10])
        self.assertIn('MassCube', form[11])
        path = 'templates/masscube/comparison.csv'
        catalog = {'masscube_templates': [{'label': 'comparison', 'value': path}],
                   'default_masscube_config_path': path}
        self.assertEqual(_workflow_library_controls(catalog, {}, definition)[5], path)
        source = {'path': path, 'content': 'parameter,value\nrt_upper_limit,18\n'}
        with patch('callbacks.workflows.dash.ctx', SimpleNamespace(triggered_id='wf-id')), \
                patch('callbacks.workflows.workflow_service.get_workflow_template_content', return_value=source) as api:
            loaded = app.callbacks['hydrate_msdial_template_editor'](path, 0, 3, 'token', definitions, {})
        api.assert_called_once_with('token', 3, path, definition)
        with patch('callbacks.workflows.dash.ctx', SimpleNamespace(triggered_id='wf-msdial-template-reset')):
            stale = app.callbacks['hydrate_msdial_template_editor'](path, 1, 4, 'token', definitions, loaded[1])
        self.assertIs(stale[0], dash.no_update)
        download = app.callbacks['download_msdial_template'](1, source['content'], path)
        self.assertEqual(download['type'], 'text/csv')
        self.assertEqual(download['content'], source['content'])

    def _library_status(self, support_files):
        app = _CallbackCapture()
        register_callbacks(app)
        definition = {'id': 17, 'static_input_config_json': {'engine': {
            'required_inputs': {}, 'support_files': support_files}}}
        libraries = {'ms1_libraries': [{'label': 'MS1', 'value': '/ms1.txt'}],
                     'ms2_libraries': [{'label': 'MS2', 'value': '/ms2.msp'}],
                     'msdial_templates': [{'label': 'Template', 'value': '/template.txt'}]}
        with patch('callbacks.workflows.dash.ctx', SimpleNamespace(triggered_id='wf-id')):
            result = app.callbacks['hydrate_workflow_libraries'](17, 0,
                build_page_cache([definition], 'token'), 'token',
                _store_workflow_libraries({}, 'token', 17, libraries), {})
        return result[6]

    def test_library_status_is_silent_when_the_engine_has_support_files(self):
        self.assertEqual(self._library_status(['ms2_library_path']), '')

    def test_library_status_says_when_the_engine_uses_no_support_files(self):
        self.assertEqual(self._library_status([]), 'No support files for this workflow.')

    def test_optional_engine_library_can_stay_empty_and_survive_clear_and_refresh(self):
        app = _CallbackCapture()
        register_callbacks(app)
        definition = {'id': 17, 'static_input_config_json': {'engine': {
            'engine_id': 'thirdengine', 'required_inputs': {}, 'support_files': ['ms2_library_path']}}}
        definitions = build_page_cache([definition], 'token')
        libraries = {'ms2_libraries': [{'label': 'MS2', 'value': '/ms2.msp'}]}
        cache = _store_workflow_libraries({}, 'token', 17, libraries)
        selections = _store_workflow_library_selections({}, 'token', 17, None, '/ms2.msp', None)
        with patch('callbacks.workflows.dash.ctx', SimpleNamespace(triggered_id='wf-id')):
            empty = app.callbacks['hydrate_workflow_libraries'](17, 0, definitions, 'token', cache, {})
            selected = app.callbacks['hydrate_workflow_libraries'](17, 0, definitions, 'token', cache, selections)
        self.assertEqual(empty[2], [{'label': 'MS2', 'value': '/ms2.msp'}])
        self.assertIsNone(empty[3])
        self.assertEqual(selected[3], '/ms2.msp')
        cleared = app.callbacks['persist_workflow_library_selections'](None, None, None, 'token', 17, selections)
        self.assertEqual(_workflow_library_selections(cleared, 'token', 17)['ms2_library'], None)
        # A remembered clear wins even over a configured default.
        libraries['default_ms2_library_path'] = '/ms2.msp'
        with (patch('callbacks.workflows.dash.ctx', SimpleNamespace(triggered_id='wf-list-btn')),
              patch('callbacks.workflows.workflow_service.list_workflow_libraries', return_value=libraries)):
            refreshed = app.callbacks['hydrate_workflow_libraries'](17, 1, definitions, 'token', cache, cleared)
        self.assertIsNone(refreshed[3])
        with (patch('callbacks.workflows.dash.ctx', SimpleNamespace(triggered_id='wf-list-btn')),
              patch('callbacks.workflows.workflow_service.list_workflow_libraries', side_effect=ValueError('offline'))):
            cached = app.callbacks['hydrate_workflow_libraries'](17, 1, definitions, 'token', cache, cleared)
        self.assertIsNone(cached[3])

    def test_descriptor_required_library_keeps_automatic_default(self):
        app = _CallbackCapture()
        register_callbacks(app)
        definition = {'id': 17, 'static_input_config_json': {'engine': {
            'required_inputs': {'ms2_library_path': 'MS2 library'}, 'support_files': ['ms2_library_path']}}}
        definitions = build_page_cache([definition], 'token')
        libraries = {'ms2_libraries': [{'label': 'MS2', 'value': '/ms2.msp'}]}
        cache = _store_workflow_libraries({}, 'token', 17, libraries)
        with patch('callbacks.workflows.dash.ctx', SimpleNamespace(triggered_id='wf-id')):
            result = app.callbacks['hydrate_workflow_libraries'](17, 0, definitions, 'token', cache, {})
        self.assertEqual(result[3], '/ms2.msp')

    def test_preview_can_resume_without_rt_choices(self):
        job = {'id': 41, 'run_root': '/run', 'status': 'succeeded',
               'parameter_values_json': {'workflow_stage': 'qc_preview'},
               'input_manifest_json': {'engine': {'descriptor': {'review_transitions': {'qc_preview': 'full'}}}}}
        allowed, explanation = _job_can_resume(job)
        self.assertTrue(allowed)
        self.assertIn('automatic', explanation)
        job['input_manifest_json']['engine']['descriptor']['review_transitions'] = {}
        job['parameter_values_json']['workflow_stage'] = 'msdial_review'
        self.assertFalse(_job_can_resume(job)[0])

    def test_workflow_selection_refreshes_modes_profiles_and_input_controls(self):
        app = _CallbackCapture()
        register_callbacks(app)
        definition = {'id': 3, 'name': 'Fixture engine',
                      'static_input_config_json': {'engine': {'execution_profiles': ['local_docker'],
                          'package': {'config': {'profile': 'strict'}}}},
                      'parameter_schema_json': {'properties': {
                          'analysis_mode': {'enum': ['qc_only'], 'default': 'qc_only'},
                          'workflow_stage': {'enum': ['full', 'qc_preview'], 'default': 'full'}}}}
        result = app.callbacks['configure_workflow_form'](
            3, build_page_cache([definition], 'token'), 'token', 'full', 'slurm_singularity_ssh')
        self.assertEqual(result[1], 'qc_only')
        self.assertEqual(result[3], 'local_docker')
        self.assertEqual(result[4:10], ({'display': 'none'},) * 6)
        self.assertTrue(result[10])
        self.assertEqual(len(result), 13)  # template label and helper; no generated description

    def test_template_editor_loads_and_resets_the_selected_source(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        source = {
            "path": "templates/msdial/selected.txt",
            "content": "MS-DIAL ver. 5.5\nMinimum peak height: 1000\n",
            "checksum_sha256": "a" * 64,
        }

        with (
            patch("callbacks.workflows.dash.ctx", SimpleNamespace(triggered_id="wf-msdial-template")),
            patch("callbacks.workflows.workflow_service.get_workflow_template_content", return_value=source),
        ):
            loaded = app.callbacks["hydrate_msdial_template_editor"](
                "templates/msdial/selected.txt", 0, 17, "token", {}, {}
            )

        with patch("callbacks.workflows.dash.ctx", SimpleNamespace(triggered_id="wf-msdial-template-reset")):
            reset = app.callbacks["hydrate_msdial_template_editor"](
                "templates/msdial/selected.txt", 1, 17, "token", {}, loaded[1]
            )

        self.assertEqual(loaded[0], source["content"])
        self.assertEqual(loaded[1]['data'], {**source, 'workflow_id': 17})
        self.assertIn("Loaded", loaded[2])
        self.assertEqual(reset[0], source["content"])
        self.assertIs(reset[1], dash.no_update)
        self.assertIn("Restored", reset[2])

    def test_template_editor_download_uses_current_text_and_filename(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)

        result = app.callbacks["download_msdial_template"](
            1,
            "MS-DIAL ver. 5.5\nMinimum peak height: 2500\n",
            "templates/msdial/selected.txt",
        )

        self.assertEqual(result["filename"], "selected.txt")
        self.assertEqual(result["type"], "text/plain")
        self.assertIn("Minimum peak height: 2500", result["content"])

    def test_workflow_definitions_reuse_matching_session_cache_on_page_mount(self) -> None:
        cache = build_page_cache([{"id": 1, "name": "MS-DIAL"}], "token")

        self.assertTrue(
            should_reuse_page_cache(
                cache,
                "token",
                triggered_id="wf-page-ready",
                passive_trigger_ids={"wf-page-ready"},
            )
        )

    def test_workflow_jobs_reuse_cache_until_poll_or_refresh(self) -> None:
        cache = build_page_cache([{"id": 110, "status": "succeeded"}], "token", study_id=9, assay_id=18)

        self.assertTrue(
            should_reuse_page_cache(
                cache,
                "token",
                triggered_id="wf-page-ready",
                passive_trigger_ids={"wf-page-ready"},
                study_id=9,
                assay_id=18,
            )
        )
        self.assertFalse(
            should_reuse_page_cache(
                cache,
                "token",
                triggered_id="wf-jobs-refresh-interval",
                passive_trigger_ids={"wf-page-ready"},
                study_id=9,
                assay_id=18,
            )
        )

    def test_workflow_outputs_reuse_cache_when_panels_reopen(self) -> None:
        cache = build_page_cache([{"file_id": 501}], "token", study_id=9, assay_id=18)

        self.assertEqual(page_cache_data(cache, []), [{"file_id": 501}])

    def test_hidden_workflow_page_does_not_poll(self) -> None:
        self.assertFalse(_workflow_page_is_active("/studies"))
        self.assertTrue(_workflow_page_is_active("/workflows"))

    def test_workflow_cache_reuse_requires_exact_auth_study_and_assay_context(self) -> None:
        cache = build_page_cache([{"id": 110}], "token", study_id=9, assay_id=18)

        self.assertFalse(_should_reuse_job_cache("wf-page-ready", cache, "other-token", 9, 18, False, False, False))
        self.assertFalse(_should_reuse_job_cache("wf-page-ready", cache, "token", 10, 18, False, False, False))
        self.assertFalse(_should_reuse_output_cache("wf-page-ready", cache, "token", 9, 19, False, False, None, 110))

    def test_output_cache_scope_matches_only_owning_job(self) -> None:
        cache_matches = getattr(workflow_callbacks, "_workflow_output_cache_matches", None)

        self.assertIsNotNone(cache_matches)
        cache = build_page_cache([], "token", study_id=9, assay_id=18)
        cache["active_job_id"] = 124

        self.assertTrue(cache_matches(cache, "token", 9, 18, 124))
        self.assertFalse(cache_matches(cache, "token", 9, 18, 123))

    def test_library_cache_and_selections_require_exact_auth_and_workflow(self) -> None:
        libraries = {
            "ms1_libraries": [{"label": "MS1", "value": "/ms1.msp"}],
            "ms2_libraries": [{"label": "MS2", "value": "/ms2.msp"}],
            "msdial_templates": [{"label": "Template", "value": "/template.txt"}],
        }
        library_store = _store_workflow_libraries({}, "token", 17, libraries)
        selection_store = _store_workflow_library_selections(
            {},
            "token",
            17,
            "/ms1.msp",
            "/ms2.msp",
            "/template.txt",
        )

        self.assertEqual(_workflow_library_cache_entry(library_store, "token", 17), libraries)
        self.assertIsNone(_workflow_library_cache_entry(library_store, "other-token", 17))
        self.assertIsNone(_workflow_library_cache_entry(library_store, "token", 18))
        self.assertEqual(
            _workflow_library_selections(selection_store, "token", 17),
            {"ms1_library": "/ms1.msp", "ms2_library": "/ms2.msp", "msdial_template": "/template.txt"},
        )
        self.assertEqual(_workflow_library_selections(selection_store, "other-token", 17), {})
        self.assertEqual(_workflow_library_selections(selection_store, "token", 18), {})

    def test_library_selection_persists_only_while_option_remains_valid(self) -> None:
        options = [{"label": "Default", "value": "/default.msp"}, {"label": "Custom", "value": "/custom.msp"}]

        self.assertEqual(_selected_library_value(options, "/custom.msp", "/default.msp"), "/custom.msp")
        self.assertEqual(_selected_library_value(options, "/removed.msp", "/default.msp"), "/default.msp")

    def test_failed_explicit_library_refresh_preserves_exact_cached_choices(self) -> None:
        app = _CallbackCapture()
        register_callbacks(app)
        libraries = {
            "ms1_libraries": [{"label": "MS1", "value": "/ms1.msp"}],
            "ms2_libraries": [{"label": "MS2", "value": "/ms2.msp"}],
            "msdial_templates": [{"label": "Template", "value": "/template.txt"}],
        }
        library_store = _store_workflow_libraries({}, "token", 17, libraries)
        selection_store = _store_workflow_library_selections(
            {},
            "token",
            17,
            "/ms1.msp",
            "/ms2.msp",
            "/template.txt",
        )

        with (
            patch("callbacks.workflows.dash.ctx", SimpleNamespace(triggered_id="wf-list-btn")),
            patch("callbacks.workflows.workflow_service.list_workflow_libraries", side_effect=ValueError("offline")),
        ):
            result = app.callbacks["hydrate_workflow_libraries"](17, 1, {}, "token", library_store, selection_store)
            mismatched = app.callbacks["hydrate_workflow_libraries"](17, 1, {}, "other-token", library_store, selection_store)

        self.assertEqual(result[0], [{"label": "MS1", "value": "/ms1.msp"}])
        self.assertEqual(result[1], "/ms1.msp")
        self.assertEqual(result[2], [{"label": "MS2", "value": "/ms2.msp"}])
        self.assertEqual(result[3], "/ms2.msp")
        self.assertEqual(result[4], [{"label": "Template", "value": "/template.txt"}])
        self.assertEqual(result[5], "/template.txt")
        self.assertIn("offline", result[6])
        self.assertIs(result[7], dash.no_update)
        self.assertEqual(mismatched[:6], ([], None, [], None, [], None))

    def test_is_qc_options_include_all_standards_overview(self) -> None:
        options = _is_qc_target_options([{"target_name": "IS:A"}, {"target_name": "IS:B"}])

        self.assertEqual(
            options[0],
            {"label": "All standards (RT overview)", "value": "__all__"},
        )

    def test_rt_model_options_are_unique_compounds_and_default_to_all(self) -> None:
        options = _is_qc_rt_model_options([
            {"target_id": "1", "target_name": "IS:A", "sample_name": "S1"},
            {"target_id": "1", "target_name": "IS:A", "sample_name": "S2"},
            {"target_id": "2", "target_name": "IS:B", "sample_name": "S1"},
        ])

        self.assertEqual(options, [
            {"label": "IS:A", "value": "1"},
            {"label": "IS:B", "value": "2"},
        ])
        self.assertEqual(_selected_rt_model_targets(options, None), ["1", "2"])
        self.assertEqual(_selected_rt_model_targets(options, ["2"]), ["2"])

    def test_rt_correction_control_is_enabled_only_for_eligible_is_qc_job(self) -> None:
        eligible = {
            "parameter_values_json": {"workflow_stage": "is_qc_review"},
            "status_payload_json": {"rt_correction": {"can_apply": True}},
        }
        blocked = {
            "parameter_values_json": {"workflow_stage": "is_qc_review"},
            "status_payload_json": {
                "rt_correction": {
                    "can_apply": False,
                    "blocking_reasons": ["S2 has no anchor"],
                }
            },
        }

        self.assertEqual(
            _rt_correction_control_state(eligible, False),
            (False, False, "RT correction is available after review."),
        )
        self.assertEqual(
            _rt_correction_control_state(blocked, True),
            (True, False, "S2 has no anchor"),
        )
        self.assertEqual(
            _rt_correction_control_state(
                eligible,
                True,
                {"job_id": 7, "can_apply": False, "blocking_reasons": ["No selected anchor"]},
                active_job_id=7,
            ),
            (True, False, "No selected anchor"),
        )


    def test_sirius_mode_is_enabled_only_when_sirius_is_selected(self) -> None:
        self.assertTrue(_sirius_mode_disabled(False))
        self.assertFalse(_sirius_mode_disabled(True))

    def test_feature_index_from_map_click_reads_customdata(self) -> None:
        click_data = {"points": [{"customdata": [42, 100, "Feature"]}]}

        self.assertEqual(_feature_index_from_map_click(click_data), 42)

    def test_job_selector_defaults_to_latest_job_not_all_jobs(self) -> None:
        jobs = [
            {"id": 43, "status": "failed", "parameter_values_json": {"workflow_stage": "full"}, "status_payload_json": {}, "created_at": "2026-05-02"},
            {"id": 42, "status": "succeeded", "parameter_values_json": {"workflow_stage": "msdial_review"}, "status_payload_json": {}, "created_at": "2026-05-01"},
        ]

        options = _job_selector_options(jobs)

        self.assertEqual(options[0]["value"], ALL_JOBS_VALUE)
        self.assertEqual(_default_active_job_value(options), 43)
        self.assertEqual(_selected_active_job_value(options, 42), 42)
        self.assertEqual(_selected_active_job_value(options, "43"), 43)
        self.assertEqual(_selected_active_job_value(options, 99), 43)

    def test_eic_archive_options_label_job_and_execution_profile(self) -> None:
        options = _eic_archive_options(
            [
                {
                    "artifact_role": "msdial_eic_archive",
                    "workflow_job_id": 73,
                    "execution_profile": "slurm_singularity_ssh",
                    "file_id": 2267,
                    "original_filename": "AlignResult.EIC.aef",
                    "file_size_bytes": 2048,
                },
                {
                    "artifact_role": "msdial_alignment",
                    "workflow_job_id": 73,
                    "file_id": 2264,
                    "original_filename": "AlignResult.mdalign",
                    "file_size_bytes": 1024,
                },
            ]
        )

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["value"], 2267)
        self.assertIn("Job #73", options[0]["label"])
        self.assertIn("HPC SSH", options[0]["label"])

    def test_stable_dropdown_updates_preserve_current_value(self) -> None:
        options = [{"label": "All jobs", "value": ALL_JOBS_VALUE}, {"label": "Job #42", "value": 42}]

        self.assertTrue(_dropdown_value_present(options, "42"))
        self.assertIs(_stable_dropdown_value_update(options, "42", 99), dash.no_update)
        self.assertEqual(_stable_dropdown_value_update(options, 99, 42), 42)

    def test_job_can_resume_paused_review_and_failed_resume_retry_only(self) -> None:
        engine = {"engine": {"descriptor": {"review_transitions": {"is_qc_review": "qc_preview", "qc_preview": "full"}}}}
        paused = {
            "id": 42,
            "status": "succeeded",
            "run_root": "/tmp/job_42",
            "input_manifest_json": engine,
            "parameter_values_json": {"workflow_stage": "qc_preview"},
            "status_payload_json": {},
        }
        is_qc_paused = {
            "id": 45,
            "status": "succeeded",
            "run_root": "/tmp/job_45",
            "input_manifest_json": engine,
            "parameter_values_json": {"workflow_stage": "is_qc_review"},
            "status_payload_json": {"is_qc_status": "pass"},
        }
        failed_retry = {
            "id": 43,
            "status": "failed",
            "run_root": "/tmp/job_43",
            "input_manifest_json": engine,
            "parameter_values_json": {"nextflow_resume": True},
            "status_payload_json": {},
        }
        failed_non_resume = {
            "id": 44,
            "status": "failed",
            "run_root": "/tmp/job_44",
            "input_manifest_json": engine,
            "parameter_values_json": {},
            "status_payload_json": {},
        }
        retired_legacy = {
            "id": 46,
            "status": "succeeded",
            "run_root": "/tmp/job_46",
            "input_manifest_json": {"file_ids": [1]},
            "parameter_values_json": {"workflow_stage": "msdial_review"},
            "status_payload_json": {},
        }

        self.assertTrue(_job_can_resume(paused)[0])
        self.assertTrue(_job_can_resume(is_qc_paused)[0])
        self.assertIn("IS-QC", _job_can_resume(is_qc_paused)[1])
        self.assertTrue(_job_can_resume(failed_retry)[0])
        self.assertFalse(_job_can_resume(failed_non_resume)[0])
        allowed, reason = _job_can_resume(retired_legacy)
        self.assertFalse(allowed)
        self.assertIn("retired MS-DIAL workflow", reason)

    def test_failed_output_refresh_does_not_preserve_another_jobs_rows(self) -> None:
        cache = _workflow_output_cache(
            [{"workflow_job_id": 123, "artifact_role": "msdial_eic_archive", "file_id": 501}],
            "token",
            9,
            18,
            "running-revision",
            123,
        )

        recovered = cache_workflow_outputs_after_fetch_failure(
            cache,
            "token",
            9,
            18,
            "terminal-revision",
            124,
            ValueError("offline"),
        )

        self.assertEqual(page_cache_data(recovered, []), [])
        self.assertEqual(recovered["active_job_id"], 124)
        self.assertIn("Workflow output load failed: offline", recovered["error"])
        self.assertNotIn("warning", recovered)


if __name__ == "__main__":
    unittest.main()
