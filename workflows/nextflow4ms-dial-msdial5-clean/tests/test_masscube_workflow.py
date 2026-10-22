"""Run the workflow graph with command-boundary scientific executables."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

WORKFLOW = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("nextflow"), "requires local Nextflow")
class MassCubeWorkflowTests(unittest.TestCase):
    def test_native_bundle_companion_and_resume_preserve_upstream_cache(self):
        self.assertTrue((WORKFLOW / "main_masscube.nf").is_file(), "MassCube workflow entrypoint missing")
        with tempfile.TemporaryDirectory(prefix="masscube-workflow-", dir="/private/tmp") as temp:
            root = Path(temp); bin_dir = root / "bin"; bin_dir.mkdir()
            (root / "inputs").mkdir()
            (root / "inputs/a.mzML").write_text("a")
            (root / "inputs/b.mzml").write_text("b")
            (root / "samplesheet.csv").write_text("acquisition_id,staged_filename\nfile-1,a.mzML\nfile-2,b.mzml\n")
            (root / "science.json").write_text("{}")
            (root / "package.json").write_text('{"profile":"strict","source_tool":"masscube"}')
            (root / "overrides.tsv").write_text("initial")
            (root / "local.config").write_text("docker.enabled = false\nprocess.executor = 'local'\nprocess.memory = '128 MB'\ntrace.enabled = true\ntrace.overwrite = true\ntrace.file = 'trace.tsv'\n")
            # The real native runner is separately exercised against real caches.
            # Replace only scientific executables to test Nextflow I/O and resume.
            (bin_dir / "python3").write_text('''#!/bin/bash
set -eu
test "$(basename "$1")" = masscube_runner.py
shift
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) destination="$2" ;;
    --inputs) inputs="$2" ;;
    --samplesheet) sheet="$2" ;;
    --metabolensr-config) config="$2" ;;
    --config) test -f "$2" ;;
    --library) echo 'No library selected for this test' >&2; exit 23 ;;
  esac
  shift 2
done
test -f "$inputs/a.mzML"
test -f "$inputs/b.mzml"
mkdir -p "$destination/cache" "$destination/single_files"
cp "$sheet" "$destination/samplesheet.csv"
cp "$config" "$destination/metabolensr_config.json"
echo aligned > "$destination/aligned_feature_table.tsv"
echo acquisition > "$destination/acquisition_evidence.tsv"
echo '{}' > "$destination/source_metadata.json"
echo '{}' > "$destination/evidence.index.json"
echo '{}' > "$destination/evidence.jsonl"
echo native > "$destination/single_files/a.txt"
echo cache > "$destination/cache/a.mzpkl"
''')
            (bin_dir / "metabolensr").write_text('''#!/bin/bash
set -eu
if [ "$1" = preview ]; then
  test -f input.txt
  test -f samplesheet.csv
  test -f acquisition_evidence.tsv
  test -f config.json
  mkdir preview
  echo preview > preview/qc_preview.rds
else
  test -f preview/qc_preview.rds
  test -f overrides.tsv
  mkdir final
  cp overrides.tsv final/result.txt
fi
''')
            for executable in bin_dir.iterdir():
                executable.chmod(0o755)
            env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
            command = ["nextflow", "-c", str(root / "local.config"), "run", str(WORKFLOW / "main_masscube.nf"),
                       "-ansi-log", "false", "--input_dir", str(root / "inputs"),
                       "--samplesheet", str(root / "samplesheet.csv"), "--masscube_config", str(root / "science.json"),
                       "--metabolensr_config", str(root / "package.json"), "--metabolensr_overrides", str(root / "overrides.tsv"),
                       "--masscube_container_image", "fixture", "--metabolensr_container_image", "fixture",
                       "--thermorawfileparser_image", "fixture", "--outdir", str(root / "results")]
            first = subprocess.run(command + ["--workflow_stage", "qc_preview"], cwd=root, env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertTrue((root / "results/masscube/evidence.index.json").is_file())
            self.assertTrue((root / "results/metabolensr/aligned/preview/qc_preview.rds").is_file())
            self.assertFalse((root / "results/metabolensr/aligned/final").exists())
            native = root / "results/masscube"
            # Publication must not replace files already registered or held open
            # by readers, including when recovering an incomplete earlier copy.
            for path in [native, *native.rglob("*")]:
                os.utime(path, (1000000000, 1000000000))
            def native_state():
                return {str(path.relative_to(native)): (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
                        for path in native.rglob("*") if path.is_file()}
            original_native = native_state()
            missing = native / "single_files/a.txt"
            missing.unlink()
            retained = native_state()
            (root / "overrides.tsv").write_text("reviewed")
            second = subprocess.run(command + ["--workflow_stage", "full", "-resume"], cwd=root, env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual((root / "results/metabolensr/aligned/final/result.txt").read_text(), "reviewed")
            after_review = native_state()
            self.assertEqual({name: after_review[name] for name in retained}, retained,
                             "Review continuation must preserve native file inodes, timestamps, and content")
            self.assertEqual(missing.read_bytes(), original_native["single_files/a.txt"][2])
            trace = (root / "trace.tsv").read_text().splitlines()
            for process in ("prepare_acquisition_input", "MASSCUBE_PROCESS", "METABOLENSR_QC:METABOLENSR_PREVIEW"):
                lines = [line for line in trace if process in line]
                self.assertEqual(len(lines), 1, trace)
                self.assertIn("CACHED", lines[0])
            retry_missing = native / "cache/a.mzpkl"
            retry_missing.unlink()
            retained = native_state()
            retry = subprocess.run(command + ["--workflow_stage", "full", "-resume"], cwd=root, env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
            after_retry = native_state()
            self.assertEqual({name: after_retry[name] for name in retained}, retained,
                             "Retry must preserve existing native files while restoring missing ones")
            self.assertEqual(retry_missing.read_bytes(), original_native["cache/a.mzpkl"][2])
            self.assertEqual(set(after_retry), set(original_native))
            retry_trace = (root / "trace.tsv").read_text().splitlines()
            for process in ("prepare_acquisition_input", "MASSCUBE_PROCESS", "METABOLENSR_QC:METABOLENSR_PREVIEW", "METABOLENSR_QC:METABOLENSR_FINALIZE"):
                lines = [line for line in retry_trace if process in line]
                self.assertEqual(len(lines), 1, retry_trace)
                self.assertIn("CACHED", lines[0])


if __name__ == "__main__":
    unittest.main()
