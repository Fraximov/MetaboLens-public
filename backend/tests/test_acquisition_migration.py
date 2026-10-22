"""Opt-in migration check against a disposable PostgreSQL database."""
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase, skipUnless

from alembic import command
from alembic.config import Config
import sqlalchemy as sa


@skipUnless(os.getenv("ACQUISITION_MIGRATION_TEST"), "Requires a disposable PostgreSQL DATABASE_URL")
class AcquisitionMigrationTests(TestCase):
    def test_upgrade_backfills_singletons_flags_reinjections_and_preserves_ids(self):
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
        command.upgrade(config, "9c5f1d2a8b34")
        engine = sa.create_engine(os.environ["DATABASE_URL"])
        metadata = sa.MetaData()
        metadata.reflect(engine)
        now = datetime.now(timezone.utc)
        with engine.begin() as conn:
            conn.execute(metadata.tables["users"].insert().values(id=1, email="migration@test.local", display_name="Test",
                auth_provider="local", is_active=True, created_at=now, updated_at=now))
            conn.execute(metadata.tables["studies"].insert().values(id=1, public_or_internal_identifier="TEST",
                title="Migration test", status="draft", created_by=1, created_at=now, updated_at=now, metadata_json={}))
            conn.execute(metadata.tables["assays"].insert().values(id=1, study_id=1, name="HILIC positive",
                platform="metabolomics", status="draft", is_default=True, created_by=1, created_at=now, updated_at=now))
            for sample_id in (1, 2):
                conn.execute(metadata.tables["samples"].insert().values(id=sample_id, study_id=1, name=f"S{sample_id}",
                    annotations_json={"group_label": "control"}, created_at=now))
                conn.execute(metadata.tables["assay_sample_links"].insert().values(assay_id=1, sample_id=sample_id,
                    sample_role="input", annotations_json={"batch_id": "B", "injection_order": 7, "vial": "A1"}, created_at=now))
            conn.execute(metadata.tables["datasets"].insert().values(id=1, study_id=1, assay_id=1, name="raw",
                dataset_kind="raw", dataset_type="thermo_raw_acquisition", status="registered", created_by=1, created_at=now))
            for file_id, sample_id in ((11, 1), (12, 2), (13, 2), (14, None)):
                conn.execute(metadata.tables["dataset_files"].insert().values(id=file_id, dataset_id=1, sample_id=sample_id,
                    file_role="thermo_raw", original_filename="S1.raw", storage_uri=f"file:///test/{file_id}.raw",
                    source_scheme="file", ingest_mode="registered_path", status="registered", created_by=1, created_at=now))
        command.upgrade(config, "head")
        metadata = sa.MetaData()
        metadata.reflect(engine)
        files, acquisitions = metadata.tables["dataset_files"], metadata.tables["acquisitions"]
        with engine.connect() as conn:
            rows = conn.execute(sa.select(files.c.id, acquisitions.c.annotations_json, acquisitions.c.metadata_review_required)
                .select_from(files.outerjoin(acquisitions, files.c.acquisition_id == acquisitions.c.id)).order_by(files.c.id)).all()
            self.assertEqual([r.id for r in rows], [11, 12, 13, 14])
            self.assertEqual(rows[0].annotations_json, {"batch_id": "B", "injection_order": 7, "vial": "A1"})
            self.assertEqual([r.annotations_json for r in rows[1:3]], [{}, {}])
            self.assertEqual([r.metadata_review_required for r in rows], [False, True, True, None])
            self.assertEqual(conn.scalar(sa.select(sa.func.count()).select_from(metadata.tables["samples"])), 2)
        command.downgrade(config, "9c5f1d2a8b34")
        self.assertFalse(sa.inspect(engine).has_table("acquisitions"))
        self.assertNotIn("acquisition_id", {c["name"] for c in sa.inspect(engine).get_columns("dataset_files")})
        engine.dispose()
