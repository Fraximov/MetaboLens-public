import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from callbacks.workflows import register_callbacks
from callbacks.workflows.launch import LAUNCH_FORM_STATES
from pages.workflows import workflows_page_layout
from services import workflow_service
from services.page_cache import build_page_cache
from test_workflow_callbacks import _CallbackCapture
from test_workflows_page import _walk


def definition(transitions=None, default='full'):
    return {
        'id': 3, 'name': 'MetaboLensR',
        'static_input_config_json': {'engine': {
            # Deliberately reversed: JSON object order does not define stage order.
            'review_transitions': transitions if transitions is not None else {
                'qc_preview': 'full', 'is_qc_review': 'qc_preview'},
            'execution_profiles': ['local_docker'], 'required_inputs': {},
            'package': {'config': {'profile': 'strict'}},
        }},
        'parameter_schema_json': {'properties': {
            'workflow_stage': {'enum': ['full', 'qc_preview', 'is_qc_review'], 'default': default},
        }},
    }


class ReviewPauseTests(unittest.TestCase):
    def setUp(self):
        self.app = _CallbackCapture()
        register_callbacks(self.app)

    def configure(self, selected, workflow=None):
        workflow = workflow or definition()
        return self.app.callbacks['configure_review_pauses'](
            3, build_page_cache([workflow], 'token'), 'token', selected)

    def test_switches_follow_transition_order_and_start_automatic(self):
        options, selected, switch_style = self.configure(None)
        self.assertEqual(options, [
            {'value': 'is_qc_review', 'label': 'Pause after IS-QC / RT review'},
            {'value': 'qc_preview', 'label': 'Pause for feature review'},
        ])
        self.assertEqual(selected, [])
        self.assertEqual(switch_style, {})
        switch = next(c for c in _walk(workflows_page_layout())
                      if getattr(c, 'id', None) == 'wf-review-stages')
        self.assertTrue(switch.switch)

    def test_defaults_follow_chain_but_explicit_selection_is_preserved(self):
        self.assertEqual(self.configure(None, definition(default='is_qc_review'))[1],
                         ['is_qc_review', 'qc_preview'])
        self.assertEqual(self.configure(None, definition(default='qc_preview'))[1], ['qc_preview'])
        for selected in ([], ['is_qc_review'], ['qc_preview'], ['qc_preview', 'is_qc_review']):
            with self.subTest(selected=selected):
                expected = [stage for stage in ['is_qc_review', 'qc_preview'] if stage in selected]
                self.assertEqual(self.configure(selected, definition(default='is_qc_review'))[1], expected)

    def test_switch_to_masscube_removes_unsupported_pause_and_no_engine_hides_pauses(self):
        result = self.configure(['is_qc_review', 'qc_preview'], definition({'qc_preview': 'full'}))
        self.assertEqual(result[0], [{'value': 'qc_preview', 'label': 'Pause for feature review'}])
        self.assertEqual(result[1], ['qc_preview'])
        result = self.configure(['is_qc_review', 'qc_preview'], {'id': 3})
        self.assertEqual(result, ([], None, {'display': 'none'}))

    def test_preview_and_launch_agree_for_every_pause_combination(self):
        for stages, first in [([], 'full'), (['is_qc_review'], 'is_qc_review'),
                              (['qc_preview'], 'qc_preview'),
                              (['qc_preview', 'is_qc_review'], 'is_qc_review')]:
            with self.subTest(stages=stages), patch('services.workflow_service.call_api', return_value={'id': 42}) as api:
                workflow = definition()
                workflow_service.launch_workflow(
                    token='token', assay_id=2, workflow_id=3, workflow_definition=workflow,
                    workflow_params='{"workflow_stage":"qc_preview"}', review_stages=stages)
                payload = api.call_args.kwargs['payload']
                self.assertEqual(payload['parameters'], {'workflow_stage': first})
                ordered = [stage for stage in ['is_qc_review', 'qc_preview'] if stage in stages]
                self.assertEqual(payload['review_stages'], ordered)
                preview = workflow_service.build_nextflow_command_preview(
                    [workflow], 3, '{"workflow_stage":"qc_preview"}',
                    None, None, None, None, None, review_stages=stages)
                self.assertIn(f'"workflow_stage": "{first}"', preview)
                self.assertIn(f'# Review pauses: {json.dumps(ordered)}', preview)

    def test_omitted_pause_argument_sends_the_schema_default_stage_and_no_pauses(self):
        with patch('services.workflow_service.call_api', return_value={'id': 42}) as api:
            workflow_service.launch_workflow(
                token='token', assay_id=2, workflow_id=3, workflow_definition=definition(), workflow_params='{}')
        self.assertNotIn('review_stages', api.call_args.kwargs['payload'])
        self.assertEqual(api.call_args.kwargs['payload']['parameters']['workflow_stage'], 'full')

    def test_masscube_payload_keeps_only_supported_selected_pauses(self):
        workflow = definition({'qc_preview': 'full'})
        for selected, expected in [(['is_qc_review', 'qc_preview'], ['qc_preview']),
                                   (['is_qc_review'], []), ([], [])]:
            with self.subTest(selected=selected), patch('services.workflow_service.call_api', return_value={'id': 42}) as api:
                workflow_service.launch_workflow(
                    token='token', assay_id=2, workflow_id=3, workflow_definition=workflow,
                    workflow_params='{}', review_stages=selected)
                payload = api.call_args.kwargs['payload']
                self.assertEqual(payload['review_stages'], expected)
                self.assertEqual(payload['parameters']['workflow_stage'], 'qc_preview' if expected else 'full')

    def test_callbacks_submit_and_preview_the_same_switches(self):
        workflow = definition()
        cache = build_page_cache([workflow], 'token')
        form = dict.fromkeys(name for name, _, _ in LAUNCH_FORM_STATES)
        form.update(pathname='/workflows', token='token', study_id=1, assay_id=2,
                    wf_id=3, wf_params='{}', review_stages=['is_qc_review'], definitions_store=cache)
        with (patch('callbacks.workflows.dash.ctx', SimpleNamespace(
                triggered_id='wf-launch-btn',
                triggered=[{'prop_id': 'wf-launch-btn.n_clicks', 'value': 1}])),
              patch('services.workflow_service.call_api', return_value={'id': 42}) as api):
            result = self.app.callbacks['execute_workflow_action'](
                1, *[form[name] for name, _, _ in LAUNCH_FORM_STATES])
        self.assertIn('Launched workflow job #42', result[0])
        self.assertEqual(api.call_args.kwargs['payload'], {
            'parameters': {'workflow_stage': 'is_qc_review'}, 'review_stages': ['is_qc_review']})
        preview = self.app.callbacks['refresh_studio_preview'](
            'token', 1, 2, cache, 3, '{}', None, None, None, None, None,
            ['is_qc_review'], None, False, None, None)
        text = ''.join(value for value in _walk(preview[8]) if isinstance(value, str))
        self.assertIn('"workflow_stage": "is_qc_review"', text)
        self.assertIn('# Review pauses: ["is_qc_review"]', text)

    def test_future_descriptor_stages_need_no_engine_specific_switch_code(self):
        workflow = definition({'finish_review': 'full', 'start_review': 'middle_review',
                               'middle_review': 'finish_review'}, default='middle_review')
        result = self.configure(None, workflow)
        self.assertEqual([option['value'] for option in result[0]],
                         ['start_review', 'middle_review', 'finish_review'])
        self.assertEqual(result[1], ['middle_review', 'finish_review'])

    def test_future_engine_uses_its_terminal_stage_for_automatic_execution(self):
        workflow = definition({'qc_preview': 'complete'}, default='qc_preview')
        workflow['parameter_schema_json']['properties']['workflow_stage']['enum'] = ['qc_preview', 'complete']
        for selected, expected in [([], 'complete'), (['qc_preview'], 'qc_preview')]:
            with self.subTest(selected=selected), patch('services.workflow_service.call_api', return_value={'id': 42}) as api:
                workflow_service.launch_workflow(
                    token='token', assay_id=2, workflow_id=3, workflow_definition=workflow,
                    workflow_params='{}', review_stages=selected)
                self.assertEqual(api.call_args.kwargs['payload'], {
                    'parameters': {'workflow_stage': expected}, 'review_stages': selected})
                preview = workflow_service.build_nextflow_command_preview(
                    [workflow], 3, '{}', None, None, None, None, None, review_stages=selected)
                self.assertIn(f'"workflow_stage": "{expected}"', preview)
                self.assertIn(f'# Review pauses: {json.dumps(selected)}', preview)
