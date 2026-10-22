"""Client-selected local paths must stay inside configured import roots."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException

from backend.tests import test_advanced_backend_services as fixtures
from app.models.models import DatasetFile
from app.schemas.dataset_file import DatasetFileRegister
from app.schemas.study import SampleBulkCreateItem, SampleBulkPathCreateItem
from app.services.file_service import FileService


class FilePathBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AdvancedBackendServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.allowed = self.root / "allowed"
        self.outside = self.root / "outside"
        self.uploads = self.root / "uploads"
        for folder in (self.allowed, self.outside, self.uploads):
            folder.mkdir()
            (folder / "sample.raw").write_bytes(b"fixture")
        self.user = self.fixture._user("submitter@metabolens.local")
        self.study = self.fixture._create_study(self.user.email)
        self.assay = self.fixture._default_assay(self.study.id)
        self.service = FileService(self.fixture.db)
        settings = patch.multiple(self.service.settings, raw_path_browse_roots=str(self.allowed),
                                  upload_storage_dir=str(self.uploads))
        settings.start()
        self.addCleanup(settings.stop)

    def register(self, path):
        return self.service.register(self.user.id, self.study.id, DatasetFileRegister(
            file_role="thermo_raw", original_filename="sample.raw", storage_uri=Path(path).as_uri()))

    def test_all_client_path_entrypoints_deny_outside_root(self):
        operations = [
            lambda: self.service.browse_raw_folders(self.user.id, self.study.id, str(self.outside)),
            lambda: self.service.scan_raw_folder(self.user.id, self.study.id, str(self.outside)),
            lambda: self.service.import_raw_folder_for_assay(self.user.id, self.assay.id, str(self.outside)),
            lambda: self.register(self.outside / "sample.raw"),
            lambda: self.service.create_samples_with_paths_for_assay(self.user.id, self.assay.id, [
                SampleBulkPathCreateItem(sample=SampleBulkCreateItem(name="outside", description="fixture"),
                                         raw_file={"source_path": str(self.outside / "sample.raw")})]),
        ]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(HTTPException) as error:
                operation()
            self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.fixture.db.query(DatasetFile).count(), 0)

    def test_absent_or_unconfigured_roots_never_fall_back_to_filesystem_root(self):
        for configured in ("", str(self.root / "missing"), "relative-root"):
            with self.subTest(configured=configured), patch.object(self.service.settings, "raw_path_browse_roots", configured):
                self.assertEqual(self.service.browse_raw_folders(self.user.id, self.study.id, None)["entries"], [])
                with self.assertRaises(HTTPException):
                    self.service.scan_raw_folder(self.user.id, self.study.id, str(self.outside))

    def test_symlink_escapes_are_not_listed_or_registered(self):
        link = self.allowed / "escape"
        link.symlink_to(self.outside, target_is_directory=True)
        file_link = self.allowed / "escape.raw"
        file_link.symlink_to(self.outside / "sample.raw")
        listed = self.service.browse_raw_folders(self.user.id, self.study.id, str(self.allowed))
        self.assertEqual(listed["entries"], [])
        scanned = self.service.scan_raw_folder(self.user.id, self.study.id, str(self.allowed))
        self.assertEqual([row["original_filename"] for row in scanned], ["sample.raw"])
        for path in (file_link, link / "sample.raw"):
            with self.subTest(path=path), self.assertRaises(HTTPException):
                self.register(path)

    def test_allowed_inputs_and_upload_storage_remain_registerable(self):
        with patch("app.services.file_service.sha256_file", side_effect=AssertionError("Preview must not hash measurements")):
            self.assertEqual(len(self.service.scan_raw_folder(self.user.id, self.study.id, str(self.allowed))), 1)
        file = self.register(self.uploads / "sample.raw")
        self.assertEqual(file.storage_uri, (self.uploads / "sample.raw").as_uri())
        result = self.service.import_raw_folder_for_assay(self.user.id, self.assay.id, str(self.allowed))
        self.assertEqual(result["imported_count"], 1)

    def test_unreadable_input_hash_does_not_register_a_partial_file(self):
        with patch("app.services.file_service.sha256_file", side_effect=OSError("unreadable")):
            with self.assertRaises(HTTPException) as error:
                self.service.import_raw_folder_for_assay(self.user.id, self.assay.id, str(self.allowed))
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.fixture.db.query(DatasetFile).count(), 0)

    def test_remote_file_authority_is_rejected(self):
        with self.assertRaises(HTTPException):
            self.service.register(self.user.id, self.study.id, DatasetFileRegister(
                file_role="thermo_raw", original_filename="sample.raw",
                storage_uri=f"file://remote-host{self.allowed}/sample.raw"))
