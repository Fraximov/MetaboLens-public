"""Executable staging checks; run with Python unittest and local Nextflow."""
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


WORKFLOW = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("nextflow"), "requires local Nextflow")
class SharedWorkflowStagingTests(unittest.TestCase):
    def run_nextflow(self, root, source, *arguments):
        (root / "test.nf").write_text(source)
        (root / "nextflow.config").write_text("process.executor = 'local'\n")
        result = subprocess.run(
            ["nextflow", "-C", str(root / "nextflow.config"), "run", str(root / "test.nf"),
             "-ansi-log", "false", *arguments],
            cwd=root, capture_output=True, text=True, timeout=90,
            env={**os.environ, "NXF_OFFLINE": "true",
                 "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"]},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_shared_preparation_copies_exact_filenames_without_engine_config(self):
        with TemporaryDirectory(prefix="acquisition-staging-") as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            for name in ("sample one.mzml", "sample's two.mzML", "sample three.RAW", "legacy.abf", "ignored.txt"):
                (inputs / name).write_text(name)
            (root / "msdial.txt").write_text("legacy configuration\n")
            (root / "bin").mkdir()
            converter = root / "bin/ThermoRawFileParser"
            converter.write_text('''#!/usr/bin/env python3
import pathlib, sys
args = dict(arg.split('=', 1) for arg in sys.argv[1:])
source = pathlib.Path(args['-i'])
assert args['-f'] == '1' and args['-l'] == '1'
(pathlib.Path(args['-o']) / (source.stem + '.mzML')).write_text('converted ' + source.read_text())
''')
            converter.chmod(0o755)
            module = (WORKFLOW / "modules/acquisition/main").as_posix()
            legacy_module = (WORKFLOW / "modules/msdial/main").as_posix()
            source = f"""nextflow.enable.dsl = 2
params.thermorawfileparser_image = null
include {{ prepare_acquisition_input }} from '{module}'
include {{ prepare_msdial5_input }} from '{legacy_module}'
workflow {{
    acquisitions = channel.fromPath('{inputs.as_posix()}/*').collect()
    prepare_acquisition_input(acquisitions)
    prepare_acquisition_input.out.prepared_data.view {{ folder ->
        assert folder.resolve('sample one.mzml').text == 'sample one.mzml'
        assert folder.resolve("sample's two.mzML").text == "sample's two.mzML"
        assert folder.resolve('sample three.mzML').text == 'converted sample three.RAW'
        assert folder.resolve('legacy.abf').text == 'legacy.abf'
        assert !folder.resolve('ignored.txt').exists()
        'prepared inputs verified'
    }}
    prepare_msdial5_input(acquisitions, channel.value(file('msdial.txt')))
    prepare_msdial5_input.out.prepared_msdial_config.view {{ config ->
        assert config.text == 'legacy configuration\\n'
        'legacy configuration verified'
    }}
    prepare_msdial5_input.out.prepared_data.view {{ folder ->
        assert folder.resolve('sample three.mzML').text == 'converted sample three.RAW'
        'legacy preparation verified'
    }}
}}
"""
            output = self.run_nextflow(root, source)
            self.assertIn("prepared inputs verified", output)
            self.assertIn("legacy configuration verified", output)
            self.assertIn("legacy preparation verified", output)

    def test_preview_stages_companions_and_resume_preserves_both_input_contracts(self):
        with TemporaryDirectory(prefix="package-staging-") as temporary:
            root = Path(temporary)
            (root / "table.tsv").write_text("native table\n")
            (root / "samples.csv").write_text("acquisition_id\nfile-1\n")
            (root / "evidence.tsv").write_text("source_feature_id\n001\n")
            (root / "config.json").write_text("{}\n")
            (root / "overrides.tsv").write_text("automatic\n")
            (root / "bin").mkdir()
            executable = root / "bin/metabolensr"
            executable.write_text('''#!/usr/bin/env python3
import pathlib, sys
args = sys.argv[1:]
def option(name):
    return pathlib.Path(args[args.index(name) + 1])
output = option('--outdir')
output.mkdir()
if args[0] == 'preview':
    assert option('--input').read_text() == 'native table\\n'
    assert option('--samplesheet').read_text() == 'acquisition_id\\nfile-1\\n'
    evidence = pathlib.Path('evidence.tsv')
    (output / 'qc_preview.rds').write_text(evidence.read_text() if evidence.exists() else 'legacy')
else:
    preview = option('--preview').read_text()
    (output / 'result.txt').write_text(preview + option('--overrides').read_text())
''')
            executable.chmod(0o755)
            module = (WORKFLOW / "modules/metabolensr/main").as_posix()
            source = f"""nextflow.enable.dsl = 2
params.metabolensr_container_image = null
params.preview_only = true
params.outdir = '{(root / 'results').as_posix()}'
include {{ METABOLENSR_QC }} from '{module}'
workflow {{
    inputs = channel.of(
        tuple('legacy', file('table.tsv'), file('samples.csv')),
        tuple('native', file('table.tsv'), file('samples.csv'), [file('evidence.tsv')]))
    METABOLENSR_QC(inputs, channel.value(file('config.json')),
        channel.value(file('overrides.tsv')), params.preview_only)
}}
"""
            self.run_nextflow(root, source)
            base = root / "results/metabolensr"
            self.assertEqual((base / "native/preview/qc_preview.rds").read_text(), "source_feature_id\n001\n")
            self.assertEqual((base / "legacy/preview/qc_preview.rds").read_text(), "legacy")
            self.assertFalse((base / "native/final").exists())
            (root / "overrides.tsv").write_text("reviewed\n")
            output = self.run_nextflow(root, source, "-resume", "--preview_only", "false")
            self.assertEqual(output.count("Cached process > METABOLENSR_QC:METABOLENSR_PREVIEW"), 2)
            self.assertEqual((base / "native/final/result.txt").read_text(), "source_feature_id\n001\nreviewed\n")
            self.assertEqual((base / "legacy/final/result.txt").read_text(), "legacyreviewed\n")


if __name__ == "__main__":
    unittest.main()
