"""Materialize one queued workflow job into a runnable Nextflow run folder.

Start at `RunMaterializer.prepare()`. A new job is built from the engine
snapshot the backend froze at launch (`_prepare_new`); a job continuing after a
review pause reuses its folder and frozen contract (`_prepare_continuation`).
Either way the result is saved as the recovery snapshot. Engine-specific steps
live in the trusted adapters under `services/engines/`; the HPC smoke test has
its own `hpc_smoke_materializer`.
"""

from copy import deepcopy
from dataclasses import replace
import json
import os
import re
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile
from typing import Any

from app.core.engine_contract import EngineDescriptor, contained_path, sha256_file, validate_engine_parameters
from app.models.models import DatasetFile, MetadataSubmission, WorkflowJob

from config import WorkerSettings
from services.engines import ENGINE_ADAPTERS, EngineContext
from services.input_stager import InputStager
from services.materialized_run import (
    MaterializedRun,
    is_truthy,
    load_materialized_run_snapshot,
    nextflow_param_text,
    write_materialized_run_snapshot,
)
from services.nextflow_override import local_resource_override, slurm_singularity_override
from services.run_workspace import RunWorkspaceBuilder
from services.samplesheet_builder import SamplesheetBuilder


def _verify_container_files(records):
    for record in records.values():
        path = Path(record['path'])
        if not path.is_absolute() or not re.fullmatch(r'[0-9a-f]{64}', record['sha256']):
            raise ValueError('Invalid pinned container path or SHA-256')
        if not path.is_file() or sha256_file(path) != record['sha256']:
            raise ValueError(f'Pinned container unavailable or changed: {path}')


def _execution_images(descriptor, profile, settings):
    images = {**descriptor['container_images'],
              'metabolensr_container_image': descriptor['package']['container_image']}
    if profile == 'local_docker':
        return images, {}
    manifest_path = settings.workflow_singularity_image_manifest
    if not manifest_path:
        raise ValueError('WORKFLOW_SINGULARITY_IMAGE_MANIFEST is required for engine HPC runs')
    mapping = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    records = {}
    for reference in images.values():
        if reference not in mapping:
            raise ValueError(f'Pinned container has no Singularity mapping: {reference}')
        record = mapping[reference]
        records[reference] = {'path': record['path'], 'sha256': record['sha256']}
    _verify_container_files(records)
    return {key: records[value]['path'] for key, value in images.items()}, records


def _copy_checked(source: Path, destination: Path, expected: str):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if sha256_file(destination) != expected:
        raise ValueError(f'Queued workflow input changed: {source}')


def _write_params(run):
    Path(run.params_path).write_text(json.dumps({
        'job_id': run.job_id, 'study_id': run.study_id, 'assay_id': run.assay_id,
        'engine': run.engine_execution, 'pipeline_root': run.pipeline_root, 'entrypoint': run.entrypoint_path,
        'execution_profile': run.execution_profile, 'nextflow_profile': run.nextflow_profile,
        'nextflow_params': run.nextflow_params, 'selected_files': run.selected_files,
        'reference_file_asset_id': run.reference_file_asset_id,
        'metadata_snapshot': run.metadata_snapshot, 'method_metadata': run.method_metadata,
    }, indent=2) + '\n', encoding='utf-8')


