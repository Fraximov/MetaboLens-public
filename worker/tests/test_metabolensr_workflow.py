"""Opt-in real Nextflow/package acceptance, using a previously materialized run."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest


class MetabolensrTaskEnvironmentTests(unittest.TestCase):
    def test_finalize_replaces_unmounted_scheduler_tmpdir(self):
        workflow = Path(__file__).resolve().parents[2] / 'workflows/nextflow4ms-dial-msdial5-clean'
        source = (workflow / 'modules/metabolensr/main.nf').read_text()
        script = source.split('process METABOLENSR_FINALIZE {', 1)[1].split('"""', 2)[1]
        script = script.replace('\\$', '$')
        with TemporaryDirectory() as temp:
            root = Path(temp)
            executable = root / 'metabolensr'
            executable.write_text('#!/bin/sh\nset -eu\ntest -d "$TMPDIR"\ntest "$TMPDIR" = "$PWD/.tmp"\ntouch "$TMPDIR/report-probe"\n')
            executable.chmod(0o755)
            env = {**os.environ, 'TMPDIR': str(root / 'unmounted-slurm-temp'),
                   'PATH': str(root) + os.pathsep + os.environ['PATH']}
            result = subprocess.run(['bash', '-eu', '-c', script], cwd=root, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / '.tmp/report-probe').is_file())


@unittest.skipUnless(os.environ.get('NEXTFLOW_PUBLISH_ACCEPTANCE'), 'requires local Nextflow')
class NativePublishResumeTests(unittest.TestCase):
    def test_resume_skips_identical_native_output_but_repairs_changed_content(self):
        workflow = Path(__file__).resolve().parents[2] / 'workflows/nextflow4ms-dial-msdial5-clean'
        output = (workflow / 'main_metabolensr.nf').read_text().split('\noutput {', 1)[1]
        with TemporaryDirectory(prefix='metabolens-publish-') as temp:
            root = Path(temp)
            (root / 'main.nf').write_text('''process MAKE {
    output:
    path 'native.txt'
    script:
    "echo original > native.txt"
}
workflow {
    main:
    MAKE()
    publish:
    msdial_artifacts = MAKE.out
    is_qc_artifacts = channel.empty()
    rt_correction_artifacts = channel.empty()
    sirius_outputs = channel.empty()
}
output {''' + output)
            (root / 'nextflow.config').write_text("workflow.output.mode = 'copy'\n")
            command = ['nextflow', 'run', 'main.nf', '-ansi-log', 'false']
            def execute(resume=False):
                result = subprocess.run(command + (['-resume'] if resume else []), cwd=root,
                                        capture_output=True, text=True, timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            execute()
            published = root / 'results/native.txt'
            os.utime(published, (1000000000, 1000000000))
            execute(resume=True)
            self.assertEqual(published.stat().st_mtime, 1000000000,
                             'Identical published files must not be replaced on resume')
            published.write_text('modified')
            execute(resume=True)
            self.assertEqual(published.read_text().strip(), 'original')


@unittest.skipUnless(os.environ.get('METABOLENSR_ACCEPTANCE_RUN'), 'requires real package containers and fixture run')
class MetabolensrWorkflowTests(unittest.TestCase):
    def test_two_aligned_bundles_publish_independently(self):
        fixture = Path(os.environ['METABOLENSR_ACCEPTANCE_RUN'])
        workflow = Path(__file__).resolve().parents[2] / 'workflows/nextflow4ms-dial-msdial5-clean'
        descriptor = json.loads((workflow / 'engines/msdial5/engine.json').read_text())
        with TemporaryDirectory(prefix='metabolensr-bundles-', dir='/private/tmp') as temp:
            root = Path(temp)
            config = root / 'docker.config'
            config.write_text('docker.enabled = true\nprocess.executor = "local"\n')
            command = [
                'nextflow', '-C', str(config), 'run', str(workflow / 'tests/metabolensr_bundle_isolation.nf'),
                '-ansi-log', 'false', '-work-dir', str(root / 'work'),
                '--input', str(fixture / 'results/metabolensr/adapted.txt'),
                '--samplesheet', str(fixture / 'results/metabolensr/acquisitions.csv'),
                '--metabolensr_config', str(fixture / 'contract/metabolensr_config.json'),
                '--metabolensr_overrides', str(fixture / 'contract/feature_curation_overrides.tsv'),
                '--metabolensr_container_image', descriptor['package']['container_image'],
                '--outdir', str(root / 'results'),
            ]
            completed = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=300)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            for bundle in ('bundle-a', 'bundle-b'):
                for output in ('preview/qc_preview_summary.json', 'final/metabolensr_manifest.json', 'final/qc_report.html'):
                    self.assertTrue((root / 'results/metabolensr' / bundle / output).is_file(), f'{bundle}/{output} missing')
