"""Instrument reinjections retain one study sample and independent metadata."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException

from backend.tests import test_advanced_backend_services as fixtures
from app.models.models import AssaySampleLink, DatasetFile, Sample
from app.schemas.study import SampleBulkCreateItem, SampleBulkPathCreateItem, SamplePathReference
from app.services.file_service import FileService


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AdvancedBackendServiceTests()
        self.fixture.setUp()
        self.db = self.fixture.db
        self.admin = self.fixture._user("admin@metabolens.local")
        self.study = self.fixture._create_study()
        self.assay = self.fixture._default_assay(self.study.id)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.fixture.tearDown)
        self.files = FileService(self.db)

    def register(self, filename, order, sample_id=None):
        path = Path(self.temp.name, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RAW")
        return self.files.create_samples_with_paths_for_assay(self.admin.id, self.assay.id, [
            SampleBulkPathCreateItem(
                existing_sample_id=sample_id,
                sample=None if sample_id else SampleBulkCreateItem(name="S1", description="Study sample"),
                annotations_json={"group_label": "control", "batch_id": f"batch-{order}", "injection_order": order},
                raw_file=SamplePathReference(source_path=str(path)),
            )])[0]

    def test_reinjections_have_independent_metadata_and_one_sample(self):
        sample = self.register("first.raw", 1)
        repeated = self.register("repeat.raw", 7, sample.id)
        self.assertEqual(sample.id, repeated.id)
        self.assertEqual(self.db.query(Sample).count(), 1)
        link = self.db.query(AssaySampleLink).filter_by(assay_id=self.assay.id, sample_id=sample.id).one()
        self.assertEqual(link.annotations_json, {"group_label": "control"})
        rows = sorted(self.files.list_for_assay(self.admin.id, self.assay.id), key=lambda f: f["id"])
        self.assertNotEqual(rows[0]["acquisition_id"], rows[1]["acquisition_id"])
        self.assertEqual([r["acquisition_annotations_json"]["injection_order"] for r in rows], [1, 7])
        self.assertEqual([r["acquisition_annotations_json"]["batch_id"] for r in rows], ["batch-1", "batch-7"])

    def test_same_filename_in_different_folders_is_a_distinct_acquisition(self):
        sample = self.register("first/S1.raw", 1)
        self.register("repeat/S1.raw", 7, sample.id)
        self.assertEqual(self.db.query(DatasetFile).count(), 2)
        with self.assertRaises(HTTPException):
            self.register("repeat/S1.raw", 8, sample.id)

    def test_reinjection_cannot_reuse_a_sample_from_another_study(self):
        sample = self.register("first.raw", 1)
        other = self.fixture._create_study()
        self.assay = self.fixture._default_assay(other.id)
        with self.assertRaises(HTTPException):
            self.register("other.raw", 2, sample.id)


    def test_launch_freezes_acquisition_metadata_before_worker_materialization(self):
        from app.models.models import WorkflowDefinition
        from app.services.study_service import StudyService
        from app.services.workflow_launch_service import WorkflowLaunchService
        from backend.tests.test_engine_contract import fixture_descriptor
        sample = self.register("first.raw", 1)
        self.register("repeat.raw", 7, sample.id)
        root = Path(self.temp.name)
        (root / "fixture.nf").write_text("// fixture")
        workflow = self.fixture._workflow_definition()
        workflow.pipeline_root = str(root)
        workflow.static_input_config_json = {"engine": fixture_descriptor()}
        workflow.parameter_schema_json = {"type": "object", "properties": {}}
        self.db.commit()
        StudyService(self.db).transition(self.admin.id, self.study.id, "approved")
        self.fixture._add_metadata(self.study.id, self.assay.id, submitted_by=self.admin.id, status="valid")
        job = WorkflowLaunchService(self.db).launch(self.admin.id, self.assay.id, workflow.id)
        self.assertEqual([r["injection_order"] for r in job.input_manifest_json["sample_metadata_rows"]], [1, 7])
        from app.models.models import Acquisition
        acquisition = self.db.query(Acquisition).order_by(Acquisition.id).first()
        acquisition.annotations_json = {"batch_id": "edited", "injection_order": 99}
        sample.name = "Edited later"
        self.db.commit()
        self.db.refresh(job)
        self.assertEqual([r["sample_name"] for r in job.input_manifest_json["sample_metadata_rows"]], ["S1", "S1"])
        self.assertEqual([r["injection_order"] for r in job.input_manifest_json["sample_metadata_rows"]], [1, 7])

    def test_acquisition_updates_are_scoped_audited_and_permission_checked(self):
        from app.models.models import Acquisition, AuditEvent
        from app.schemas.acquisition import AcquisitionPatch
        from app.services.acquisition_service import AcquisitionService
        self.register("first.raw", 1)
        service = AcquisitionService(self.db)
        item = service.list_for_assay(self.admin.id, self.assay.id)[0]
        stranger = self.fixture._user("submitter@metabolens.local")
        with self.assertRaises(HTTPException):
            service.update(stranger.id, self.assay.id, item["id"], AcquisitionPatch(annotations_json={"injection_order": 5}))
        wrong_assay = self.fixture._default_assay(self.fixture._create_study().id)
        with self.assertRaises(HTTPException):
            service.update(self.admin.id, wrong_assay.id, item["id"], AcquisitionPatch())
        for invalid in (0, -1, 1.5, True, {}, []):
            with self.subTest(order=invalid), self.assertRaises(ValueError):
                AcquisitionPatch(annotations_json={"injection_order": invalid})
        acquisition = self.db.get(Acquisition, item["id"])
        acquisition.metadata_review_required = True
        self.db.commit()
        result = service.update(self.admin.id, self.assay.id, item["id"], AcquisitionPatch(
            annotations_json={"injection_order": 5, "batch_id": "corrected"}))
        self.assertFalse(result["metadata_review_required"])
        self.assertEqual(result["annotations_json"]["injection_order"], 5)
        self.assertEqual(self.db.query(AuditEvent).filter_by(event_type="acquisition.update").count(), 1)

    def test_migration_backfill_does_not_guess_positions_for_repeated_files(self):
        import importlib.util
        from app.models.models import Acquisition
        sample = self.register("first.raw", 1)
        self.register("repeat.raw", 7, sample.id)
        self.db.query(DatasetFile).update({DatasetFile.acquisition_id: None}, synchronize_session=False)
        self.db.query(Acquisition).delete(synchronize_session=False)
        link = self.db.query(AssaySampleLink).one()
        link.annotations_json = {"group_label": "control", "batch_id": "overwritten", "injection_order": 7}
        self.db.commit()
        path = Path(__file__).parents[1] / "alembic/versions/a1c8e4f9b260_add_acquisitions.py"
        spec = importlib.util.spec_from_file_location("acquisition_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.backfill(self.db.connection())
        self.db.commit()
        self.db.expire_all()
        acquisitions = self.db.query(Acquisition).all()
        self.assertEqual(len(acquisitions), 2)
        self.assertTrue(all(a.metadata_review_required for a in acquisitions))
        self.assertEqual([a.annotations_json for a in acquisitions], [{}, {}])
        self.assertEqual(self.db.query(Sample).count(), 1)
        self.assertEqual(self.db.query(AssaySampleLink).one().annotations_json["injection_order"], 7)

    def test_deleting_sample_removes_its_acquisitions(self):
        from app.models.models import Acquisition
        from app.services.study_service import StudyService
        sample = self.register("first.raw", 1)
        self.register("repeat.raw", 7, sample.id)
        StudyService(self.db).delete_sample(self.admin.id, self.study.id, sample.id)
        self.assertEqual(self.db.query(Acquisition).count(), 0)
        self.assertEqual(self.db.query(DatasetFile).count(), 0)

    def test_bulk_upload_reinjections_reuse_sample_even_with_same_filename(self):
        from io import BytesIO
        from fastapi import UploadFile
        from app.models.models import Acquisition
        from app.services.storage_service import LocalUploadStorage
        self.files.storage = LocalUploadStorage(self.temp.name)
        samples = self.files.create_samples_with_files(self.admin.id, self.study.id,
            [SampleBulkCreateItem(name="S1", description="Same vial", annotations_json={
                "group_label": "control", "batch_id": "1", "injection_order": order}) for order in (1, 7)],
            [UploadFile(filename="S1.raw", file=BytesIO(content)) for content in (b"first", b"repeat")])
        self.assertEqual([s.id for s in samples], [samples[0].id, samples[0].id])
        self.assertEqual(self.db.query(Sample).count(), 1)
        self.assertEqual([a.annotations_json["injection_order"] for a in self.db.query(Acquisition).order_by(Acquisition.id)], [1, 7])

    def test_sample_patch_cannot_overwrite_acquisition_metadata(self):
        from app.schemas.study import SamplePatch
        from app.services.study_service import StudyService
        sample = self.register("first.raw", 1)
        with self.assertRaises(HTTPException):
            StudyService(self.db).update_sample(self.admin.id, self.study.id, sample.id,
                SamplePatch(annotations_json={"injection_order": 99}))

    def test_legacy_two_step_registration_transfers_metadata_only_to_first_measurement(self):
        from app.schemas.study import SampleCreate
        from app.schemas.dataset_file import DatasetFileRegister
        from app.services.study_service import StudyService
        sample = StudyService(self.db).add_sample(self.admin.id, self.study.id, SampleCreate(
            name="S1", annotations_json={"group_label": "control", "batch_id": "legacy", "injection_order": 7}))
        first = self.files.register(self.admin.id, self.study.id, DatasetFileRegister(
            sample_id=sample.id, file_role="thermo_raw", original_filename="first.raw", storage_uri=Path(self.temp.name, "first.raw").as_uri()))
        second = self.files.register(self.admin.id, self.study.id, DatasetFileRegister(
            sample_id=sample.id, file_role="thermo_raw", original_filename="repeat.raw", storage_uri=Path(self.temp.name, "repeat.raw").as_uri()))
        self.assertEqual(first.acquisition.annotations_json, {"batch_id": "legacy", "injection_order": 7})
        self.assertEqual(second.acquisition.annotations_json, {})

    def test_injection_volume_rejects_boolean_and_nonnumeric_values(self):
        from app.schemas.acquisition import AcquisitionPatch
        for value in (True, False, {}, [], "nan", "inf", 0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AcquisitionPatch(annotations_json={"injection_volume_ul": value})
