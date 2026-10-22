import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ExportProfilesTests(unittest.TestCase):
    def test_export_checks_identity_and_detects_stale_catalog_without_rewriting(self):
        from scripts.export_metabolensr_profiles import export_profiles
        descriptor = {'package': {'version': '1.0', 'commit': 'a' * 40,
            'container_image': 'package@sha256:' + 'b' * 64, 'config': {'profile': 'strict'}}}
        catalog = {'strict': {'name': 'strict', 'statistics': [], 'annotation': []}}
        image = json.dumps([{'Config': {'Labels': {'org.opencontainers.image.revision': 'a' * 40}}}])
        exported = json.dumps({'version': '1.0', 'profiles': catalog, 'supports_rule_selection': True})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'engine.json'
            path.write_text(json.dumps(descriptor))
            with patch('subprocess.check_output', side_effect=[image, exported]):
                export_profiles(path)
            self.assertEqual(json.loads(path.read_text())['package']['curation_profiles'], catalog)
            self.assertTrue(json.loads(path.read_text())['package']['supports_rule_selection'])
            before = path.read_bytes()
            with patch('subprocess.check_output', side_effect=[image, exported]):
                export_profiles(path, check=True)
            for response, pattern in [
                (json.dumps({'version': '2.0', 'profiles': catalog}), 'version'),
                (json.dumps({'version': '1.0', 'profiles': {}}), 'profile'),
                (json.dumps({'version': '1.0', 'profiles': {**catalog, 'other': catalog['strict']}}), 'catalog'),
            ]:
                with self.subTest(pattern=pattern), patch('subprocess.check_output', side_effect=[image, response]):
                    with self.assertRaisesRegex(ValueError, pattern):
                        export_profiles(path, check=True)
                self.assertEqual(path.read_bytes(), before)
            with patch('subprocess.check_output', side_effect=[image, json.dumps({
                    'version': '1.0', 'profiles': catalog, 'supports_rule_selection': False})]):
                with self.assertRaisesRegex(ValueError, 'capability'):
                    export_profiles(path, check=True)
            wrong_image = json.dumps([{'Config': {'Labels': {'org.opencontainers.image.revision': 'c' * 40}}}])
            with patch('subprocess.check_output', return_value=wrong_image), self.assertRaisesRegex(ValueError, 'revision'):
                export_profiles(path)
            self.assertEqual(path.read_bytes(), before)
