import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from app.models.models import WorkflowJob
from config import WorkerSettings
from services.hpc_smoke_materializer import prepare_smoke_run
from services.run_materializer import RunMaterializer


class RunRecoveryTests(unittest.TestCase):
    def create_run_and_job(self, root: Path):
        original = prepare_smoke_run(
            run_root=root,
            job_id=7,
            study_id=1,
            assay_id=2,
            workflow_definition_id=3,
            execution_profile="slurm_singularity_ssh",
            nextflow_profile="embl_slurm",
            run_name="hpc_smoke_job7",
        )
        job = WorkflowJob(
            id=7,
            study_id=1,
            assay_id=2,
            workflow_definition_id=3,
            requested_by=1,
            status="running",
            execution_profile="slurm_singularity_ssh",
            run_name=original.run_name,
            run_root=original.run_root,
            results_path=original.results_path,
            logs_path=original.logs_path,
            artifact_manifest_path=(
                original.artifact_manifest_path
            ),
            external_run_id="482913",
        )
        snapshot_path = (
            Path(original.contract_dir)
            / "materialized-run.json"
        )
        return original, job, snapshot_path

    def test_load_existing_round_trips_materialized_run(
        self,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            original, job, snapshot_path = (
                self.create_run_and_job(
                    Path(tmpdir) / "job_7"
                )
            )
            materializer = RunMaterializer(
                Mock(),
                WorkerSettings(database_url="sqlite://"),
            )

            self.assertTrue(snapshot_path.is_file())

            recovered = materializer.load_existing(job)

            self.assertEqual(recovered, original)

    def test_load_existing_requires_recovery_snapshot(
        self,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            _, job, snapshot_path = self.create_run_and_job(
                Path(tmpdir) / "job_7"
            )
            snapshot_path.unlink(missing_ok=True)
            materializer = RunMaterializer(
                Mock(),
                WorkerSettings(database_url="sqlite://"),
            )

            with self.assertRaisesRegex(
                ValueError,
                "Missing recovery snapshot",
            ):
                materializer.load_existing(job)

    def test_load_existing_rejects_wrong_job_snapshot(
        self,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            _, job, _ = self.create_run_and_job(
                Path(tmpdir) / "job_7"
            )
            job.id = 8
            materializer = RunMaterializer(
                Mock(),
                WorkerSettings(database_url="sqlite://"),
            )

            with self.assertRaisesRegex(
                ValueError,
                "belongs to workflow job 7",
            ):
                materializer.load_existing(job)

    def test_load_existing_rejects_unknown_snapshot_version(
        self,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            _, job, snapshot_path = self.create_run_and_job(
                Path(tmpdir) / "job_7"
            )
            snapshot_path.write_text(
                json.dumps(
                    {
                        "version": 999,
                        "run": {},
                    }
                ),
                encoding="utf-8",
            )
            materializer = RunMaterializer(
                Mock(),
                WorkerSettings(database_url="sqlite://"),
            )

            with self.assertRaisesRegex(
                ValueError,
                "Unsupported recovery snapshot version",
            ):
                materializer.load_existing(job)


if __name__ == "__main__":
    unittest.main(verbosity=2)