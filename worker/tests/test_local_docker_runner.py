import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from config import WorkerSettings
from launchers.profile_runners.local_docker import LocalDockerProfileRunner
from services.materialized_run import MaterializedRun


class LocalDockerProfileRunnerTests(unittest.TestCase):
    def test_build_command_adds_resume_flag_without_forwarding_launcher_param(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = MaterializedRun(
                job_id=1,
                study_id=1,
                assay_id=1,
                workflow_definition_id=1,
                execution_profile="local_docker",
                pipeline_root=str(root / "pipeline"),
                entrypoint_path=str(root / "pipeline" / "main_msdial5.nf"),
                nextflow_profile="docker",
                run_name="test",
                run_root=str(root),
                staged_input_dir=str(root / "inputs" / "raw_data"),
                contract_dir=str(root / "contract"),
                results_path=str(root / "results"),
                logs_path=str(root / "logs"),
                work_path=str(root / "work"),
                params_path=str(root / "contract" / "params.json"),
                samplesheet_path=str(root / "contract" / "samplesheet.csv"),
                staged_msdial_config_path=str(root / "contract" / "msdial_config.txt"),
                nextflow_config_override_path=str(root / "contract" / "nextflow.override.config"),
                artifact_manifest_path=str(root / "contract" / "artifact-manifest.json"),
                reference_file_asset_id=1,
                reference_path=str(root / "inputs" / "raw_data" / "sample.raw"),
                ms1_library_path=str(root / "libraries" / "ms1.txt"),
                ms2_library_path=str(root / "libraries" / "ms2.msp"),
                is_library_path=str(root / "libraries" / "is.txt"),
                config_source="uploaded_file",
                metadata_snapshot={},
                method_metadata={},
                selected_files=[],
                nextflow_params={
                    "input_dir": str(root / "inputs" / "raw_data"),
                    "ref": str(root / "inputs" / "raw_data" / "sample.raw"),
                    "msdial_config": str(root / "contract" / "msdial_config.txt"),
                    "ms1_library": str(root / "libraries" / "ms1.txt"),
                    "ms2_library": str(root / "libraries" / "ms2.msp"),
                    "is_library": str(root / "libraries" / "is.txt"),
                    "samplesheet": str(root / "contract" / "samplesheet.csv"),
                    "stats_factor_column": "group_label",
                    "outdir": str(root / "results"),
                    "workflow_stage": "full",
                    "apply_rt_correction": "true",
                    "rt_correction_target_ids": '["1","3"]',
                    "nextflow_resume": "true",
                    "enable_sirius": "true",
                    "sirius_container_image": "metabolens-sirius:test",
                    "sirius_container_options": (
                        "-e HOME=/sirius-home "
                        "-v /tmp/sirius-home:/sirius-home"
                    ),
                    "sirius_annotation_mode": "full",
                    "sirius_structure_db": "ALL",
                    "sirius_ms2_tolerance": "5",
                    "sirius_ms1_tolerance": "2",
                    "sirius_structure_expansive_search": "APPROXIMATE",
                    "sirius_recompute": "true",
                },
            )

            command = LocalDockerProfileRunner(WorkerSettings(database_url="sqlite://")).build_command(run)

        self.assertIn("-resume", command)
        self.assertEqual(command[command.index("-resume") + 1], "test")
        self.assertNotIn("-name", command)
        self.assertIn("--workflow_stage", command)
        self.assertIn("full", command)
        self.assertIn("--apply_rt_correction", command)
        self.assertEqual(command[command.index("--apply_rt_correction") + 1], "true")
        self.assertEqual(
            command[command.index("--rt_correction_target_ids") + 1],
            '["1","3"]',
        )
        self.assertNotIn("--nextflow_resume", command)

        sirius_params = {
            "enable_sirius": "true",
            "sirius_container_image": "metabolens-sirius:test",
            "sirius_container_options": (
                "-e HOME=/sirius-home "
                "-v /tmp/sirius-home:/sirius-home"
            ),
            "sirius_annotation_mode": "full",
            "sirius_structure_db": "ALL",
            "sirius_ms2_tolerance": "5",
            "sirius_ms1_tolerance": "2",
            "sirius_structure_expansive_search": "APPROXIMATE",
            "sirius_recompute": "true",
        }
        for param, expected_value in sirius_params.items():
            option = f"--{param}"
            self.assertIn(option, command)
            self.assertEqual(command[command.index(option) + 1], expected_value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
