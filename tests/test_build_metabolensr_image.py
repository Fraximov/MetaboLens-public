import hashlib
import importlib.util
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_metabolensr_image.py"


class PackageArchiveTests(unittest.TestCase):
    def test_vendored_archive_contains_only_verified_source(self):
        from scripts import build_metabolensr_image as module
        self.assertTrue(hasattr(module, "export_vendored_package"), "Verified vendored package export is missing")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "package"
            source.mkdir()
            description = b"Package: MetaboLensR\nVersion: 0.0.0.9003\n"
            (source / "DESCRIPTION").write_bytes(description)
            (source / "private-data.txt").write_text("excluded")
            metadata = {"commit": "a" * 40, "version": "0.0.0.9003", "source": "fixture",
                        "files_sha256": {"DESCRIPTION": hashlib.sha256(description).hexdigest()}}
            (source / "SOURCE.json").write_text(json.dumps(metadata))
            first, second = root / "first.tar.gz", root / "second.tar.gz"
            record = module.export_vendored_package(source, first)
            module.export_vendored_package(source, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(record["package_commit"], "a" * 40)
            with tarfile.open(first) as archive:
                self.assertEqual(archive.getnames(), ["MetaboLensR/DESCRIPTION"])
            (source / "DESCRIPTION").write_text("changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                module.export_vendored_package(source, second)
            metadata["files_sha256"]["../private-data.txt"] = "b" * 64
            (source / "SOURCE.json").write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                module.export_vendored_package(source, second)

    def test_archive_uses_commit_not_working_tree_and_records_checksum(self):
        self.assertTrue(SCRIPT.is_file(), "Pinned container build helper is missing")
        spec = importlib.util.spec_from_file_location("build_metabolensr_image", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "package"; repo.mkdir()
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
            git("init", "-q")
            (repo / "DESCRIPTION").write_text("Package: MetaboLensR\nVersion: 0.1.0\n")
            git("add", "DESCRIPTION")
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
            commit = git("rev-parse", "HEAD")
            (repo / "DESCRIPTION").write_text("uncommitted changes")
            (repo / "private-data.txt").write_text("must not be copied")
            archive = root / "MetaboLensR.tar.gz"
            record = module.export_package(repo, commit, archive)
            self.assertEqual(record["package_commit"], commit)
            self.assertEqual(record["package_sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            with tarfile.open(archive) as bundle:
                self.assertEqual(bundle.extractfile("MetaboLensR/DESCRIPTION").read(), b"Package: MetaboLensR\nVersion: 0.1.0\n")
                self.assertNotIn("MetaboLensR/private-data.txt", bundle.getnames())


if __name__ == "__main__":
    unittest.main()
