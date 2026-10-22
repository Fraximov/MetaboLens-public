import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.models import Assay, AssaySampleLink, Dataset, DatasetFile, Sample, User
from app.schemas.study import StudyCreate
from app.services.seed_service import SeedService
from app.services.study_service import StudyService
from services.input_stager import InputStager
from services.samplesheet_builder import SamplesheetBuilder


class SamplesheetAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        Base.metadata.create_all(self.engine)
        self.db = self.SessionLocal()
        SeedService(self.db).run()
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_samplesheet_uses_effective_assay_sample_annotations(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            raw_path = root / "SampleOne.raw"
            raw_path.write_bytes(b"RAW")

            admin = self.db.query(User).filter(User.email == "admin@metabolens.local").one()
            study = StudyService(self.db).create(admin.id, StudyCreate(title="T", description="D"))
            assay = self.db.query(Assay).filter(Assay.study_id == study.id, Assay.is_default.is_(True)).one()
            sample = Sample(
                study_id=study.id,
                name="SampleOne",
                sample_type="sample",
                organism="Homo sapiens",
                replicate_number=1,
                dilution_factor=1.0,
                annotations_json={
                    "group_label": "Sample default",
                    "batch_id": 2,
                    "injection_order": 7,
                    "plate": "A",
                    "ignored_nested": {"x": 1},
                },
            )
            dataset = Dataset(
                study_id=study.id,
                assay_id=assay.id,
                name="raw",
                dataset_kind="raw",
                dataset_type="thermo_raw_acquisition",
                status="registered",
                created_by=admin.id,
            )
            self.db.add_all([sample, dataset])
            self.db.flush()
            # The assay link's annotations win over the sample's study-wide defaults.
            self.db.add(AssaySampleLink(
                assay_id=assay.id,
                sample_id=sample.id,
                sample_role="input",
                annotations_json={"group_label": "Assay Lean", "batch_id": 3, "injection_order": 8, "plate": "B"},
            ))
            dataset_file = DatasetFile(
                dataset_id=dataset.id,
                sample_id=sample.id,
                file_role="thermo_raw",
                original_filename=raw_path.name,
                storage_uri=f"file://{raw_path}",
                checksum_sha256="b" * 64,
                file_size_bytes=raw_path.stat().st_size,
                media_type="application/octet-stream",
                status="registered",
                created_by=admin.id,
            )
            self.db.add(dataset_file)
            self.db.commit()

            staged = InputStager().stage_raw_input_files([dataset_file], root / "staged")
            output_path = root / "samplesheet.csv"
            SamplesheetBuilder(self.db).write(
                selected_files=[dataset_file],
                reference_file_asset_id=dataset_file.id,
                output_path=output_path,
                staged_files=staged,
            )

            with output_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["group_label"], "Assay Lean")
            self.assertEqual(row["batch_id"], "3")
            self.assertEqual(row["injection_order"], "8")
            self.assertEqual(row["annotation__plate"], "B")
            self.assertNotIn("annotation__ignored_nested", row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
