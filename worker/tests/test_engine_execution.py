"""Conformance checks for engines with no MS-DIAL input requirements."""
import csv
import hashlib
from copy import deepcopy
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from config import WorkerSettings
from launchers.profile_runners.local_docker import LocalDockerProfileRunner
from launchers.profile_runners.slurm_singularity import SlurmSingularityProfileRunner
from app.core.engine_contract import snapshot_engine
from app.models.models import WorkflowJob
from services.run_materializer import RunMaterializer
from backend.tests.test_engine_contract import fixture_descriptor


class EngineLauncherTests(unittest.TestCase):
    def test_msdial_adapter_preserves_deployment_sirius_auth_mount(self):
        from services.engines import EngineContext
        from services.engines.msdial import prepare_msdial
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = EngineContext(
                descriptor={}, parameters={}, manifest={"reference_file_asset_id": 7},
                workspace=SimpleNamespace(staged_msdial_config_path=root / "params.txt"),
                staged_files=[{"file_asset_id": 7, "staged_path": str(root / "input.raw")}],
                support_files={key: root / key for key in (
                    "msdial_config_path", "ms1_library_path", "ms2_library_path", "is_library_path")},
            )
            context.settings = WorkerSettings(database_url="sqlite://", workflow_sirius_container_options="--bind /shared/auth:/sirius-home")
            with patch("services.engines.msdial.MsdialConfigRenderer.render"):
                launch = prepare_msdial(context)
            self.assertEqual(launch.parameters["sirius_container_options"], "--bind /shared/auth:/sirius-home")

    def test_runners_forward_engine_parameters_without_requiring_msdial(self):
        run = SimpleNamespace(
            entrypoint_path='/workflow/fixture.nf', nextflow_profile='docker',
            nextflow_config_override_path='/run/config', work_path='/run/work',
            run_name='fixture', job_id=12,
            nextflow_params={'input_dir': '/run/input', 'fixture_threshold': '2.5',
                             'metabolensr_config': '/run/contract/package.json',
                             'outdir': '/run/results', 'nextflow_resume': 'true'},
        )
        settings = WorkerSettings(database_url='sqlite://')
        for command in (LocalDockerProfileRunner(settings).build_command,
                        SlurmSingularityProfileRunner(settings).build_nextflow_command):
            with self.subTest(runner=command.__qualname__):
                args = command(run)
                self.assertEqual(args[args.index('--fixture_threshold') + 1], '2.5')
                self.assertIn('--metabolensr_config', args)
                self.assertNotIn('--msdial_config', args)
                self.assertNotIn('--ref', args)
                self.assertNotIn('--nextflow_resume', args)
                self.assertIn('-resume', args)


