import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class WorkflowBooleanParamTests(unittest.TestCase):
    @unittest.skipIf(Path("/.dockerenv").exists(), "Nextflow preview does not terminate inside the worker test container")
    def test_string_false_does_not_enable_sirius(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        pipeline = repo_root / "workflows" / "nextflow4ms-dial-msdial5-clean"

        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            input_dir = run_dir / "input"
            input_dir.mkdir()

            sample = input_dir / "sample.mzML"
            msdial_config = run_dir / "msdial.txt"
            ms1_library = run_dir / "ms1.txt"
            ms2_library = run_dir / "ms2.msp"
            samplesheet = run_dir / "samplesheet.csv"
            override_config = run_dir / "nextflow.override.config"

            sample.write_text("", encoding="utf-8")
            msdial_config.write_text("", encoding="utf-8")
            ms1_library.write_text("", encoding="utf-8")
            ms2_library.write_text("", encoding="utf-8")
            samplesheet.write_text(
                "sample_id,file_name,group_label\n"
                "sample,sample.mzML,test\n",
                encoding="utf-8",
            )
            override_config.write_text("params.enable_sirius = 'false'\n", encoding="utf-8")

            environment = os.environ.copy()
            environment.update(
                {
                    "NXF_ANSI_LOG": "false",
                    "NXF_OFFLINE": "true",
                }
            )
            completed = subprocess.run(
                [
                    "nextflow",
                    "run",
                    str(pipeline / "main_msdial5.nf"),
                    "-preview",
                    "-c",
                    str(override_config),
                    "--input_dir",
                    str(input_dir),
                    "--msdial_config",
                    str(msdial_config),
                    "--ms1_library",
                    str(ms1_library),
                    "--ms2_library",
                    str(ms2_library),
                    "--samplesheet",
                    str(samplesheet),
                    "--outdir",
                    str(run_dir / "results"),
                ],
                cwd=run_dir,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            output = completed.stdout + completed.stderr
            self.assertEqual(completed.returncode, 0, output)
            self.assertNotIn("enable_sirius=true selected", output)


if __name__ == "__main__":
    unittest.main()
