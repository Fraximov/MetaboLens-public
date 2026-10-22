import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.models import Assay, Study, User, WorkflowDefinition, WorkflowJob
from purge_deleted_workflow_jobs import purge_eligible_jobs, validated_run_root


class PurgeDeletedWorkflowJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, future=True)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _deleted_job(self, run_root: str | None) -> WorkflowJob:
        user = User(email="operator@example.org", display_name="Operator")
        self.db.add(user)
        self.db.flush()
        study = Study(
            public_or_internal_identifier=f"PURGE-{user.id}",
            title="Purge test",
            description=None,
            status="completed",
            created_by=user.id,
        )
        self.db.add(study)
        self.db.flush()
        assay = Assay(
            study_id=study.id,
            name="LC-MS",
            status="completed",
            created_by=user.id,
        )
        workflow = WorkflowDefinition(
            name="test-workflow",
            version="1",
            nextflow_entrypoint="main.nf",
        )
        self.db.add_all([assay, workflow])
        self.db.flush()
        job = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            requested_by=user.id,
            status="failed",
            input_manifest_json={},
            parameter_values_json={},
            status_payload_json={},
            run_root=run_root,
            deleted_at=datetime.now(timezone.utc) - timedelta(days=31),
            deleted_by=user.id,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def test_validated_run_root_accepts_exact_job_directory(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            job = SimpleNamespace(id=42, run_root=str(root / "job_42"))

            self.assertEqual(validated_run_root(job, str(root)), root / "job_42")

    def test_validated_run_root_refuses_outside_directory(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            job = SimpleNamespace(id=42, run_root=str(root.parent / "job_42"))

            with self.assertRaises(ValueError):
                validated_run_root(job, str(root))

    def test_validated_run_root_allows_missing_run_root(self) -> None:
        job = SimpleNamespace(id=42, run_root=None)

        self.assertIsNone(validated_run_root(job, "/runs"))

    def test_validated_run_root_refuses_symlinked_job_directory(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            other = root / "other"
            other.mkdir()
            job_root = root / "job_42"
            job_root.symlink_to(other, target_is_directory=True)
            job = SimpleNamespace(id=42, run_root=str(job_root))

            with self.assertRaises(ValueError):
                validated_run_root(job, str(root))

    def test_purge_removes_job_directory_and_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            job = self._deleted_job(None)
            job.run_root = str(root / f"job_{job.id}")
            run_root = Path(job.run_root)
            (run_root / "results").mkdir(parents=True)
            (run_root / "results" / "report.json").write_text("{}", encoding="utf-8")
            self.db.commit()
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)

            first = purge_eligible_jobs(self.db, str(root), cutoff)
            second = purge_eligible_jobs(self.db, str(root), cutoff)

            self.db.refresh(job)
            self.assertEqual(first["purged"], 1)
            self.assertEqual(first["failures"], [])
            self.assertFalse(run_root.exists())
            self.assertIsNotNone(job.purged_at)
            self.assertEqual(second["eligible"], 0)

    def test_dry_run_changes_neither_directory_nor_database(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            job = self._deleted_job(None)
            job.run_root = str(root / f"job_{job.id}")
            run_root = Path(job.run_root)
            run_root.mkdir()
            self.db.commit()

            result = purge_eligible_jobs(
                self.db,
                str(root),
                datetime.now(timezone.utc) - timedelta(days=30),
                dry_run=True,
            )

            self.db.refresh(job)
            self.assertEqual(result["eligible"], 1)
            self.assertEqual(result["purged"], 0)
            self.assertTrue(run_root.exists())
            self.assertIsNone(job.purged_at)

    def test_failed_job_without_run_root_can_be_purged(self) -> None:
        job = self._deleted_job(None)

        result = purge_eligible_jobs(
            self.db,
            "/runs",
            datetime.now(timezone.utc) - timedelta(days=30),
        )

        self.db.refresh(job)
        self.assertEqual(result["purged"], 1)
        self.assertIsNotNone(job.purged_at)

    def test_unsafe_path_is_reported_without_marking_job_purged(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            job = self._deleted_job(str(root.parent / "job_outside"))

            result = purge_eligible_jobs(
                self.db,
                str(root),
                datetime.now(timezone.utc) - timedelta(days=30),
            )

            self.db.refresh(job)
            self.assertEqual(result["purged"], 0)
            self.assertEqual(result["failures"][0]["job_id"], job.id)
            self.assertIsNone(job.purged_at)


if __name__ == "__main__":
    unittest.main(verbosity=2)