class EngineMaterializationTests(unittest.TestCase):
    def test_staging_rejects_replaced_bytes_even_when_live_database_digest_changes(self):
        from services.engines import ENGINE_ADAPTERS
        with TemporaryDirectory() as temp:
            materializer, job, _, _ = self.setup_run(Path(temp))
            source = Path(temp) / 'sample.mzML'
            original = source.read_bytes()
            frozen = {'file_asset_id': 42, 'checksum_sha256': hashlib.sha256(original).hexdigest(),
                      'file_size_bytes': len(original)}
            job.input_manifest_json['selected_files'] = [frozen]
            source.write_bytes(b'x' * len(original))
            live = materializer.load_selected_files([42])[0]
            live.checksum_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            live.file_size_bytes = source.stat().st_size
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}), self.assertRaisesRegex(ValueError, 'checksum'):
                materializer.prepare(job)
            self.assertFalse((Path(temp) / 'runs/job_7/inputs/raw_data/sample.mzML').exists())

    def test_frozen_size_is_checked_without_a_historical_checksum(self):
        from services.engines import ENGINE_ADAPTERS
        with TemporaryDirectory() as temp:
            materializer, job, _, _ = self.setup_run(Path(temp))
            job.input_manifest_json['selected_files'] = [
                {'file_asset_id': 42, 'checksum_sha256': None, 'file_size_bytes': 1}]
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}), self.assertRaisesRegex(ValueError, 'size'):
                materializer.prepare(job)

    def test_legacy_null_identity_records_observed_digest_without_claiming_launch_verification(self):
        from services.engines import ENGINE_ADAPTERS
        with TemporaryDirectory() as temp:
            materializer, job, _, _ = self.setup_run(Path(temp))
            job.input_manifest_json['selected_files'] = [
                {'file_asset_id': 42, 'checksum_sha256': None, 'file_size_bytes': None}]
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}):
                run = materializer.prepare(job)
            staged = run.selected_files[0]
            self.assertIs(staged.get('launch_checksum_verified'), False)
            self.assertEqual(staged['checksum_sha256'], hashlib.sha256(b'fixture measurement').hexdigest())
            self.assertEqual(staged['file_size_bytes'], len(b'fixture measurement'))

    def test_verified_input_remains_frozen_on_review_resume(self):
        from services.engines import ENGINE_ADAPTERS
        with TemporaryDirectory() as temp:
            materializer, job, _, _ = self.setup_run(Path(temp))
            job.input_manifest_json['selected_files'] = [
                {'file_asset_id': 42, 'checksum_sha256': hashlib.sha256(b'fixture measurement').hexdigest(),
                 'file_size_bytes': len(b'fixture measurement')}]
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}):
                run = materializer.prepare(job)
            self.assertIs(run.selected_files[0].get('launch_checksum_verified'), True)
            job.run_root = run.run_root
            job.parameter_values_json = {**job.parameter_values_json, 'nextflow_resume': True, 'workflow_stage': 'full'}
            resumed = materializer.prepare(job)
            self.assertEqual(resumed.selected_files, run.selected_files)
            Path(run.selected_files[0]['staged_path']).write_text('changed input')
            with self.assertRaisesRegex(ValueError, 'Frozen workflow contract changed'):
                materializer.prepare(job)

    def test_selected_profile_reaches_package_config_and_survives_catalog_changes(self):
        from services.engines import ENGINE_ADAPTERS
        with TemporaryDirectory() as temp:
            materializer, job, definition, pipeline = self.setup_run(Path(temp))
            package = definition.static_input_config_json['engine']['package']
            package['curation_profiles'] = {
                name: {'name': name, 'statistics': [], 'annotation': []}
                for name in ('strict', 'fixture')}
            package['supports_rule_selection'] = True
            job.input_manifest_json['engine'] = snapshot_engine(
                definition, {}, pipeline, metabolensr_profile='fixture', metabolensr_enabled_rules={'statistics': []})
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}):
                run = materializer.prepare(job)
            config = Path(run.nextflow_params['metabolensr_config'])
            self.assertEqual(json.loads(config.read_text())['profile'], 'fixture')
            self.assertNotIn('metabolensr_profile', run.nextflow_params)
            self.assertEqual(json.loads(config.read_text())['enabled_rules'], {'statistics': []})
            self.assertNotIn('enabled_rules', run.nextflow_params)
            before = config.read_bytes()
            package['curation_profiles'].pop('fixture')
            job.run_root = run.run_root
            job.parameter_values_json = {**job.parameter_values_json, 'nextflow_resume': True, 'workflow_stage': 'full'}
            resumed = materializer.prepare(job)
            self.assertEqual(Path(resumed.nextflow_params['metabolensr_config']).read_bytes(), before)

    def test_slurm_images_are_verified_and_frozen_for_continuation(self):
        from app.core.engine_contract import sha256_file
        from services.engines import ENGINE_ADAPTERS
        with TemporaryDirectory() as temp:
            root = Path(temp)
            materializer, job, definition, _ = self.setup_run(root, workflow_local_runs_root=str(root / 'local-runs'))
            job.execution_profile = 'slurm_singularity_ssh'
            job.input_manifest_json['execution_profile'] = job.execution_profile
            job.input_manifest_json['nextflow_profile'] = 'embl_slurm'
            descriptor = job.input_manifest_json['engine']['descriptor']
            descriptor['execution_profiles'].append(job.execution_profile)
            image = root / 'package.sif'
            image.write_bytes(b'original container')
            mapping = root / 'images.json'
            mapping.write_text(json.dumps({descriptor['package']['container_image']:
                {'path': str(image), 'sha256': sha256_file(image)}}))
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}), self.assertRaisesRegex(ValueError, 'MANIFEST is required'):
                materializer.prepare(job)
            materializer.settings.workflow_singularity_image_manifest = str(mapping)
            image.write_bytes(b'wrong initial container')
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}):
                with self.assertRaisesRegex(ValueError, 'container.*changed'):
                    materializer.prepare(job)
            image.write_bytes(b'original container')
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}):
                run = materializer.prepare(job)
            self.assertEqual(Path(run.run_root), root / 'runs' / 'job_7')
            self.assertEqual(run.nextflow_params['metabolensr_container_image'], str(image))
            job.run_root = run.run_root
            job.parameter_values_json = {**job.parameter_values_json, 'nextflow_resume': True, 'workflow_stage': 'full'}
            mapping.unlink()
            job.input_manifest_json['submission_attempt'] = 1
            resumed = materializer.prepare(job)
            self.assertEqual(resumed.submission_attempt, 1)
            self.assertEqual(materializer.load_existing(job).submission_attempt, 1)
            self.assertEqual(resumed.nextflow_params['metabolensr_container_image'], str(image))
            image.write_bytes(b'replaced container')
            with self.assertRaisesRegex(ValueError, 'container.*changed'):
                materializer.prepare(job)

    def test_local_storage_is_separate_from_cluster_default_and_frozen_on_resume(self):
        from services.engines import ENGINE_ADAPTERS
        with TemporaryDirectory() as temp:
            root = Path(temp)
            materializer, job, definition, _ = self.setup_run(root, workflow_local_runs_root=str(root / 'local-runs'))
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}):
                run = materializer.prepare(job)
            self.assertEqual(Path(run.run_root), root / 'local-runs' / 'job_7')
            job.run_root = run.run_root
            job.parameter_values_json = {**job.parameter_values_json, 'nextflow_resume': True, 'workflow_stage': 'full'}
            materializer.settings.workflow_local_runs_root = str(root / 'different-local-root')
            resumed = RunMaterializer(None, materializer.settings).prepare(job)
            self.assertEqual(resumed.run_root, run.run_root)

    def setup_run(self, root, **settings_values):
        settings_values.setdefault('workflow_local_runs_root', '')
        pipeline = root / 'pipeline'; pipeline.mkdir()
        (pipeline / 'fixture.nf').write_text('// original workflow')
        source = root / 'sample.mzML'; source.write_text('fixture measurement')
        descriptor = fixture_descriptor()
        schema = {'type': 'object', 'properties': {'threshold': {'type': 'number', 'default': 2},
                  'workflow_stage': {'type': 'string', 'enum': ['qc_preview', 'full'], 'default': 'qc_preview'}}, 'additionalProperties': False}
        definition = SimpleNamespace(static_input_config_json={'engine': descriptor}, parameter_schema_json=schema)
        engine = snapshot_engine(definition, {}, pipeline)
        job = WorkflowJob(id=7, study_id=1, assay_id=2, workflow_definition_id=3,
                          execution_profile='local_docker', parameter_values_json=engine['parameters'],
                          input_manifest_json={'engine': engine, 'pipeline_root': str(pipeline), 'file_ids': [42],
                              'nextflow_profile': 'docker', 'execution_profile': 'local_docker', 'metadata_submission_id': 1})
        materializer = RunMaterializer(None, WorkerSettings(database_url='sqlite://', workflow_runs_root=str(root / 'runs'), **settings_values))
        file = SimpleNamespace(id=42, sample_id=17, original_filename='sample.mzML', storage_uri=source.as_uri(),
                               source_scheme='file', ingest_mode='registered_path')
        materializer.load_selected_files = lambda ids: [file]
        materializer.load_metadata_submission = lambda manifest: SimpleNamespace(id=1, normalized_payload_json={})
        materializer.samplesheet_builder.build_rows = lambda files, ref, **kwargs: [{'file_asset_id': 42, 'sample_id': 17, 'sample_name': 'A'}]
        return materializer, job, definition, pipeline

    @staticmethod
    def adapter(context):
        from services.engines import EngineLaunch
        artifact = context.workspace.contract_dir / 'fixture-config.txt'
        artifact.write_text(str(context.parameters['threshold']))
        return EngineLaunch(parameters={'fixture_config': str(artifact), 'fixture_threshold': context.parameters['threshold']},
                            artifacts=[(artifact, 'fixture_config')], native_outputs=[('*.fixture.tsv', 'fixture_table')])

    def test_fixture_engine_materializes_without_msdial_and_resumes_frozen_contract(self):
        from services.engines import ENGINE_ADAPTERS
        with TemporaryDirectory() as temp:
            materializer, job, definition, pipeline = self.setup_run(Path(temp))
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}), patch('services.msdial_config.MsdialConfigRenderer.render', side_effect=AssertionError('MS-DIAL must not be invoked')):
                run = materializer.prepare(job)
            self.assertNotIn('msdial_config', run.nextflow_params)
            self.assertNotIn('ref', run.nextflow_params)
            self.assertEqual(run.nextflow_params['fixture_threshold'], '2')
            self.assertEqual(Path(run.entrypoint_path).read_text(), '// original workflow')
            config = Path(run.nextflow_params['metabolensr_config'])
            self.assertEqual(json.loads(config.read_text())['source_tool'], 'fixture_engine')
            before = config.read_bytes()
            # A later deployment changes both the source and registered implementation.
            (pipeline / 'fixture.nf').write_text('// changed deployment')
            definition.static_input_config_json['engine']['adapter_id'] = 'fixture-v2'
            job.run_root = run.run_root
            job.parameter_values_json = {**job.parameter_values_json, 'nextflow_resume': True, 'workflow_stage': 'full'}
            with patch.dict(ENGINE_ADAPTERS, {}, clear=True):
                resumed = materializer.prepare(job)
            self.assertEqual(resumed.entrypoint_path, run.entrypoint_path)
            self.assertEqual(Path(resumed.entrypoint_path).read_text(), '// original workflow')
            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(resumed.nextflow_params['workflow_stage'], 'full')
            self.assertEqual(materializer.load_existing(job), resumed)
            from services.artifact_collector import ArtifactCollector
            result = Path(run.results_path) / 'aligned.fixture.tsv'
            result.write_text('feature_id\tmz\nfeature-A\t100\n')
            # Overlapping declarations still represent one native output file.
            resumed.engine_execution['native_outputs'].append(('aligned.fixture.tsv', 'fixture_table'))
            collection = ArtifactCollector().collect(resumed, status_payload={}, exit_code=0)
            records = collection.output_records
            self.assertIn('fixture_table', {row['artifact_role'] for row in records})
            self.assertIn('metabolensr_config', {row['artifact_role'] for row in records})
            self.assertEqual(collection.result_summary['primary_output_count'], 1)
            manifest = json.loads(Path(resumed.artifact_manifest_path).read_text())
            self.assertEqual(manifest['summary']['primary_output_count'], 1)

    def test_queued_job_rejects_changed_sources_or_unavailable_adapter(self):
        from services.engines import ENGINE_ADAPTERS
        for damage in ('source', 'adapter'):
            with self.subTest(damage=damage), TemporaryDirectory() as temp:
                materializer, job, definition, pipeline = self.setup_run(Path(temp))
                if damage == 'source': (pipeline / 'fixture.nf').write_text('// changed')
                with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter} if damage == 'source' else {}, clear=True):
                    with self.assertRaisesRegex(ValueError, 'changed|unavailable'):
                        materializer.prepare(job)

    def test_review_resume_uses_frozen_override_and_preserves_upstream(self):
        from app.core.engine_contract import sha256_file
        from services.engines import ENGINE_ADAPTERS
        with TemporaryDirectory() as temp:
            materializer, job, definition, _ = self.setup_run(Path(temp))
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}):
                run = materializer.prepare(job)
            original = {p: Path(run.run_root, p).read_bytes() for p in run.engine_execution['contract_sha256']}
            frozen = Path(temp) / 'app-storage' / 'review-1.tsv'
            frozen.parent.mkdir()
            frozen.write_text('feature_id\toperator_override_statistics\toperator_override_annotation\n0\tinclude\tautomatic\n')
            job.run_root = run.run_root
            job.input_manifest_json['feature_review'] = {'path':str(frozen), 'sha256':sha256_file(frozen), 'revision':1}
            job.parameter_values_json = {**job.parameter_values_json, 'nextflow_resume': True, 'workflow_stage':'full'}
            resumed = materializer.prepare(job)
            staged = Path(run.run_root) / 'contract' / 'curation' / frozen.name
            self.assertEqual(resumed.nextflow_params['metabolensr_overrides'], str(staged))
            self.assertEqual(staged.read_bytes(), frozen.read_bytes())
            self.assertEqual({p: Path(run.run_root, p).read_bytes() for p in original}, original)
            self.assertEqual(materializer.prepare(job).nextflow_params['metabolensr_overrides'], str(staged))
            staged.write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'override.*changed'):
                materializer.prepare(job)
            staged.write_bytes(frozen.read_bytes())
            frozen.write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'override.*changed'):
                materializer.prepare(job)

    def test_independent_review_pauses_preserve_frozen_run_and_rt_choices(self):
        from app.core.engine_contract import sha256_file
        from services.engines import ENGINE_ADAPTERS

        for profile in ('local_docker', 'slurm_singularity_ssh'):
            for stages in (['is_qc_review', 'qc_preview'], ['is_qc_review']):
                with self.subTest(profile=profile, stages=stages), TemporaryDirectory() as temp:
                    root = Path(temp)
                    materializer, job, definition, pipeline = self.setup_run(root)
                    engine = job.input_manifest_json['engine']
                    engine['parameter_schema']['properties']['workflow_stage']['enum'].append('is_qc_review')
                    engine['parameters']['workflow_stage'] = 'is_qc_review'
                    engine['descriptor']['review_transitions'] = {'is_qc_review': 'qc_preview', 'qc_preview': 'full'}
                    job.parameter_values_json = dict(engine['parameters'])
                    job.input_manifest_json['review_stages'] = stages
                    job.execution_profile = profile
                    job.input_manifest_json['execution_profile'] = profile
                    if profile == 'slurm_singularity_ssh':
                        job.input_manifest_json['nextflow_profile'] = 'embl_slurm'
                        engine['descriptor']['execution_profiles'].append(profile)
                        image = root / 'package.sif'
                        image.write_bytes(b'frozen container')
                        mapping = root / 'images.json'
                        mapping.write_text(json.dumps({engine['descriptor']['package']['container_image']:
                            {'path': str(image), 'sha256': sha256_file(image)}}))
                        materializer.settings.workflow_singularity_image_manifest = str(mapping)
                    with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}):
                        run = materializer.prepare(job)
                    frozen = {p: Path(run.run_root, p).read_bytes() for p in run.engine_execution['contract_sha256']}
                    acquisitions = deepcopy(run.selected_files)
                    staged = {item['staged_path']: Path(item['staged_path']).read_bytes() for item in acquisitions}
                    with Path(run.samplesheet_path).open(newline='') as handle:
                        row = next(csv.DictReader(handle))
                    self.assertEqual((row['file_asset_id'], row['sample_id'], row['acquisition_id'], row['staged_filename']),
                                     ('42', '17', 'file-42', 'sample.mzML'))
                    # A deployment or input-registration change must not rematerialize a paused run.
                    (pipeline / 'fixture.nf').write_text('// newer deployment')
                    (root / 'sample.mzML').write_text('changed original registration')
                    materializer.settings.workflow_runs_root = str(root / 'new-runs')
                    materializer.settings.workflow_local_runs_root = str(root / 'new-local-runs')
                    if profile == 'slurm_singularity_ssh':
                        mapping.unlink()
                    job.run_root = run.run_root
                    job.parameter_values_json.update(nextflow_resume=True, apply_rt_correction=True,
                                                     rt_correction_target_ids=['standard-A', 'standard-B'])
                    for attempt, stage in enumerate([*stages[1:], 'full'], start=1):
                        job.parameter_values_json['workflow_stage'] = stage
                        job.input_manifest_json['submission_attempt'] = attempt
                        # Reconstruct the worker at every pause, as after a disconnect/restart.
                        restarted = RunMaterializer(None, materializer.settings)
                        with patch.dict(ENGINE_ADAPTERS, {}, clear=True):
                            resumed = restarted.prepare(job)
                        self.assertEqual(resumed, restarted.load_existing(job))
                        self.assertEqual(resumed.submission_attempt, attempt)
                        for field in ('run_root', 'run_name', 'pipeline_root', 'entrypoint_path', 'execution_profile',
                                      'nextflow_profile', 'work_path', 'results_path', 'staged_input_dir',
                                      'samplesheet_path', 'nextflow_config_override_path'):
                            self.assertEqual(getattr(resumed, field), getattr(run, field))
                        self.assertEqual(resumed.selected_files, acquisitions)
                        self.assertEqual({p: Path(run.run_root, p).read_bytes() for p in frozen}, frozen)
                        self.assertEqual({p: Path(p).read_bytes() for p in staged}, staged)
                        self.assertEqual(resumed.nextflow_params, {**run.nextflow_params, 'workflow_stage': stage,
                            'nextflow_resume': 'true', 'apply_rt_correction': 'true',
                            'rt_correction_target_ids': '["standard-A","standard-B"]'})

    def test_resume_rejects_modified_package_configuration(self):
        from services.engines import ENGINE_ADAPTERS
        with TemporaryDirectory() as temp:
            materializer, job, definition, _ = self.setup_run(Path(temp))
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': self.adapter}):
                run = materializer.prepare(job)
            job.run_root = run.run_root
            job.parameter_values_json = {**job.parameter_values_json, 'nextflow_resume': True}
            Path(run.nextflow_params['metabolensr_config']).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'changed'):
                materializer.prepare(job)

    def test_engine_resources_use_local_limits_and_remain_frozen_on_resume(self):
        from services.engines import ENGINE_ADAPTERS

        def adapter(context):
            launch = self.adapter(context)
            launch.resource_groups = {'fixture_detect': 'peak', 'METABOLENSR_FINALIZE': 'default'}
            return launch

        with TemporaryDirectory() as temp:
            materializer, job, definition, _ = self.setup_run(Path(temp))
            materializer.settings.workflow_local_peak_memory_gb = 3
            materializer.settings.workflow_local_max_memory_gb = 4
            with patch.dict(ENGINE_ADAPTERS, {'fixture-v1': adapter}):
                run = materializer.prepare(job)
            override = Path(run.nextflow_config_override_path).read_text()
            self.assertIn('withName: fixture_detect', override)
            self.assertIn("memory = '3 GB'", override)
            self.assertIn("memory = '4 GB'", override)
            self.assertNotIn('msdial', override.lower())
            job.run_root = run.run_root
            job.parameter_values_json = {**job.parameter_values_json, 'nextflow_resume': True, 'workflow_stage': 'full'}
            materializer.settings.workflow_local_peak_memory_gb = 12
            materializer.prepare(job)
            self.assertEqual(Path(run.nextflow_config_override_path).read_text(), override)


if __name__ == '__main__':
    unittest.main()
