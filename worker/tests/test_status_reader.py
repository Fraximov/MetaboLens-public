import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.materialized_run import MaterializedRun
from services.status_reader import StatusReader


def _run(root: Path, nextflow_params: dict[str, str] | None = None) -> MaterializedRun:
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
        staged_input_dir=str(root / "inputs" / "raw_data"),
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
        nextflow_params=nextflow_params or {},
    )


class StatusReaderTests(unittest.TestCase):
    def test_fixture_engine_keeps_unknown_total_during_execution_and_failure(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run = _run(Path(tmpdir), {"workflow_stage": "qc_preview"})
            run.engine_execution = {"descriptor": {"engine_id": "fixture_engine"}, "native_outputs": []}
            trace_dir = Path(run.results_path) / "pipeline_info"
            trace_dir.mkdir()
            (trace_dir / "execution_trace.txt").write_text(
                "task_id\tname\tstatus\n1\tPREPARE_INPUT\tCOMPLETED\n", encoding="utf-8",
            )
            (Path(run.logs_path) / "execution.log").write_text(
                "Submitted process > FIXTURE_NATIVE (A)\n", encoding="utf-8",
            )
            reader = StatusReader()

            self.assertEqual(reader.read_task_summary(run)["total_tasks"], 0)
            for phase in ("running", "failed"):
                with self.subTest(phase=phase):
                    payload = reader.build_status_payload(run, phase=phase)

                    self.assertEqual(payload["total_tasks"], 0)
                    self.assertEqual(payload["observed_tasks"], 1)
                    self.assertEqual(payload["completed_tasks"], 1)
                    self.assertEqual(payload["process_states"], {"PREPARE_INPUT": "done", "FIXTURE_NATIVE": "running"})

    def test_fixture_engine_task_total_uses_observed_trace_not_legacy_or_stage_count(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run = _run(Path(tmpdir), {"workflow_stage": "qc_preview", "analysis_mode": "qc_only"})
            run.engine_execution = {
                "descriptor": {"engine_id": "fixture_engine", "stages": [
                    {"id": "native", "label": "Native", "process_patterns": ["FIXTURE_NATIVE"]},
                    {"id": "preview", "label": "Preview", "process_patterns": ["PACKAGE_PREVIEW"]},
                ]},
                "native_outputs": [],
            }
            trace_dir = Path(run.results_path) / "pipeline_info"
            trace_dir.mkdir()
            (trace_dir / "execution_trace.txt").write_text(
                "task_id\tname\tstatus\n"
                "1\tFIXTURE_NATIVE (A)\tCOMPLETED\n"
                "2\tFIXTURE_NATIVE (B)\tCACHED\n"
                "3\tPACKAGE_PREVIEW\tCOMPLETED\n", encoding="utf-8",
            )

            payload = StatusReader().build_status_payload(run, phase="succeeded")

        self.assertEqual(payload["estimated_total_tasks"], 0)
        self.assertEqual(payload["total_tasks"], 3)
        self.assertEqual(payload["completed_tasks"], 3)
        self.assertEqual(payload["observed_tasks"], 3)
        self.assertEqual(payload["task_progress_source"], "nextflow_trace")
        self.assertEqual(payload["process_states"], {"FIXTURE_NATIVE": "done", "PACKAGE_PREVIEW": "done"})

    def test_fixture_engine_without_trace_has_no_legacy_estimate(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run = _run(Path(tmpdir), {"workflow_stage": "full"})
            run.engine_execution = {"descriptor": {"engine_id": "fixture_engine"}, "native_outputs": []}

            payload = StatusReader().build_status_payload(run, phase="submitted")

        self.assertEqual(payload["estimated_total_tasks"], 0)
        self.assertEqual(payload["total_tasks"], 0)
        self.assertFalse(payload["task_progress_available"])
        self.assertEqual(payload["task_progress_source"], "unavailable")

    def test_fixture_engine_counts_unique_native_and_package_primary_files(self) -> None:
        for nested in (False, True):
            with self.subTest(nested=nested), TemporaryDirectory() as tmpdir:
                run = _run(Path(tmpdir), {"workflow_stage": "qc_preview"})
                run.engine_execution = {
                    "descriptor": {"engine_id": "fixture_engine"},
                    "native_outputs": [("native/*.tsv", "fixture_table"), ("native/aligned.tsv", "fixture_table")],
                }
                results = Path(run.results_path)
                (results / "native").mkdir()
                (results / "native" / "aligned.tsv").write_text("feature_id\n")
                (results / "native" / "evidence.tsv").write_text("feature_id\n")
                (results / "native" / "directory.tsv").mkdir()
                package = results / "metabolensr" / ("aligned" if nested else "")
                for relative in ("preview/qc_preview.rds", "final/metabolensr_result.rds", "final/qc_report.html",
                                 "preview/feature_audit.tsv", "final/metabolensr_manifest.json"):
                    path = package / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("fixture")

                payload = StatusReader().build_status_payload(run, phase="succeeded")

                self.assertEqual(payload["files"]["primary_output_count"], 5)

    def test_trace_summary_counts_completed_cached_and_failed_tasks(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _run(root, {"workflow_stage": "hpc_smoke"})
            trace_dir = Path(run.results_path) / "pipeline_info"
            trace_dir.mkdir(parents=True, exist_ok=True)
            (trace_dir / "execution_trace.txt").write_text(
                "task_id\tname\tstatus\n"
                "1\tprepare_msdial5_input\tCOMPLETED\n"
                "2\tpeak_detection_msdial5\tCACHED\n"
                "3\tadapt_mdalign_to_stats_txt\tFAILED\n",
                encoding="utf-8",
            )

            payload = StatusReader().build_status_payload(run, phase="running")

        self.assertEqual(payload["completed_tasks"], 2)
        self.assertEqual(payload["cached_tasks"], 1)
        self.assertEqual(payload["failed_tasks"], 1)
        self.assertEqual(payload["observed_tasks"], 3)
        self.assertEqual(payload["total_tasks"], 3)
        self.assertTrue(payload["task_progress_available"])

    def test_missing_trace_still_reports_estimated_total_tasks(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run = _run(Path(tmpdir), {"workflow_stage": "hpc_smoke"})

            payload = StatusReader().build_status_payload(run, phase="submitted")

        self.assertEqual(payload["completed_tasks"], 0)
        self.assertEqual(payload["total_tasks"], 2)
        self.assertEqual(payload["task_progress_source"], "estimated")
        self.assertFalse(payload["task_progress_available"])

    def test_is_qc_review_primary_outputs_follow_is_qc_artifacts(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            is_qc_dir = root / "results" / "is-qc"
            is_qc_dir.mkdir(parents=True)
            (is_qc_dir / "is_qc_summary.json").write_text("{}", encoding="utf-8")
            (is_qc_dir / "is_qc_targets.tsv").write_text("target\n", encoding="utf-8")
            (is_qc_dir / "is_qc_traces.json").write_text("{}", encoding="utf-8")
            run = _run(root, {"workflow_stage": "is_qc_review"})

            payload = StatusReader().build_status_payload(run, phase="running")

        self.assertEqual(payload["estimated_total_tasks"], 0)
        self.assertEqual(payload["files"]["primary_output_count"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
