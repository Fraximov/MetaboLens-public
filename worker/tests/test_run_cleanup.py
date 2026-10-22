import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.run_cleanup import RunCleanupService
from services.materialized_run import MaterializedRun


def _run(root: Path, staged_input_dir: Path | None = None) -> MaterializedRun:
    contract_dir = root / "contract"
    results_dir = root / "results"
    logs_dir = root / "logs"
    for path in (contract_dir, results_dir, logs_dir, root / "work"):
        path.mkdir(parents=True, exist_ok=True)
    return MaterializedRun(
        job_id=1,
        study_id=1,
        assay_id=1,
        workflow_definition_id=1,
        execution_profile="local_docker",
        pipeline_root=str(root),
        entrypoint_path="main_msdial5.nf",
        nextflow_profile="docker",
        run_name="test",
        run_root=str(root),
        staged_input_dir=str(staged_input_dir or root / "inputs" / "raw_data"),
        contract_dir=str(contract_dir),
        results_path=str(results_dir),
        logs_path=str(logs_dir),
        work_path=str(root / "work"),
        params_path=str(contract_dir / "params.json"),
        samplesheet_path=str(contract_dir / "samplesheet.csv"),
        staged_msdial_config_path=str(contract_dir / "msdial_config.txt"),
        nextflow_config_override_path=str(contract_dir / "nextflow.override.config"),
        artifact_manifest_path=str(contract_dir / "artifact-manifest.json"),
        reference_file_asset_id=1,
        reference_path=str(root / "inputs" / "raw_data" / "sample.raw"),
        ms1_library_path=str(root / "libraries" / "ms1.txt"),
        ms2_library_path=str(root / "libraries" / "ms2.msp"),
        is_library_path=str(root / "libraries" / "is.txt"),
        config_source="uploaded_file",
        metadata_snapshot={},
        method_metadata={},
        selected_files=[],
        nextflow_params={},
    )


class RunCleanupServiceTests(unittest.TestCase):
    def test_remove_staged_raw_inputs_deletes_only_input_copies(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            raw_dir = root / "inputs" / "raw_data"
            raw_dir.mkdir(parents=True)
            (raw_dir / "a.raw").write_bytes(b"123")
            (raw_dir / "nested").mkdir()
            (raw_dir / "nested" / "b.raw").write_bytes(b"4567")
            (root / "contract").mkdir()
            (root / "contract" / "params.json").write_text("{}", encoding="utf-8")
            (root / "logs").mkdir()
            (root / "logs" / "execution.log").write_text("ok\n", encoding="utf-8")
            (root / "results").mkdir()
            (root / "results" / "output.txt").write_text("result\n", encoding="utf-8")

            payload = RunCleanupService().remove_staged_raw_inputs(_run(root))

            self.assertTrue(payload["staged_inputs_removed"])
            self.assertEqual(payload["removed_file_count"], 2)
            self.assertEqual(payload["removed_bytes"], 7)
            self.assertFalse(raw_dir.exists())
            self.assertTrue((root / "contract" / "params.json").exists())
            self.assertTrue((root / "logs" / "execution.log").exists())
            self.assertTrue((root / "results" / "output.txt").exists())

    def test_remove_staged_raw_inputs_refuses_unexpected_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            unsafe_dir = root / "results"
            unsafe_dir.mkdir()

            payload = RunCleanupService().remove_staged_raw_inputs(_run(root, staged_input_dir=unsafe_dir))

            self.assertFalse(payload["staged_inputs_removed"])
            self.assertIn("Refusing to remove unexpected staged input path", payload["cleanup_error"])
            self.assertTrue(unsafe_dir.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
