"""Export copies linked spectra only when every byte still matches the package."""
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.study_package_export import export_package


def package_with_link(root):
    """A minimal package: one ISA file and one spectral link, both in the inventory."""
    source = root / "registered/QC_02.raw"
    source.parent.mkdir()
    source.write_bytes(b"instrument bytes")
    package = root / "package-7"
    (package / "submission/FILES/RAW_FILES/POS").mkdir(parents=True)
    (package / "provenance").mkdir()
    (package / "submission/i_Investigation.txt").write_text("STUDY\n")
    (package / "submission/FILES/RAW_FILES/POS/QC_02.raw").symlink_to(source)
    files = [dict(path=path, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), **extra)
             for path, data, extra in (("submission/i_Investigation.txt", b"STUDY\n", {}),
                                       ("submission/FILES/RAW_FILES/POS/QC_02.raw", b"instrument bytes", {"linked": True}))]
    (package / "provenance/manifest.json").write_text(json.dumps({"files": files}))
    return package, source


class StudyPackageExportTests(unittest.TestCase):
    def test_export_materializes_links_and_verifies_bytes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, _ = package_with_link(root)
            result = export_package(package, root / "export")
            copy = root / "export/submission/FILES/RAW_FILES/POS/QC_02.raw"
            self.assertFalse(copy.is_symlink())
            self.assertEqual(copy.read_bytes(), b"instrument bytes")
            self.assertEqual(result["linked_files_copied"], 1)
            with self.assertRaises(FileExistsError):
                export_package(package, root / "export")

    def test_changed_or_missing_source_stops_export_and_leaves_only_a_partial_copy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, source = package_with_link(root)
            source.write_bytes(b"Instrument bytes")  # Same size, different content.
            with self.assertRaisesRegex(ValueError, "Checksum or size changed"):
                export_package(package, root / "edited")
            self.assertFalse((root / "edited").exists())
            self.assertTrue((root / "edited.partial").exists())
            source.unlink()
            with self.assertRaisesRegex(ValueError, "unavailable"):
                export_package(package, root / "missing")
            self.assertFalse((root / "missing").exists())


if __name__ == "__main__":
    unittest.main()
