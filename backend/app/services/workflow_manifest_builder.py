"""Build the immutable backend-to-worker workflow manifest.

The manifest is stored on `WorkflowJob.input_manifest_json` and consumed by the
worker materializer. This file only shapes that contract; it does not validate
permissions, create jobs, or register outputs.
"""

from copy import deepcopy
from app.models.models import DatasetFile, MetadataSubmission, WorkflowDefinition


class WorkflowManifestBuilder:
    @staticmethod
    def build_input_manifest(
        *,
        study_id: int,
        assay_id: int,
        workflow: WorkflowDefinition,
        metadata: MetadataSubmission,
        files: list[DatasetFile],
        reference_file: DatasetFile | None,
        parameters: dict,
        msdial_template: dict | None = None,
        engine: dict | None = None,
        masscube_template: dict | None = None,
    ) -> dict:
        static_inputs = deepcopy(workflow.static_input_config_json or {})
        nextflow_profile = (
            parameters.get("nextflow_profile")
            or workflow.default_nextflow_profile
            or workflow.container_profile
            or "docker"
        )
        execution_profile = parameters.get("execution_profile") or workflow.execution_profile or "local_docker"
        pipeline_root = parameters.get("pipeline_root") or workflow.pipeline_root
        config_source = parameters.get("config_source") or "uploaded_file"
        selected_files = [
            {
                "file_asset_id": file.id,
                "sample_id": file.sample_id,
                "acquisition_record_id": file.acquisition_id,
                "file_kind": file.file_role,
                "original_filename": file.original_filename,
                "storage_uri": file.storage_uri,
                "checksum_sha256": file.checksum_sha256,
                "file_size_bytes": file.file_size_bytes,
                "media_type": file.media_type,
            }
            for file in files
        ]
        manifest = {
            "study_id": study_id,
            "assay_id": assay_id,
            "workflow_definition_id": workflow.id,
            "execution_backend": workflow.execution_backend,
            "execution_profile": execution_profile,
            "pipeline_root": pipeline_root,
            "entrypoint": workflow.nextflow_entrypoint,
            "nextflow_profile": nextflow_profile,
            "file_ids": [file.id for file in files],
            "selected_files": selected_files,
            "reference_file_asset_id": reference_file.id if reference_file else None,
            "reference_filename": reference_file.original_filename if reference_file else None,
            "metadata_submission_id": metadata.id,
            "config_source": config_source,
            "static_inputs": static_inputs,
        }
        if msdial_template is not None:
            manifest["msdial_template"] = dict(msdial_template)
            manifest["config_source"] = "template_plus_overrides"
        if engine is not None:
            manifest['engine'] = deepcopy(engine)
            manifest['entrypoint'] = engine['descriptor']['entrypoint']
        if masscube_template is not None:
            manifest['masscube_template'] = dict(masscube_template)
            manifest['config_source'] = 'masscube_template'
        return manifest
