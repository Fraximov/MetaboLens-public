"""The displayed package policy and submitted policy must agree."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from callbacks.workflows import register_callbacks
from callbacks.workflows.launch import LAUNCH_FORM_STATES
from services import workflow_service
from services.page_cache import build_page_cache
from test_workflow_callbacks import _CallbackCapture
from test_workflow_review_pauses import definition
from test_workflows_page import _walk


def workflow():
    result = definition()
    result['static_input_config_json']['engine']['package']['container_image'] = 'package@sha256:' + 'a' * 64
    result['static_input_config_json']['engine']['package']['curation_profiles'] = {
        'strict': {'name': 'strict', 'statistics': [
            {'rule_id': 'has_annotation', 'params': {'on_false': 'exclude'}},
            {'rule_id': 'minimum_sample_detection_pct', 'params': {'review_below': 80, 'exclude_below': 50}},
        ], 'annotation': [{'rule_id': 'has_ms2', 'params': {'on_false': 'exclude'}}]},
        'fixture': {'name': 'fixture', 'statistics': [], 'annotation': [
            {'rule_id': 'maximum_qc_cv_pct', 'params': {'review_above': 21, 'exclude_above': 31}},
        ]},
    }
    return result


class CurationProfileTests(unittest.TestCase):
    def setUp(self):
        self.app = _CallbackCapture()
        register_callbacks(self.app)

    def test_choices_follow_the_package_catalog_and_drop_stale_values(self):
        self.assertIn('configure_curation_profile', self.app.callbacks)
        configure = self.app.callbacks['configure_curation_profile']
        catalog = workflow()
        args = (3, build_page_cache([catalog], 'token'), 'token')
        options, selected, disabled, style = configure(*args, 'fixture')
        self.assertEqual([option['value'] for option in options], ['strict', 'fixture'])
        self.assertEqual((selected, disabled, style), ('fixture', False, {}))
        self.assertEqual(configure(*args, 'unavailable')[1], 'strict')
        del catalog['static_input_config_json']['engine']['package']['curation_profiles']['fixture']
        _, selected, disabled, _ = configure(3, build_page_cache([catalog], 'token'), 'token', 'fixture')
        self.assertEqual((selected, disabled), ('strict', True))
        self.assertEqual(configure(3, build_page_cache([definition()], 'token'), 'token', 'fixture'),
                         ([], None, True, {'display': 'none'}))

    def test_rule_list_displays_each_domains_actual_limits(self):
        self.assertIn('configure_curation_rules', self.app.callbacks)
        configure = self.app.callbacks['configure_curation_rules']
        store = build_page_cache([workflow()], 'token')
        def show(name):
            return configure(name, 3, store, 0, 'token', None, None, None)[0]
        rendered = show('strict')
        text = ' '.join(c for c in _walk(rendered) if isinstance(c, str))
        for value in ('Statistics', 'Annotation', '80%', '50%', 'MS2', 'Annotation present'):
            self.assertIn(value, text)
        changed = ' '.join(c for c in _walk(show('fixture')) if isinstance(c, str))
        self.assertIn('21%', changed)
        self.assertIn('31%', changed)
        self.assertNotIn('80%', changed)

    def test_callback_submits_the_profile_shown_in_preview_outside_engine_parameters(self):
        form = dict.fromkeys(name for name, _, _ in LAUNCH_FORM_STATES)
        form.update(pathname='/workflows', token='token', study_id=1, assay_id=2,
                    wf_id=3, wf_params='{}', workflow_stage='full', review_stages=[],
                    metabolensr_profile='fixture', definitions_store=build_page_cache([workflow()], 'token'))
        with (patch('callbacks.workflows.dash.ctx', SimpleNamespace(
                triggered_id='wf-launch-btn', triggered=[{'value': 1}])),
              patch('services.workflow_service.call_api', return_value={'id': 42}) as api):
            result = self.app.callbacks['execute_workflow_action'](
                1, *[form[name] for name, _, _ in LAUNCH_FORM_STATES])
        self.assertIn('Launched workflow job #42', result[0])
        payload = api.call_args.kwargs['payload']
        self.assertEqual(payload.get('metabolensr_profile'), 'fixture')
        self.assertNotIn('metabolensr_profile', payload['parameters'])
        preview = self.app.callbacks['refresh_studio_preview'](
            'token', 1, 2, form['definitions_store'], 3, '{}', None,
            None, None, None, None, [], None, False, None, None, 'fixture')
        text = ''.join(c for c in _walk(preview[8]) if isinstance(c, str))
        self.assertIn('MetaboLensR profile: fixture', text)

    def test_missing_catalog_omits_profile_even_when_form_retains_a_value(self):
        with patch('services.workflow_service.call_api', return_value={'id': 42}) as api:
            workflow_service.launch_workflow(token='token', assay_id=2, workflow_id=3,
                workflow_params='{}', workflow_definition=definition(), metabolensr_profile='stale')
        self.assertNotIn('metabolensr_profile', api.call_args.kwargs['payload'])

    def test_rule_switches_reset_with_the_profile_and_keep_empty_selections(self):
        catalog = workflow()
        catalog['static_input_config_json']['engine']['package']['supports_rule_selection'] = True
        store = build_page_cache([catalog], 'token')
        configure = self.app.callbacks['configure_curation_rules']
        with patch('callbacks.workflows.dash.ctx', SimpleNamespace(triggered_id='wf-id')):
            result = configure('strict', 3, store, 0, 'token', None, None, None)
        # Read-only rendering, controls style, stats options/value, annotation options/value, context.
        self.assertEqual(result[1], {})
        self.assertEqual(result[3], ['has_annotation', 'minimum_sample_detection_pct'])
        self.assertEqual(result[5], ['has_ms2'])
        context = result[6]
        subset = workflow_service.workflow_curation_rule_selection(catalog, 'strict', context, [], ['has_ms2'])
        self.assertEqual(subset, {'statistics': []})
        self.assertIsNone(workflow_service.workflow_curation_rule_selection(
            catalog, 'strict', context, result[3], result[5]))
        with patch('callbacks.workflows.dash.ctx', SimpleNamespace(triggered_id='wf-definitions-store')):
            kept = configure('strict', 3, store, 0, 'token', context, [], ['has_ms2'])
        self.assertEqual(kept[3], [])
        with patch('callbacks.workflows.dash.ctx', SimpleNamespace(triggered_id='wf-curation-reset')):
            reset = configure('strict', 3, store, 1, 'token', context, [], [])
        self.assertEqual(reset[3], result[3])
        self.assertEqual(reset[5], result[5])
        with patch('callbacks.workflows.dash.ctx', SimpleNamespace(triggered_id='wf-metabolensr-profile')):
            changed = configure('fixture', 3, store, 1, 'token', context, [], [])
        self.assertEqual(changed[3], [])
        self.assertEqual(changed[5], ['maximum_qc_cv_pct'])
        with self.assertRaisesRegex(ValueError, 'updating'):
            workflow_service.workflow_curation_rule_selection(catalog, 'fixture', context, [], ['has_ms2'])
        catalog['id'] = 4
        with self.assertRaisesRegex(ValueError, 'updating'):
            workflow_service.workflow_curation_rule_selection(catalog, 'strict', context, [], ['has_ms2'])
        catalog['id'] = 3
        catalog['static_input_config_json']['engine']['package']['supports_rule_selection'] = False
        with self.assertRaisesRegex(ValueError, 'updating'):
            workflow_service.workflow_curation_rule_selection(catalog, 'strict', context, [], ['has_ms2'])
        self.assertIsNone(workflow_service.workflow_curation_rule_selection(catalog, 'strict', None, None, None))
        package = catalog['static_input_config_json']['engine']['package']
        package['supports_rule_selection'] = True
        for key, value in (('commit', 'new-package'), ('container_image', 'package@sha256:' + 'b' * 64)):
            original = package.get(key)
            package[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'updating'):
                workflow_service.workflow_curation_rule_selection(catalog, 'strict', context, [], ['has_ms2'])
            package[key] = original

    def test_customized_status_and_launch_match_the_visible_rule_switches(self):
        catalog = workflow()
        catalog['static_input_config_json']['engine']['package']['supports_rule_selection'] = True
        context = workflow_service.workflow_curation_context(catalog, 'strict')
        state = self.app.callbacks['show_curation_selection']([], ['has_ms2'], context)
        self.assertIn('No automatic filtering', state[0])
        self.assertEqual(state[1], '')  # an enabled domain needs no count
        self.assertEqual(state[2:], ('Customized', False))
        form = dict.fromkeys(name for name, _, _ in LAUNCH_FORM_STATES)
        form.update(pathname='/workflows', token='token', study_id=1, assay_id=2,
                    wf_id=3, wf_params='{}', workflow_stage='full', review_stages=[],
                    metabolensr_profile='strict', metabolensr_rule_context=context,
                    metabolensr_statistics_rules=[], metabolensr_annotation_rules=['has_ms2'],
                    definitions_store=build_page_cache([catalog], 'token'))
        with (patch('callbacks.workflows.dash.ctx', SimpleNamespace(
                triggered_id='wf-launch-btn', triggered=[{'value': 1}])),
              patch('services.workflow_service.call_api', return_value={'id': 42}) as api):
            result = self.app.callbacks['execute_workflow_action'](
                1, *[form[name] for name, _, _ in LAUNCH_FORM_STATES])
        self.assertIn('Launched workflow job #42', result[0])
        self.assertEqual(api.call_args.kwargs['payload']['metabolensr_enabled_rules'], {'statistics': []})
        self.assertEqual(api.call_args.kwargs['payload']['metabolensr_expected_image'],
                         catalog['static_input_config_json']['engine']['package']['container_image'])
        self.assertNotIn('enabled_rules', api.call_args.kwargs['payload']['parameters'])
        preview = self.app.callbacks['refresh_studio_preview'](
            'token', 1, 2, form['definitions_store'], 3, '{}', None,
            None, None, None, None, [], None, False, None, None,
            'strict', context, [], ['has_ms2'])
        text = ''.join(c for c in _walk(preview[8]) if isinstance(c, str))
        self.assertIn('"statistics": []', text)
        self.assertNotIn('"annotation": []', text)

    def test_older_package_catalog_stays_read_only(self):
        catalog = workflow()
        self.assertIsNone(workflow_service.workflow_curation_context(catalog, 'strict'))
        configure = self.app.callbacks['configure_curation_rules']
        result = configure('strict', 3, build_page_cache([catalog], 'token'), 0, 'token', None, [], [])
        self.assertEqual(result[1], {'display': 'none'})
        self.assertIn('MS2', ' '.join(c for c in _walk(result[0]) if isinstance(c, str)))
