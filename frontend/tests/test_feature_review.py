import unittest
from unittest.mock import patch
from types import SimpleNamespace
from tests.test_job_workspace import _CallbackCapture


class FeatureReviewTests(unittest.TestCase):
    def setUp(self):
        from callbacks.feature_review import register_callbacks
        self.app = _CallbackCapture()
        register_callbacks(self.app)

    def test_saved_revision_and_domain_changes_reuse_native_graphs(self):
        from dash import Input, Output, State, no_update
        selected = {'bundle': 'bundle:142:90', 'feature_id': '0'}
        row = {'feature_id': '0', 'label': 'Example', 'has_ms2': False,
               'override_statistics': 'include', 'override_annotation': 'exclude'}
        values = {('fr-selection', 'data'): selected,
                  ('fr-page', 'data'): {'bundle': selected['bundle'], 'revision': 1, 'items': [row]},
                  ('fr-domain', 'value'): 'statistics',
                  ('token-store', 'data'): 'tok', ('assay-select-store', 'data'): 18}
        requests = []

        def evidence(method, path, **kwargs):
            requests.append(path)
            return {'spot': {'peaks': [{'sample_name': 'QC', 'points': [
                {'x': 1.0, 'intensity': 10}, {'x': 2.0, 'intensity': 20}]}]}, 'ms2': None}

        def render():
            deps = self.app.dependencies['review_evidence']
            result = self.app.callbacks['review_evidence'](*[
                values.get((d.component_id, d.component_property)) for d in deps
                if isinstance(d, (Input, State))])
            outputs = [d for d in deps if isinstance(d, Output)]
            return {(d.component_id, d.component_property): value for d, value in zip(outputs, result)}

        with patch('services.workflow_service.call_api', side_effect=evidence):
            values.update(render())
            values[('fr-page', 'data')]['revision'] = 2
            values[('fr-domain', 'value')] = 'annotation'
            updated = render()
        self.assertEqual(len(requests), 1, 'Saving decisions must not reload unchanged native evidence')
        self.assertIs(updated[('fr-native-evidence', 'children')], no_update)
        self.assertEqual(updated[('fr-value', 'value')], 'exclude')
        self.assertEqual(updated[('fr-editor', 'data')], {**selected, 'domain': 'annotation', 'revision': 2})

    def test_native_graph_identity_includes_auth_assay_bundle_and_feature(self):
        from services.page_cache import build_page_cache
        old = {'bundle': 'bundle:142:90', 'feature_id': '0'}
        key = build_page_cache(old, 'tok', assay_id=18)
        for selected, token, assay in [
                ({**old, 'feature_id': 'engine/id:2'}, 'tok', 18),
                ({**old, 'bundle': 'bundle:143:91'}, 'tok', 18),
                (old, 'new-token', 18), (old, 'tok', 19)]:
            with self.subTest(selected=selected, assay=assay, changed_auth=token != 'tok'):
                page = {'bundle': selected['bundle'], 'revision': 3, 'items': [
                    {'feature_id': selected['feature_id'], 'label': 'Example', 'has_ms2': None}]}
                with patch('services.workflow_service.call_api', return_value={'spot': None, 'ms2': None}):
                    result = self.app.callbacks['review_evidence'](selected,page,'statistics',token,assay,key)
                self.assertIn('Chromatogram unavailable', str(result[5]))
                self.assertEqual(result[6]['data'], selected)
                self.assertEqual(result[3], {**selected, 'domain': 'statistics', 'revision': 3})

    def test_explicit_reload_refreshes_previously_missing_native_evidence_once(self):
        from dash import Input, State, no_update
        selected = {'bundle': 'bundle:142:90', 'feature_id': '0'}
        values = {('fr-selection', 'data'): selected,
                  ('fr-page', 'data'): {'bundle': selected['bundle'], 'revision': 1, 'items': [
                      {'feature_id': '0', 'label': 'Example', 'has_ms2': None}]},
                  ('fr-domain', 'value'): 'statistics', ('fr-reload', 'n_clicks'): 0,
                  ('token-store', 'data'): 'tok', ('assay-select-store', 'data'): 18}
        requests = []

        def evidence(method, path, **kwargs):
            requests.append(path)
            return {'spot': None, 'ms2': None} if len(requests) == 1 else {
                'spot': {'peaks': [{'sample_name': 'QC', 'points': [
                    {'x': 1.0, 'intensity': 10}, {'x': 2.0, 'intensity': 20}]}]}, 'ms2': None}

        def render():
            return self.app.callbacks['review_evidence'](*[
                values.get((d.component_id, d.component_property))
                for d in self.app.dependencies['review_evidence'] if isinstance(d, (Input, State))])

        with patch('services.workflow_service.call_api', side_effect=evidence):
            first = render()
            self.assertIn('Chromatogram unavailable', str(first[5]))
            values[('fr-native-key', 'data')] = first[6]
            values[('fr-reload', 'n_clicks')] = 1
            refreshed = render()
            self.assertEqual(len(requests), 2, 'An explicit reload must retry successful missing evidence')
            self.assertEqual(type(refreshed[5][0]).__name__, 'Graph')
            values[('fr-native-key', 'data')] = refreshed[6]
            self.assertIs(render()[5], no_update)
            self.assertEqual(len(requests), 2)

    def test_native_error_is_retried_and_stale_bundle_clears_graph_and_editor(self):
        from services.api_client import APIError
        selected = {'bundle': 'bundle:142:90', 'feature_id': '0'}
        page = {'bundle': selected['bundle'], 'revision': 3, 'items': [
            {'feature_id': '0', 'label': 'Example', 'has_ms2': None}]}
        with patch('services.workflow_service.call_api', side_effect=APIError('Unavailable')):
            failed = self.app.callbacks['review_evidence'](selected,page,'statistics','tok',18)
        self.assertIn('Evidence unavailable', str(failed[5]))
        self.assertIsNone(failed[6])
        with patch('services.workflow_service.call_api', return_value={'spot': None, 'ms2': None}):
            retry = self.app.callbacks['review_evidence'](selected,page,'statistics','tok',18,failed[6])
        self.assertEqual(retry[6]['data'], selected)
        stale = self.app.callbacks['review_evidence'](
            {**selected, 'bundle': 'bundle:143:91'},page,'statistics','tok',18,retry[6])
        self.assertEqual(stale[5], [])
        self.assertIsNone(stale[6])
        self.assertIsNone(stale[3])

    def test_default_queue_and_pagination_are_server_side(self):
        with patch('callbacks.feature_review.workflow_service.get_feature_review', return_value={'items': [], 'revision': 4}) as get:
            result, _ = self.app.callbacks['load_review_page']('bundle:142:90','include','statistics','lipid','', '',2,0,None,'tok',18)
        self.assertEqual(get.call_args.kwargs['queue'], 'include')
        self.assertEqual(get.call_args.kwargs['page'], 3)
        self.assertEqual(result['revision'], 4)

    def test_page_control_follows_filtered_server_page(self):
        with patch('callbacks.feature_review.workflow_service.get_feature_review', return_value={'items': [], 'revision': 4, 'page': 1}):
            _, page = self.app.callbacks['load_review_page']('bundle:142:90','include','statistics','lipid','', '',7,0,None,'tok',18)
        self.assertEqual(page, 0)

    def test_evidence_includes_adduct_and_explicit_unknown_ms2(self):
        selected={'bundle':'bundle:142:90','feature_id':'0'}
        data={'bundle':selected['bundle'],'revision':1,'items':[{'feature_id':'0','label':'Example','adduct_type':'[M+H]+','has_ms2':None}]}
        with patch('callbacks.feature_review.workflow_service.get_package_feature_trace',return_value={}):
            children,*_=self.app.callbacks['review_evidence'](selected,data,'statistics','tok',18)
        text=str(children)
        self.assertIn('[M+H]+',text)
        self.assertIn('MS2: unknown',text)

    def test_selection_survives_save_and_navigation(self):
        data = {'bundle':'bundle:142:90','items':[{'feature_id':'0'},{'feature_id':'5'}]}
        selected = {'bundle':'bundle:142:90','feature_id':'5'}
        with patch('callbacks.feature_review.dash.ctx',SimpleNamespace(triggered_id='fr-page')):
            result, _ = self.app.callbacks['select_review_feature'](None,0,0,data,selected,0)
        self.assertEqual(result['feature_id'], '5')

    def test_annotation_copy_requires_explicit_confirmation(self):
        page = {'bundle':'bundle:142:90','revision':7,'can_edit':True,'items':[
            {'feature_id':'0','annotation_override_source':'annotation','override_annotation':'exclude'}]}
        with patch('callbacks.feature_review.dash.ctx',SimpleNamespace(triggered_id='fr-save')):
            with patch('callbacks.feature_review.workflow_service.patch_feature_review') as save:
                result, confirm = self.app.callbacks['save_review'](1,0,page,{'bundle':'bundle:142:90','feature_id':'0'},'statistics','include',True,'','tok',18,{'bundle':'bundle:142:90','feature_id':'0','domain':'statistics','revision':7})
        self.assertTrue(confirm)
        save.assert_not_called()

    def test_save_passes_displayed_revision_and_preserves_conflict(self):
        page={'bundle':'bundle:142:90','revision':7,'can_edit':True,'items':[{'feature_id':'0'}]}
        from services.api_client import APIError
        with patch('callbacks.feature_review.dash.ctx',SimpleNamespace(triggered_id='fr-save')):
            with patch('callbacks.feature_review.workflow_service.patch_feature_review', side_effect=APIError('409 conflict')) as save:
                result, _ = self.app.callbacks['save_review'](1,0,page,{'bundle':'bundle:142:90','feature_id':'0'},'statistics','exclude',False,'note','tok',18,{'bundle':'bundle:142:90','feature_id':'0','domain':'statistics','revision':7})
        self.assertEqual(save.call_args.kwargs['expected_revision'],7)
        self.assertFalse(result['ok'])
        self.assertIn('409',result['message'])

    def test_save_rejects_editor_from_another_feature_or_revision(self):
        page={'bundle':'bundle:142:90','revision':7,'can_edit':True,'items':[{'feature_id':'5'}]}
        editor={'bundle':'bundle:142:90','revision':6,'feature_id':'0','domain':'statistics'}
        with patch('callbacks.feature_review.workflow_service.patch_feature_review') as save:
            result, _ = self.app.callbacks['save_review'](1,0,page,{'bundle':'bundle:142:90','feature_id':'5'},'statistics','exclude',False,'old','tok',18,editor)
        save.assert_not_called()
        self.assertFalse(result['ok'])

    def test_initial_mount_does_not_erase_persisted_selection(self):
        import dash
        selected={'bundle':'bundle:142:90','feature_id':'5'}
        with patch('callbacks.feature_review.dash.ctx',SimpleNamespace(triggered_id='fr-page')):
            result,_=self.app.callbacks['select_review_feature'](None,0,0,None,selected,0)
        self.assertIs(result,dash.no_update)

    def test_failed_finalization_retries_frozen_revision_without_refreezing(self):
        page={'bundle':'bundle:142:90','revision':8,'frozen_revision':4,'can_retry':True,'can_continue':False}
        with patch('callbacks.feature_review.workflow_service.continue_workflow_job',return_value={'id':142}) as resume:
            result,status=self.app.callbacks['continue_review'](1,page,'tok',18)
        resume.assert_called_once_with('tok',18,142)
        self.assertTrue(result['ok'])
        self.assertIn('4',status['message'])
