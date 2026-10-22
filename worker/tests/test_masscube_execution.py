import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from services.engines import ENGINE_ADAPTERS, EngineContext


class MassCubeExecutionTests(unittest.TestCase):
    def test_edited_csv_is_frozen_as_an_artifact_without_a_mutable_cli_path(self):
        with TemporaryDirectory() as temp:
            text = 'parameter,value\nms1_abs_int_tol,42000\n'
            snapshot = {'content': text, 'source_path': 'templates/masscube/preset.csv',
                        'checksum_sha256': hashlib.sha256(text.encode()).hexdigest()}
            context = EngineContext(descriptor={}, parameters={
                'masscube_config_path': snapshot['source_path'],
                'masscube_parameters': {'ms1_abs_int_tol': 42000}},
                manifest={'masscube_template': snapshot}, workspace=SimpleNamespace(contract_dir=Path(temp)),
                staged_files=[], support_files={})
            launch = ENGINE_ADAPTERS['masscube-v1'](context)
            frozen = Path(temp) / 'masscube_template_source.csv'
            self.assertEqual(frozen.read_text(), text)
            self.assertIn((frozen, 'masscube_template_source'), launch.artifacts)
            self.assertEqual(set(context.parameters) - set(launch.consumed_parameters), set())
            self.assertEqual(json.loads(Path(launch.parameters['masscube_config']).read_text()),
                             {'ms1_abs_int_tol': 42000})
            snapshot['content'] = text.replace('42000', '90000')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                ENGINE_ADAPTERS['masscube-v1'](context)

    def test_adapter_freezes_only_science_and_declares_native_outputs(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for stage in ('qc_preview', 'full'):
                params = {'workflow_stage': stage, 'analysis_mode': 'qc_only',
                          'masscube_parameters': {'mz_tol_ms1': .01}, 'ms2_library_path': 'library.msp'}
                context = EngineContext(descriptor={}, parameters=params, manifest={},
                    workspace=SimpleNamespace(contract_dir=root), staged_files=[],
                    support_files={'ms2_library_path': root / 'library.msp'})
                launch = ENGINE_ADAPTERS['masscube-v1'](context)
                config = Path(launch.parameters['masscube_config'])
                self.assertEqual(json.loads(config.read_text()), {'mz_tol_ms1': .01})
                forwarded = {key: value for key, value in params.items() if key not in launch.consumed_parameters}
                self.assertEqual(forwarded, {'workflow_stage': stage, 'analysis_mode': 'qc_only'})
                self.assertEqual(launch.parameters['masscube_ms2_library'], str(root / 'library.msp'))
                self.assertFalse(launch.legacy_fields)
                self.assertNotIn('ref', launch.parameters)
                self.assertIn(('masscube/evidence.index.json', 'masscube_evidence_index'), launch.native_outputs)
                self.assertEqual(launch.resource_groups['MASSCUBE_PROCESS'], 'peak')
                self.assertEqual(launch.artifacts, [(config, 'masscube_config')])

    def test_optional_library_is_not_manufactured(self):
        with TemporaryDirectory() as temp:
            context = EngineContext(descriptor={}, parameters={}, manifest={},
                workspace=SimpleNamespace(contract_dir=Path(temp)), staged_files=[], support_files={})
            launch = ENGINE_ADAPTERS['masscube-v1'](context)
            self.assertNotIn('masscube_ms2_library', launch.parameters)
            self.assertEqual(json.loads(Path(launch.parameters['masscube_config']).read_text()), {})
