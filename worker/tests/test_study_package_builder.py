"""Build failures and worker restarts must never publish partial packages."""
import unittest
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.models import StudyPackage, User, Study
from services.study_package_builder import PackageBuildRunner, validation_issues, verified_spectral_files


class StudyPackageBuilderTests(unittest.TestCase):
    def test_missing_or_malformed_validation_is_never_success(self):
        for report in ({}, {"completed": True}, {"completed": True, "violations": [], "parser_messages": {"i.txt": [{"type":"ERROR"}]}}):
            self.assertTrue(validation_issues(report))
        self.assertEqual(validation_issues({"completed": True, "violations": [], "parser_messages": {}}), [])
        result = validation_issues({"completed": True, "violations": [dict(type="ERROR", identifier="rule_x", violation="Missing raw file", sourceFile="a.txt")], "parser_messages": {}})
        self.assertEqual(result[0]["code"], "rule_x")
        self.assertEqual(result[0]["file"], "a.txt")

    def test_checksum_reuse_comes_only_from_earlier_built_packages_of_the_study(self):
        with TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'test.db'}")
            Base.metadata.create_all(engine)
            sessions = sessionmaker(bind=engine)
            with sessions() as db:
                db.add(User(id=1, email="fixture@example.invalid", display_name="Fixture"))
                db.add_all([Study(id=i, title="Fixture", public_or_internal_identifier=f"fixture-{i}", created_by=1)
                            for i in (1, 2)])
                db.flush()
                for package_id, study_id, status, sha in ((1, 1, "validation_failed", "a"), (2, 1, "complete", "b"),
                                                          (3, 1, "failed", "c"), (4, 2, "complete", "d"),
                                                          (5, 1, "building", "e")):
                    db.add(StudyPackage(id=package_id, study_id=study_id, revision=package_id, requested_by=1,
                        request_key=str(package_id), request_json={}, snapshot_json={}, status=status,
                        report_json={"spectral_files": [{"file_id": 567, "sha256": sha * 64}]}))
                db.commit()
                self.assertEqual(verified_spectral_files(db, 1, 5)[567]["sha256"], "b" * 64)
            engine.dispose()

    def test_queued_export_copies_linked_spectra_or_fails_clearly(self):
        import hashlib
        import json
        from collections import namedtuple
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = create_engine(f"sqlite:///{root / 'test.db'}")
            Base.metadata.create_all(engine)
            sessions = sessionmaker(bind=engine)
            source = root / "raw/QC_02.raw"
            source.parent.mkdir()
            source.write_bytes(b"instrument bytes")
            package_dir = root / "packages/package-1"
            (package_dir / "submission/FILES").mkdir(parents=True)
            (package_dir / "provenance").mkdir()
            (package_dir / "submission/FILES/QC_02.raw").symlink_to(source)
            (package_dir / "provenance/manifest.json").write_text(json.dumps({"files": [dict(
                path="submission/FILES/QC_02.raw", sha256=hashlib.sha256(b"instrument bytes").hexdigest(),
                size_bytes=16, linked=True)]}))
            with sessions() as db:
                db.add(User(id=1, email="fixture@example.invalid", display_name="Fixture"))
                db.add(Study(id=1, title="Fixture", public_or_internal_identifier="fixture", created_by=1))
                db.flush()
                for package_id in (1, 2):
                    db.add(StudyPackage(id=package_id, study_id=1, revision=package_id, requested_by=1,
                                        request_key=str(package_id), request_json={}, snapshot_json={},
                                        status="validation_failed", export_status="queued"))
                db.commit()
            settings = SimpleNamespace(study_package_root=str(root / "packages"),
                                       study_package_export_root=str(root / "exports"))
            runner = PackageBuildRunner(sessions, settings)
            try:
                self.assertTrue(runner.poll())
                runner.future.result()
                with sessions() as db:
                    exported = db.get(StudyPackage, 1)
                    self.assertEqual(exported.export_status, "exported")
                    copy = Path(exported.export_uri.removeprefix("file://")) / "submission/FILES/QC_02.raw"
                self.assertFalse(copy.is_symlink())
                self.assertEqual(copy.read_bytes(), b"instrument bytes")
                # Package 2 has no folder; then a full disk stops a retry before copying.
                runner.poll()
                runner.future.result()
                with sessions() as db:
                    self.assertEqual(db.get(StudyPackage, 2).export_status, "export_failed")
                    retry = db.get(StudyPackage, 1)
                    retry.export_status = "queued"
                    db.commit()
                usage = namedtuple("usage", "total used free")
                with patch("services.study_package_builder.shutil.disk_usage", return_value=usage(10, 9, 1)):
                    runner.poll()
                    runner.future.result()
                with sessions() as db:
                    failed = db.get(StudyPackage, 1)
                    self.assertEqual(failed.export_status, "export_failed")
                    self.assertIn("free", failed.export_error)
                    failed.export_status = "exporting"
                    db.commit()
                runner.recover_interrupted()
                with sessions() as db:
                    self.assertIn("restarted", db.get(StudyPackage, 1).export_error)
            finally:
                runner.close()
                engine.dispose()

    def test_restart_marks_interrupted_request_failed_without_touching_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = create_engine(f"sqlite:///{root / 'test.db'}")
            Base.metadata.create_all(engine)
            sessions = sessionmaker(bind=engine)
            with sessions() as db:
                db.add(User(id=1, email="fixture@example.invalid", display_name="Fixture"))
                db.add(Study(id=1, title="Fixture", public_or_internal_identifier="fixture", created_by=1))
                db.flush()
                db.add(StudyPackage(id=1, study_id=1, revision=1, requested_by=1, request_key="fixture",
                    request_json={}, snapshot_json={}, status="building"))
                db.commit()
            partial = root / "package-1"
            partial.mkdir()
            (partial / "partial.raw").write_bytes(b"partial")
            runner = PackageBuildRunner(sessions, SimpleNamespace(study_package_root=str(root)))
            try:
                runner.recover_interrupted()
                with sessions() as db:
                    package = db.get(StudyPackage, 1)
                    self.assertEqual(package.status, "failed")
                    self.assertIsNone(package.output_uri)
                    self.assertIsNotNone(package.finished_at)
                self.assertTrue((partial / "partial.raw").exists())
                # An in-flight copy is never awaited by the polling cycle.
                runner.future = Future()
                self.assertFalse(runner.poll())
            finally:
                runner.close()
                engine.dispose()
