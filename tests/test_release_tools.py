import importlib.util
import io
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigureLocalTests(unittest.TestCase):
    def test_config_is_private_random_and_never_overwritten(self):
        configure = load('configure_local')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = configure.configure(root, 'operator@example.org')
            before = path.read_bytes()
            values = dict(line.split('=', 1) for line in before.decode().splitlines())
            self.assertEqual(values['BOOTSTRAP_ADMIN_EMAIL'], 'operator@example.org')
            self.assertEqual(len({values[key] for key in ('APP_SECRET_KEY', 'POSTGRES_PASSWORD', 'BOOTSTRAP_ADMIN_PASSWORD')}), 3)
            self.assertGreaterEqual(len(values['APP_SECRET_KEY']), 32)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                configure.configure(root, 'other@example.org')
            self.assertEqual(path.read_bytes(), before)

    def test_rejects_env_injection_without_creating_file(self):
        configure = load('configure_local')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                configure.configure(Path(directory), 'a@example.org\nAUTO_SEED=true')
            self.assertFalse((Path(directory) / '.env').exists())


class ReleaseScanTests(unittest.TestCase):
    def test_rejects_environment_variants_but_allows_example_template(self):
        scan = load('check_release')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env.example').write_text('APP_SECRET_KEY=\n')
            self.assertEqual(scan.audit(root), [])
            for name in ('.env.local', '.env.production', 'backend.env'):
                (root / name).write_text('APP_SECRET_KEY=synthetic-test-value\n')
            findings = scan.audit(root)
            self.assertEqual(len(findings), 3)

    def test_rejects_credentials_in_plain_files_and_nested_archives(self):
        scan = load('check_release')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.rtoken').write_text('synthetic-credential')
            payload = ('-----BEGIN ' + 'RSA PRIVATE KEY-----\nnot-a-real-key').encode()
            with tarfile.open(root / 'bundle.tar.gz', 'w:gz') as archive:
                info = tarfile.TarInfo('nested/key.txt')
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            findings = scan.audit(root)
            self.assertTrue(any('.rtoken' in finding for finding in findings))
            self.assertTrue(any('nested/key.txt' in finding and 'private key' in finding for finding in findings))
            self.assertTrue(all('synthetic-credential' not in finding for finding in findings))

    def test_rejects_symlink_and_real_measurements_but_accepts_source(self):
        scan = load('check_release')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source.py').write_text('print("hello")\n')
            (root / 'Dockerfile.sirius').write_text('FROM debian:bookworm-slim\n')
            self.assertEqual(scan.audit(root), [])
            (root / 'linked').symlink_to(root / 'source.py')
            (root / 'measurement.mzML').write_text('<mzML/>')
            findings = scan.audit(root)
            self.assertTrue(any('symlink' in finding for finding in findings))
            self.assertTrue(any('measurement.mzML' in finding for finding in findings))


if __name__ == '__main__':
    unittest.main()
