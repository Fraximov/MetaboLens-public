import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.models.models import WorkflowDefinition, WorkflowJob

from config import WorkerSettings
from services.artifact_collector import ArtifactCollector
from services.hpc_smoke_materializer import HpcSmokeMaterializer, prepare_smoke_run
from services.status_reader import StatusReader


class HpcSmokeMaterializerTests(unittest.TestCase):
    def test_prepare_writes_tiny_nextflow_project_and_contract(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings = WorkerSettings(
                database_url="sqlite://",
                workflow_runs_root=tmpdir,
                workflow_slurm_smoke_container_image="/g/example-lab/members/researcher/singularity/images/python-3.11.sif",
            )
            job = WorkflowJob(
                id=77,
                study_id=3,
                assay_id=5,
                workflow_definition_id=11,
                requested_by=2,
                status="queued",
                input_manifest_json={"job_kind": "hpc_smoke_test", "nextflow_profile": "embl_slurm"},
                parameter_values_json={"workflow_stage": "hpc_smoke"},
                execution_backend="nextflow",
                execution_profile="slurm_singularity_ssh",
                status_payload_json={},
            )
            definition = WorkflowDefinition(id=11, name="msdial5-nextflow", version="test", nextflow_entrypoint="main.nf")

            run = HpcSmokeMaterializer(settings).prepare(job, definition)

            self.assertEqual(run.execution_profile, "slurm_singularity_ssh")
            self.assertEqual(run.nextflow_profile, "embl_slurm")
            self.assertEqual(run.nextflow_params["workflow_stage"], "hpc_smoke")
            self.assertEqual(run.nextflow_params["input_dir"], str(Path(tmpdir).resolve() / "job_77" / "inputs" / "raw_data"))
            self.assertTrue((Path(run.run_root) / "main.nf").is_file())
            self.assertTrue((Path(run.staged_input_dir) / "sample_a.txt").is_file())
            self.assertTrue((Path(run.staged_input_dir) / "sample_b.txt").is_file())
            config = Path(run.nextflow_config_override_path).read_text(encoding="utf-8")
            self.assertIn("process.executor = 'slurm'", config)
            self.assertIn("singularity.enabled = true", config)
            self.assertIn("process.container = '/g/example-lab/members/researcher/singularity/images/python-3.11.sif'", config)
            params = json.loads(Path(run.params_path).read_text(encoding="utf-8"))
            self.assertEqual(params["nextflow_params"]["analysis_mode"], "ssh_slurm_smoke")
            self.assertEqual(params["selected_files"], [])

    def test_prepare_requires_smoke_container_for_app_jobs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings = WorkerSettings(
                database_url="sqlite://",
                workflow_runs_root=tmpdir,
                workflow_slurm_smoke_container_image="",
            )
            job = WorkflowJob(
                id=78,
                study_id=3,
                assay_id=5,
                workflow_definition_id=11,
                requested_by=2,
                status="queued",
                input_manifest_json={"job_kind": "hpc_smoke_test"},
                execution_backend="nextflow",
                execution_profile="slurm_singularity_ssh",
            )
            definition = WorkflowDefinition(id=11, name="msdial5-nextflow", version="test", nextflow_entrypoint="main.nf")

            with self.assertRaises(ValueError) as ctx:
                HpcSmokeMaterializer(settings).prepare(job, definition)

            self.assertIn("WORKFLOW_SLURM_SMOKE_CONTAINER_IMAGE", str(ctx.exception))

    def test_status_reader_estimates_two_smoke_tasks(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run = prepare_smoke_run(
                run_root=Path(tmpdir) / "job_9001",
                job_id=9001,
                study_id=1,
                assay_id=1,
                workflow_definition_id=1,
                execution_profile="slurm_singularity",
                nextflow_profile="embl_slurm",
                container_image="/g/example-lab/members/researcher/singularity/images/python-3.11.sif",
            )

            self.assertEqual(StatusReader().estimate_total_tasks(run), 2)

    def test_artifact_collector_registers_smoke_outputs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run = prepare_smoke_run(
                run_root=Path(tmpdir) / "job_9001",
                job_id=9001,
                study_id=1,
                assay_id=1,
                workflow_definition_id=1,
                execution_profile="slurm_singularity",
                nextflow_profile="embl_slurm",
                container_image="/g/example-lab/members/researcher/singularity/images/python-3.11.sif",
            )
            Path(run.results_path, "sample_a.upper.txt").write_text("ALPHA\nBETA\n", encoding="utf-8")
            Path(run.results_path, "sample_b.upper.txt").write_text("GAMMA\nDELTA\n", encoding="utf-8")

            collection = ArtifactCollector().collect(run, status_payload={"phase": "running"}, exit_code=0)

            smoke_records = [record for record in collection.output_records if record["artifact_role"] == "hpc_smoke_output"]
            self.assertEqual([record["filename"] for record in smoke_records], ["sample_a.upper.txt", "sample_b.upper.txt"])
            self.assertEqual(collection.result_summary["primary_output_count"], 2)


if __name__ == "__main__":
    unittest.main()