class RunMaterializer:
    """Turn one queued workflow job into files that Nextflow can execute.

    The worker is the only layer that touches local workflow-run folders. The
    backend creates the launch intent in the database; this service converts
    that intent into concrete files for a long-running worker execution.
    """

    def __init__(self, db, settings: WorkerSettings):
        self.db = db
        self.settings = settings
        self.workspace_builder = RunWorkspaceBuilder(settings.workflow_runs_root, settings.workflow_local_runs_root)
        self.input_stager = InputStager()
        self.samplesheet_builder = SamplesheetBuilder(db)

    def prepare(self, job: WorkflowJob) -> MaterializedRun:
        """Materialize one queued job, then save its recovery snapshot."""

        manifest = job.input_manifest_json or {}
        if 'engine' not in manifest:
            raise ValueError(f'Workflow job {job.id} has no engine snapshot; the pre-engine MS-DIAL workflow is retired')
        if is_truthy((job.parameter_values_json or {}).get('nextflow_resume')):
            run = self._prepare_continuation(job)
        else:
            run = self._prepare_new(job)
        submission_attempt = int(manifest.get('submission_attempt', 0))
        return write_materialized_run_snapshot(replace(run, submission_attempt=submission_attempt))

    def load_existing(self, job: WorkflowJob) -> MaterializedRun:
        """Reload a run without rewriting or resubmitting it."""

        return load_materialized_run_snapshot(job)

    def _prepare_new(self, job: WorkflowJob) -> MaterializedRun:
        # 1. Check the frozen engine snapshot against what this worker can run.
        manifest = deepcopy(job.input_manifest_json)
        engine = manifest['engine']
        descriptor = EngineDescriptor.model_validate(engine['descriptor']).model_dump()
        adapter = ENGINE_ADAPTERS.get(descriptor['adapter_id'])
        if adapter is None:
            raise ValueError(f"Pinned engine adapter unavailable: {descriptor['adapter_id']}")
        profile = manifest['execution_profile']
        if profile not in descriptor['execution_profiles']:
            raise ValueError(f'Engine does not support execution profile {profile}')
        images, container_files = _execution_images(descriptor, profile, self.settings)
        parameters = validate_engine_parameters(engine['parameter_schema'], engine['parameters'])
        if dict(job.parameter_values_json or {}) != parameters:
            raise ValueError('Queued engine parameters changed after launch')

        # 2. Create the run folder and copy the checked workflow source and support files into it.
        workspace = self.workspace_builder.build(job)
        manifest_pipeline = Path(manifest['pipeline_root'])
        pipeline = manifest_pipeline
        configured_pipeline = Path(getattr(self.settings, 'workflow_pipeline_root', pipeline))
        if not pipeline.is_dir() and configured_pipeline.is_dir():
            pipeline = configured_pipeline
        frozen_pipeline = workspace.contract_dir / 'workflow'
        frozen_hashes = {}
        for relative, checksum in engine['source_sha256'].items():
            source = contained_path(pipeline, relative)
            destination = contained_path(frozen_pipeline, relative)
            _copy_checked(source, destination, checksum)
            frozen_hashes[destination.relative_to(workspace.run_root).as_posix()] = checksum
        entrypoint = contained_path(frozen_pipeline, descriptor['entrypoint'])
        if not entrypoint.is_file():
            raise ValueError('Pinned engine entrypoint unavailable')
        support_files = {}
        artifacts = []
        for key, record in engine['support_files'].items():
            support_source = Path(record['path'])
            if support_source.is_absolute():
                if support_source.is_relative_to(manifest_pipeline):
                    support_source = contained_path(pipeline, support_source.relative_to(manifest_pipeline).as_posix())
            else:
                support_source = contained_path(pipeline, record['path'])
            destination = contained_path(workspace.contract_dir / 'support', f"{key}/{Path(record['path']).name}")
            _copy_checked(support_source, destination, record['sha256'])
            support_files[key] = destination
            artifacts.append((destination, f'engine_input_{key}'))

        # 3. Stage the acquisitions, write the samplesheet, and let the engine adapter add its files.
        selected = self.load_selected_files(manifest['file_ids'])
        staged = self.input_stager.stage_raw_input_files(selected, workspace.staged_input_dir,
                                                       frozen_files=manifest.get('selected_files'))
        for record in staged:
            relative = Path(record['staged_path']).relative_to(workspace.run_root).as_posix()
            frozen_hashes[relative] = record['checksum_sha256']
        metadata = self.load_metadata_submission(manifest)
        self.samplesheet_builder.write(selected_files=selected,
            reference_file_asset_id=manifest.get('reference_file_asset_id'),
            output_path=workspace.samplesheet_path, staged_files=staged,
            frozen_rows=manifest.get("sample_metadata_rows"), legacy_metadata="sample_metadata_rows" not in manifest)
        context = EngineContext(descriptor, deepcopy(parameters), manifest, workspace, staged, support_files, self.settings)
        launch = adapter(context)

        # 4. Write the MetaboLensR package config, the empty review overrides and the contract checksums.
        package_config = workspace.contract_dir / 'metabolensr_config.json'
        package_config.write_text(json.dumps(descriptor['package']['config'], indent=2) + '\n', encoding='utf-8')
        overrides = workspace.contract_dir / 'feature_curation_overrides.tsv'
        overrides.write_text('feature_id\toperator_override_statistics\toperator_override_annotation\n', encoding='utf-8')
        artifacts.extend(launch.artifacts)
        artifacts.extend([(workspace.samplesheet_path, 'run_samplesheet'), (package_config, 'metabolensr_config'),
                          (overrides, 'metabolensr_overrides')])
        for path, _ in artifacts:
            relative = path.relative_to(workspace.run_root).as_posix()
            if path.resolve() != contained_path(workspace.run_root, relative):
                raise ValueError(f'Invalid engine artifact: {path}')
            frozen_hashes[relative] = sha256_file(path)

        # 5. Build the Nextflow parameters and the per-run Nextflow config.
        forwarded = {key: value for key, value in context.parameters.items() if key not in launch.consumed_parameters}
        params = {**forwarded, **launch.parameters, **images,
                  'input_dir': str(workspace.staged_input_dir), 'samplesheet': str(workspace.samplesheet_path),
                  'outdir': str(workspace.results_path), 'run_id': workspace.run_name,
                  'params_json': str(workspace.params_path), 'metabolensr_config': str(package_config),
                  'metabolensr_overrides': str(overrides)}
        params = {key: nextflow_param_text(value) for key, value in params.items()}
        config_text = (local_resource_override(self.settings, launch.resource_groups)
                       if profile == 'local_docker'
                       else slurm_singularity_override(manifest['nextflow_profile']))
        workspace.nextflow_config_override_path.write_text(config_text, encoding='utf-8')
        frozen_hashes[workspace.nextflow_config_override_path.relative_to(workspace.run_root).as_posix()] = sha256_file(workspace.nextflow_config_override_path)

        # 6. Record everything a continuation must find unchanged, then write params.json.
        engine_execution = {**engine, 'contract_sha256': frozen_hashes,
                            'container_files': container_files,
                            'artifacts': [(str(path), role) for path, role in artifacts],
                            'native_outputs': launch.native_outputs}
        # MS-DIAL-era fields stay in the recovery snapshot; other engines leave them empty.
        legacy_fields = {'reference_file_asset_id': None, 'reference_path': '', 'staged_msdial_config_path': '',
                         'ms1_library_path': '', 'ms2_library_path': '', 'is_library_path': '', **launch.legacy_fields}
        run = MaterializedRun(
            **{**workspace.path_fields(), **legacy_fields},
            job_id=job.id, study_id=job.study_id, assay_id=job.assay_id, workflow_definition_id=job.workflow_definition_id,
            execution_profile=profile, pipeline_root=str(frozen_pipeline), entrypoint_path=str(entrypoint),
            nextflow_profile=manifest['nextflow_profile'], config_source='engine_snapshot',
            metadata_snapshot=metadata.normalized_payload_json, method_metadata=self.build_method_metadata(metadata),
            selected_files=staged, nextflow_params=params, engine_execution=engine_execution,
        )
        _write_params(run)
        return run

    def _prepare_continuation(self, job: WorkflowJob) -> MaterializedRun:
        """Continue a paused or failed run in its existing folder with Nextflow `-resume`."""

        run = self.load_existing(job)
        engine = run.engine_execution
        if not engine:
            raise ValueError('Engine continuation is missing its immutable contract')
        _verify_container_files(engine.get('container_files', {}))
        for relative, checksum in engine['contract_sha256'].items():
            path = contained_path(Path(run.run_root), relative)
            if not path.is_file() or sha256_file(path) != checksum:
                raise ValueError(f'Frozen workflow contract changed: {relative}')
        supplied = dict(job.parameter_values_json or {})
        supplied.pop('nextflow_resume', None)
        mutable = {'workflow_stage', 'apply_rt_correction', 'rt_correction_target_ids'}
        original = engine['parameters']
        if {k: v for k, v in supplied.items() if k not in mutable} != {k: v for k, v in original.items() if k not in mutable}:
            raise ValueError('Scientific parameters cannot change during continuation')
        # RT decisions are already checked by the existing backend review transition.
        validation_values = {k: v for k, v in supplied.items() if k not in {'apply_rt_correction', 'rt_correction_target_ids'}}
        validate_engine_parameters(engine['parameter_schema'], validation_values)
        params = dict(run.nextflow_params)
        for key in mutable & supplied.keys():
            value = supplied[key]
            params[key] = json.dumps(value, separators=(',', ':')) if isinstance(value, list) else nextflow_param_text(value)
        frozen = (job.input_manifest_json or {}).get('feature_review')
        if frozen:
            source = Path(frozen['path'])
            root = Path(run.run_root)
            if not source.is_file() or sha256_file(source) != frozen['sha256']:
                raise ValueError('Frozen review override changed or is unavailable')
            path = contained_path(root, f'contract/curation/{source.name}')
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                # Publish only a complete checked copy. A retry never rewrites an
                # already-staged revision, and SSH maps this run-local path.
                with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    try:
                        with source.open('rb') as incoming:
                            shutil.copyfileobj(incoming, handle)
                        handle.flush()
                        os.fsync(handle.fileno())
                        if sha256_file(temporary) != frozen['sha256']:
                            raise ValueError('Frozen review override changed while staging')
                        os.replace(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
            if not path.is_file() or sha256_file(path) != frozen['sha256']:
                raise ValueError('Staged review override changed or is unavailable')
            params['metabolensr_overrides'] = str(path)
        params['nextflow_resume'] = 'true'
        resumed = replace(run, nextflow_params=params)
        _write_params(resumed)
        return resumed

    def load_selected_files(self, file_ids: list[int]) -> list[DatasetFile]:
        """Load selected `DatasetFile` rows in the exact requested order."""

        rows = self.db.query(DatasetFile).filter(DatasetFile.id.in_(file_ids)).all()
        by_id = {row.id: row for row in rows}
        missing_ids = [file_id for file_id in file_ids if file_id not in by_id]
        if missing_ids:
            raise ValueError(f"Missing workflow input files: {missing_ids}")
        return [by_id[file_id] for file_id in file_ids]

    def load_metadata_submission(self, manifest: dict[str, Any]) -> MetadataSubmission:
        """Load the metadata submission snapshot attached to the workflow job."""

        metadata_submission_id = manifest.get("metadata_submission_id")
        record = self.db.query(MetadataSubmission).filter(MetadataSubmission.id == metadata_submission_id).first()
        if record is None:
            raise ValueError("Workflow job is missing a valid metadata submission")
        return record

    @staticmethod
    def get_by_path(payload: dict[str, Any], path: str) -> Any:
        """Read a dotted path from a nested metadata payload."""

        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    def build_method_metadata(self, metadata_submission: MetadataSubmission) -> dict[str, Any]:
        """Extract assay-method metadata copied into the workflow contract."""

        normalized = metadata_submission.normalized_payload_json or {}
        return {
            "profile_id": normalized.get("profile_id"),
            "profile_version": normalized.get("profile_version"),
            "instrument_vendor": self.get_by_path(normalized, "instrument.ms_vendor"),
            "instrument_model": self.get_by_path(normalized, "instrument.ms_model"),
            "ion_source": self.get_by_path(normalized, "instrument.ms_ion_source"),
            "polarity": self.get_by_path(normalized, "instrument.ms_polarity_mode"),
            "separation_mode": self.get_by_path(normalized, "chromatography.separation_mode"),
            "column_model": self.get_by_path(normalized, "chromatography.column_name"),
            "column_type": self.get_by_path(normalized, "chromatography.chromatography_type"),
            "chromatography_instrument": self.get_by_path(normalized, "chromatography.lc_system"),
            "scan_mz_range": self.get_by_path(normalized, "acquisition.mz_range"),
            "mass_analyzer": self.get_by_path(normalized, "instrument.ms_analyzer_type"),
            "ms_type": self.get_by_path(normalized, "acquisition.acquisition_mode"),
            "acquisition_software": self.get_by_path(normalized, "run_context.acquisition_software"),
        }
