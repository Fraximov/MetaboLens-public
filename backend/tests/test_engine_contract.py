import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.core.engine_contract import snapshot_engine, validate_engine_parameters, validate_engine_acquisitions


def fixture_descriptor():
    return {
        'schema_version': 1, 'engine_id': 'fixture_engine', 'engine_version': '1',
        'adapter_id': 'fixture-v1', 'importer_id': 'fixture_engine', 'entrypoint': 'fixture.nf',
        'required_inputs': {}, 'capabilities': [], 'execution_profiles': ['local_docker'],
        'source_paths': ['fixture.nf'], 'container_images': {},
        'package': {'version': '0.0.0.9001', 'commit': '1' * 40,
                    'container_image': 'package@sha256:' + '2' * 64,
                    'config': {'schema_version': '1.0', 'profile': 'strict', 'source_tool': 'fixture_engine'}},
    }


class EngineContractTests(unittest.TestCase):
    def test_rule_subsets_are_validated_and_frozen_without_changing_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'fixture.nf').write_text('// fixture')
            descriptor = fixture_descriptor()
            package = descriptor['package']
            package['supports_rule_selection'] = True
            package['curation_profiles'] = {'strict': {'name': 'strict',
                'statistics': [{'rule_id': rule, 'params': {'on_false': 'exclude'}}
                               for rule in ('has_annotation', 'has_ms2')],
                'annotation': [{'rule_id': 'has_ms2', 'params': {'on_false': 'exclude'}}]}}
            workflow = SimpleNamespace(static_input_config_json={'engine': descriptor},
                                       parameter_schema_json={'type': 'object'})
            requested = {'statistics': [], 'annotation': ['has_ms2']}
            frozen = snapshot_engine(workflow, {}, root, metabolensr_enabled_rules=requested)
            self.assertEqual(frozen['descriptor']['package']['config']['enabled_rules'], requested)
            self.assertNotIn('enabled_rules', package['config'])
            self.assertNotIn('enabled_rules', frozen['parameters'])
            requested['annotation'].clear()
            self.assertEqual(frozen['descriptor']['package']['config']['enabled_rules']['annotation'], ['has_ms2'])
            self.assertNotIn('enabled_rules', snapshot_engine(workflow, {}, root)['descriptor']['package']['config'])
            with self.assertRaisesRegex(ValueError, 'changed.*Refresh'):
                snapshot_engine(workflow, {}, root, metabolensr_expected_image='package@sha256:' + '9' * 64)
            self.assertEqual(snapshot_engine(workflow, {}, root,
                metabolensr_expected_image=package['container_image'])['descriptor']['package']['container_image'],
                package['container_image'])
            ordered = snapshot_engine(workflow, {}, root,
                metabolensr_enabled_rules={'statistics': ['has_ms2', 'has_annotation']})
            self.assertEqual(ordered['descriptor']['package']['config']['enabled_rules'],
                             {'statistics': ['has_annotation', 'has_ms2']})
            package['config']['enabled_rules'] = {'statistics': []}
            self.assertNotIn('enabled_rules', snapshot_engine(workflow, {}, root)['descriptor']['package']['config'])
            self.assertEqual(package['config'].pop('enabled_rules'), {'statistics': []})
            for invalid in ([], {'unknown': []}, {'statistics': None}, {'statistics': 'has_ms2'},
                            {'statistics': [1]}, {'statistics': ['has_ms2', 'has_ms2']},
                            {'annotation': ['has_annotation']}, {'statistics': ['unknown']}):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'enabled_rules'):
                    snapshot_engine(workflow, {}, root, metabolensr_enabled_rules=invalid)
            package.pop('supports_rule_selection')
            with self.assertRaisesRegex(ValueError, 'rule selection'):
                snapshot_engine(workflow, {}, root, metabolensr_enabled_rules={})

    def test_api_rule_selection_rejects_explicit_null_and_invalid_types(self):
        from app.schemas.workflow import WorkflowLaunch
        self.assertIsNone(WorkflowLaunch().metabolensr_enabled_rules)
        self.assertEqual(WorkflowLaunch(metabolensr_enabled_rules={'statistics': []}).metabolensr_enabled_rules,
                         {'statistics': []})
        for selection in (None, [], {'statistics': None}, {'statistics': [1]}, {'statistics': 'has_ms2'}):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                WorkflowLaunch(metabolensr_enabled_rules=selection)

    def test_selected_package_profile_is_validated_and_frozen_separately(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'fixture.nf').write_text('// fixture')
            descriptor = fixture_descriptor()
            descriptor['package']['curation_profiles'] = {
                name: {'name': name, 'statistics': [], 'annotation': []}
                for name in ('strict', 'fixture')}
            workflow = SimpleNamespace(static_input_config_json={'engine': descriptor},
                                       parameter_schema_json={'type': 'object'})
            frozen = snapshot_engine(workflow, {}, root, metabolensr_profile='fixture')
            self.assertEqual(frozen['descriptor']['package']['config']['profile'], 'fixture')
            self.assertEqual(descriptor['package']['config']['profile'], 'strict')
            self.assertNotIn('metabolensr_profile', frozen['parameters'])
            descriptor['package']['curation_profiles']['fixture']['statistics'].append(
                {'rule_id': 'has_ms2', 'params': {'on_false': 'exclude'}})
            self.assertEqual(frozen['descriptor']['package']['curation_profiles']['fixture']['statistics'], [])
            with self.assertRaisesRegex(ValueError, 'profile'):
                snapshot_engine(workflow, {}, root, metabolensr_profile='unavailable')
            descriptor['package'].pop('curation_profiles')
            self.assertEqual(snapshot_engine(workflow, {}, root)['descriptor']['package']['config']['profile'], 'strict')
            with self.assertRaisesRegex(ValueError, 'profile'):
                snapshot_engine(workflow, {}, root, metabolensr_profile='fixture')

    def test_engine_acquisition_limits_are_data_driven_and_frozen(self):
        descriptor = {**fixture_descriptor(), 'evidence_adapter_id': 'fixture-indexed-v1',
                      'input_extensions': ['.mzml'], 'minimum_acquisitions': 2}
        files = [SimpleNamespace(original_filename='one.mzML'), SimpleNamespace(original_filename='two.MZML')]
        validate_engine_acquisitions(descriptor, files)
        with self.assertRaisesRegex(ValueError, 'at least 2'):
            validate_engine_acquisitions(descriptor, files[:1])
        with self.assertRaisesRegex(ValueError, 'unsupported.*three.abf'):
            validate_engine_acquisitions(descriptor, files + [SimpleNamespace(original_filename='three.abf')])
        validate_engine_acquisitions(fixture_descriptor(), files[:1])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'fixture.nf').write_text('// fixture')
            workflow = SimpleNamespace(static_input_config_json={'engine': descriptor}, parameter_schema_json={'type': 'object'})
            frozen = snapshot_engine(workflow, {}, root)
            self.assertEqual(frozen['descriptor']['evidence_adapter_id'], 'fixture-indexed-v1')
            self.assertEqual(frozen['descriptor']['input_extensions'], ['.mzml'])

    def test_snapshot_freezes_descriptor_parameters_and_source_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'fixture.nf').write_text('// original')
            descriptor = fixture_descriptor()
            schema = {'type': 'object', 'properties': {'threshold': {'type': 'number', 'minimum': 0, 'default': 2}}, 'additionalProperties': False}
            workflow = SimpleNamespace(static_input_config_json={'engine': descriptor}, parameter_schema_json=schema)
            frozen = snapshot_engine(workflow, {}, root)
            descriptor['adapter_id'] = 'fixture-v2'
            schema['properties']['threshold']['default'] = 9
            self.assertEqual(frozen['descriptor']['adapter_id'], 'fixture-v1')
            self.assertEqual(frozen['parameters'], {'threshold': 2})
            self.assertEqual(len(frozen['source_sha256']['fixture.nf']), 64)
            self.assertEqual(frozen['parameter_schema']['properties']['threshold']['default'], 2)

    def test_parameter_validation_rejects_unknown_and_wrong_values(self):
        schema = {'type': 'object', 'properties': {'threshold': {'type': 'number', 'minimum': 0}}, 'additionalProperties': False}
        for params in ({'threshold': -1}, {'threshold': 'high'}, {'engine_id': 'other'}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                validate_engine_parameters(schema, params)

    def test_snapshot_rejects_mutable_images_and_escaping_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'fixture.nf').write_text('// fixture')
            for damage in ('image', 'path', 'entrypoint'):
                descriptor = fixture_descriptor()
                if damage == 'image': descriptor['package']['container_image'] = 'package:latest'
                if damage == 'path': descriptor['source_paths'] = ['../outside.nf']
                if damage == 'entrypoint': descriptor['entrypoint'] = 'other.nf'
                workflow = SimpleNamespace(static_input_config_json={'engine': descriptor}, parameter_schema_json={'type': 'object'})
                with self.subTest(damage=damage), self.assertRaises(ValueError):
                    snapshot_engine(workflow, {}, root)

    def test_snapshot_stores_support_paths_relative_to_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'fixture.nf').write_text('// fixture')
            (root / 'support').mkdir()
            (root / 'support' / 'template.txt').write_text('template')
            descriptor = fixture_descriptor()
            descriptor['support_files'] = ['template']
            workflow = SimpleNamespace(
                static_input_config_json={'engine': descriptor, 'template': 'support/template.txt'},
                parameter_schema_json={'type': 'object'},
            )
            frozen = snapshot_engine(workflow, {}, root)
            self.assertEqual(frozen['support_files']['template']['path'], 'support/template.txt')


if __name__ == '__main__':
    unittest.main()
