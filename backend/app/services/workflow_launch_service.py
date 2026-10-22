"""Create queued workflow jobs from user launch intent.

This module owns launch-time validation and job creation. It delegates the
stored manifest shape to `WorkflowManifestBuilder` and does not mutate worker
runtime state after the job is queued.
"""

import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session

from app.core.engine_contract import snapshot_engine, validate_engine_acquisitions
from app.core.errors import bad_request, not_found
from app.core.states import ACCEPTED_INPUT_FILE_STATES, FINISHED_JOB_STATES
from app.core.permissions import require_allowed
from app.core.sample_context import annotations_dict, effective_sample_annotations, split_acquisition_annotations
from app.models.models import Assay, AssaySampleLink, Dataset, DatasetFile, MetadataSubmission, Sample, WorkflowJob, WorkflowRunArtifact
from app.services.audit_service import AuditService
from app.services.acquisition_service import AcquisitionService
from app.services.samplesheet_rows_service import SamplesheetRowsService
from app.services.provenance_service import ProvenanceService
from app.services.study_service import StudyService
from app.services.workflow_definition_service import WorkflowDefinitionService
from app.services.workflow_manifest_builder import WorkflowManifestBuilder
from app.services.workflow_is_qc_service import WorkflowIsQcService, select_correction_plan


class WorkflowLaunchService:
    NEXTFLOW_PROFILES_BY_EXECUTION_PROFILE = {
        "local_docker": {"docker"},
        "slurm_singularity": {"embl_slurm"},
        "slurm_singularity_ssh": {"embl_slurm"},
    }
    DEFAULT_NEXTFLOW_PROFILE_BY_EXECUTION_PROFILE = {
        "local_docker": "docker",
        "slurm_singularity": "embl_slurm",
        "slurm_singularity_ssh": "embl_slurm",
    }
    RETIRED_WORKFLOW_MESSAGE = (
        "This job used the retired MS-DIAL workflow. Its results stay available, "
        "but it can no longer be continued or retried; launch a new run instead."
    )

    def __init__(self, db: Session):
        self.db = db
        self.studies = StudyService(db)
        self.audit = AuditService(db)
        self.prov = ProvenanceService(db)
        self.definitions = WorkflowDefinitionService(db)
        self.manifests = WorkflowManifestBuilder()

    def launch(
        self,
        user_id: int,
        assay_id: int,
        workflow_definition_id: int,
        parameters: dict | None = None,
        file_ids: list[int] | None = None,
        msdial_template_text: str | None = None,
        review_stages: list[str] | None = None,
        metabolensr_profile: str | None = None,
        metabolensr_enabled_rules: dict | None = None,
        metabolensr_expected_image: str | None = None,
        masscube_template_text: str | None = None,
    ) -> WorkflowJob:
        assay = self._load_launchable_assay(assay_id)
        study = self.studies.get(user_id, assay.study_id)
        require_allowed(self.studies.perms.can_operate_workflows(user_id, study.id), "Only operator/admin can launch workflows")

        if study.status not in {"approved", "processing", "completed"}:
            raise bad_request("Study must be approved before launch")

        workflow = self.definitions.active_definition(workflow_definition_id)
        metadata = self._latest_valid_metadata(assay_id)

        normalized_parameters = dict(parameters or {})
        engine_descriptor = (workflow.static_input_config_json or {}).get('engine')
        if not engine_descriptor:
            raise bad_request('This workflow definition has no engine descriptor and cannot be launched')
        if review_stages is not None:
            normalized_parameters['workflow_stage'] = self._selected_review_stage(
                engine_descriptor.get('review_transitions', {}), review_stages)
        if str(normalized_parameters.get("apply_rt_correction") or "").strip().lower() in {"1", "true", "yes"}:
            raise bad_request("RT correction can be selected only when continuing an IS-QC review")
        normalized_parameters.pop("apply_rt_correction", None)
        if normalized_parameters.get("rt_correction_target_ids"):
            raise bad_request("RT correction standards can be selected only when continuing an IS-QC review")
        normalized_parameters.pop("rt_correction_target_ids", None)
        self.definitions.validate_library_parameters(workflow, normalized_parameters)
        masscube_template_snapshot = None
        if engine_descriptor.get('engine_id') == 'masscube':
            if msdial_template_text is not None:
                raise bad_request('MassCube requires a MassCube parameter template')
            masscube_template_snapshot = self.definitions.resolve_masscube_template(
                user_id, workflow, normalized_parameters, masscube_template_text)
        elif masscube_template_text is not None:
            raise bad_request('This workflow does not support MassCube templates')
        msdial_template_snapshot = None
        if msdial_template_text is not None:
            selected_template = normalized_parameters.get("msdial_config_path") or dict(workflow.static_input_config_json or {}).get("msdial_config_path")
            if not selected_template:
                raise bad_request("Select an MS-DIAL parameter template before editing it")
            selected_template = self.definitions.validate_msdial_config_path(workflow, str(selected_template))
            msdial_template_snapshot = self.definitions.msdial_template_snapshot(selected_template, msdial_template_text)
        self._validate_sirius_parameters(workflow, normalized_parameters)
        self._normalize_execution_target(workflow, normalized_parameters)
        files = self._select_files(assay_id=assay_id, file_ids=file_ids)
        if not files:
            raise bad_request("At least one finalized dataset file is required")
        try:
            validate_engine_acquisitions(engine_descriptor, files)
        except ValueError as exc:
            raise bad_request(str(exc)) from exc
        self._default_missing_batch_ids(assay_id, files)
        self._validate_required_sample_metadata(assay_id, files)

        needs_reference = 'reference_file_id' in engine_descriptor['required_inputs']
        reference_file = self.select_reference_file(files=files, parameters=normalized_parameters) if needs_reference else None
        execution_profile = normalized_parameters['execution_profile']
        if execution_profile not in engine_descriptor['execution_profiles']:
            raise bad_request(f'Engine does not support execution profile {execution_profile}')
        scientific = {key: value for key, value in normalized_parameters.items()
                      if key not in {'execution_profile', 'nextflow_profile'}}
        if reference_file:
            scientific['reference_file_id'] = reference_file.id
        pipeline_root = self.definitions.resolve_pipeline_root(workflow)
        try:
            engine_snapshot = snapshot_engine(workflow, scientific, pipeline_root, metabolensr_profile,
                                              metabolensr_enabled_rules, metabolensr_expected_image)
        except (ValueError, OSError) as exc:
            raise bad_request(str(exc)) from exc
        normalized_parameters = {**engine_snapshot['parameters'],
            'execution_profile': execution_profile, 'nextflow_profile': normalized_parameters['nextflow_profile']}
        input_manifest = self.manifests.build_input_manifest(
            study_id=study.id,
            assay_id=assay.id,
            workflow=workflow,
            metadata=metadata,
            files=files,
            reference_file=reference_file,
            parameters=normalized_parameters,
            msdial_template=msdial_template_snapshot,
            masscube_template=masscube_template_snapshot,
            engine=engine_snapshot,
        )
        input_manifest['sample_metadata_rows'] = SamplesheetRowsService(self.db).build_rows(files, reference_file.id if reference_file else None)
        input_manifest['pipeline_root'] = str(pipeline_root)
        normalized_parameters = engine_snapshot['parameters']
        if review_stages is not None:
            # Launch intent stays outside frozen scientific parameters and the CLI.
            input_manifest['review_stages'] = list(review_stages)
        status_payload = {
            "phase": "queued",
            "selected_file_count": len(files),
            "reference_file_asset_id": reference_file.id if reference_file else None,
            "reference_filename": reference_file.original_filename if reference_file else None,
            "config_source": input_manifest["config_source"],
        }
        job = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow_definition_id,
            requested_by=user_id,
            status="queued",
            input_manifest_json=input_manifest,
            parameter_values_json=normalized_parameters,
            execution_backend=workflow.execution_backend,
            execution_profile=input_manifest["execution_profile"],
            status_payload_json=status_payload,
        )
        self.db.add(job)
        study.status = "processing"
        self.audit.log(
            user_id,
            "workflow.launch",
            "workflow_job",
            "new",
            {
                "study_id": study.id,
                "assay_id": assay.id,
                "workflow_definition_id": workflow_definition_id,
                "execution_profile": input_manifest["execution_profile"],
                "selected_file_ids": input_manifest["file_ids"],
                "reference_file_asset_id": reference_file.id if reference_file else None,
                "review_stages": review_stages,
            },
        )
        self.prov.log(study_id=study.id, assay_id=assay.id, workflow_job_id=None, event_type="workflow.queued", source_ref=input_manifest)
        self.db.commit()
        self.db.refresh(job)
        return job

    def launch_hpc_smoke_test(self, user_id: int, assay_id: int, workflow_definition_id: int) -> WorkflowJob:
        """Queue an operator diagnostic job for the SSH/SLURM runner.

        This intentionally bypasses scientific launch prerequisites such as
        valid metadata and RAW inputs. The backend still owns permission and
        assay checks, then the worker owns the long-running SSH/SLURM action.
        """

        assay = self._load_launchable_assay(assay_id)
        study = self.studies.get(user_id, assay.study_id)
        require_allowed(
            self.studies.perms.can_operate_workflows(user_id, study.id),
            "Only operator/admin can launch HPC smoke tests",
        )

        workflow = self.definitions.active_definition(workflow_definition_id)
        parameter_values = {
            "workflow_stage": "hpc_smoke",
            "analysis_mode": "ssh_slurm_smoke",
            "output_prefix": "hpc_smoke",
            "nextflow_resume": False,
        }
        input_manifest = {
            "job_kind": "hpc_smoke_test",
            "diagnostic": True,
            "study_id": study.id,
            "assay_id": assay.id,
            "workflow_definition_id": workflow.id,
            "file_ids": [],
            "selected_files": [],
            "metadata_submission_id": None,
            "reference_file_asset_id": None,
            "execution_backend": workflow.execution_backend,
            "execution_profile": "slurm_singularity_ssh",
            "nextflow_profile": "embl_slurm",
            "entrypoint": "main.nf",
            "config_source": "hpc_smoke_diagnostic",
        }
        status_payload = {
            "phase": "queued",
            "workflow_stage": "hpc_smoke",
            "job_kind": "hpc_smoke_test",
            "diagnostic": True,
            "selected_file_count": 0,
            "message": "Queued HPC SSH smoke test diagnostic job.",
        }
        job = WorkflowJob(
            study_id=study.id,
            assay_id=assay.id,
            workflow_definition_id=workflow.id,
            requested_by=user_id,
            status="queued",
            input_manifest_json=input_manifest,
            parameter_values_json=parameter_values,
            execution_backend=workflow.execution_backend,
            execution_profile="slurm_singularity_ssh",
            status_payload_json=status_payload,
        )
        self.db.add(job)
        self.db.flush()
        self.audit.log(
            user_id,
            "workflow.hpc_smoke.launch",
            "workflow_job",
            str(job.id),
            {
                "study_id": study.id,
                "assay_id": assay.id,
                "workflow_definition_id": workflow.id,
                "execution_profile": "slurm_singularity_ssh",
                "job_kind": "hpc_smoke_test",
            },
        )
        self.prov.log(
            study_id=study.id,
            assay_id=assay.id,
            workflow_job_id=job.id,
            event_type="workflow.hpc_smoke_queued",
            source_ref=input_manifest,
        )
        self.db.commit()
        self.db.refresh(job)
        return job

    def continue_review_stage(
        self,
        user_id: int,
        assay_id: int,
        job_id: int,
        operator_note: str | None = None,
        apply_rt_correction: bool | None = None,
        rt_correction_target_ids: list[str] | None = None,
        preview_file_id: int | None = None,
        expected_revision: int | None = None,
    ) -> WorkflowJob:
        job = (
            self.db.query(WorkflowJob)
            .filter(
                WorkflowJob.id == job_id,
                WorkflowJob.assay_id == assay_id,
                WorkflowJob.deleted_at.is_(None),
            )
            .with_for_update()
            .first()
        )
        if job is None:
            raise not_found("Workflow job not found")

        study = self.studies.get(user_id, job.study_id)
        require_allowed(self.studies.perms.can_operate_workflows(user_id, study.id), "Only operator/admin can continue workflows")

        if job.status not in FINISHED_JOB_STATES:
            raise bad_request("Only a completed review job can be continued")
        engine = (job.input_manifest_json or {}).get('engine')
        if not engine:
            raise bad_request(self.RETIRED_WORKFLOW_MESSAGE)
        parameter_values = dict(job.parameter_values_json or {})
        status_payload = dict(job.status_payload_json or {})
        transitions = engine['descriptor'].get('review_transitions', {})
        review_stage = self._review_stage_for(parameter_values, status_payload, transitions)
        is_failed_resume_retry = job.status == "failed" and bool(parameter_values.get("nextflow_resume"))
        if review_stage is None and not is_failed_resume_retry:
            raise bad_request("Only jobs launched with a review workflow_stage can be continued")
        if not job.run_root:
            raise bad_request("Workflow job is missing its run directory and cannot be resumed")
        normalized_operator_note = str(operator_note or "").strip()
        if review_stage == "is_qc_review" and self._is_failed_is_qc_review(job, status_payload) and not normalized_operator_note:
            raise bad_request("Failed IS-QC review requires an operator note before continuation")

        if review_stage == "is_qc_review":
            apply_correction = bool(apply_rt_correction) if apply_rt_correction is not None else False
            plan = WorkflowIsQcService(self.db).get_correction_plan(
                user_id,
                assay_id,
                job_id,
                required=apply_correction or rt_correction_target_ids is not None,
            )
            selected_plan = select_correction_plan(plan, rt_correction_target_ids) if plan else None
            if apply_correction and not selected_plan.get("can_apply"):
                raise bad_request("RT correction plan is not eligible for application")
            selected_target_ids = selected_plan["selected_target_ids"] if selected_plan else []
            parameter_values["apply_rt_correction"] = apply_correction
            parameter_values["rt_correction_target_ids"] = selected_target_ids
            status_payload["rt_correction_decision"] = "corrected" if apply_correction else "original"
            status_payload["rt_correction_target_ids"] = selected_target_ids
        elif apply_rt_correction is not None and bool(apply_rt_correction) != bool(parameter_values.get("apply_rt_correction")):
            raise bad_request("RT correction choice cannot be changed after IS-QC continuation")
        elif rt_correction_target_ids is not None and list(map(str, rt_correction_target_ids)) != list(map(str, parameter_values.get("rt_correction_target_ids") or [])):
            raise bad_request("RT correction standard selection cannot be changed after IS-QC continuation")

        if (preview_file_id is None) != (expected_revision is None):
            raise bad_request("Reviewed continuation requires preview identity and expected revision")
        frozen = (job.input_manifest_json or {}).get('feature_review')
        if preview_file_id is not None and not (is_failed_resume_retry and frozen):
            if job.status != 'succeeded' or review_stage != 'qc_preview':
                raise bad_request("Reviewed continuation requires a successful feature preview")
            from app.services.workflow_feature_review_service import WorkflowFeatureReviewService
            frozen = WorkflowFeatureReviewService(self.db).freeze(
                user_id, assay_id, job_id, preview_file_id, expected_revision)
            job.input_manifest_json = {**(job.input_manifest_json or {}), 'feature_review': frozen}

        # Uncertain submissions must reconcile the same marker/name before any new driver.
        failed = job.status == "failed"
        new_attempt = not failed or status_payload.get("exit_code") is not None
        job.input_manifest_json = {
            **(job.input_manifest_json or {}),
            "submission_attempt": int((job.input_manifest_json or {}).get("submission_attempt", 0)) + int(new_attempt),
        }
        previous_external_run_id = job.external_run_id
        current_stage = parameter_values.get("workflow_stage") or "full"
        next_stage = current_stage if failed else transitions.get(review_stage or "", current_stage)
        selected_reviews = (job.input_manifest_json or {}).get('review_stages')
        if not failed and selected_reviews is not None:
            next_stage = self._selected_review_stage(transitions, selected_reviews, start=next_stage)
        parameter_values["workflow_stage"] = next_stage
        parameter_values["nextflow_resume"] = True
        job.parameter_values_json = parameter_values
        job.status = "queued"
        job.external_run_id = None
        job.submitted_at = None
        job.started_at = None
        job.finished_at = None
        job.error_message = None
        job.status_payload_json = {
            **status_payload,
            "phase": "queued",
            "workflow_stage": next_stage,
            "nextflow_resume": True,
            "continued_from_workflow_stage": review_stage,
            "previous_external_run_id": previous_external_run_id,
            "exit_code": None,
        }
        if normalized_operator_note:
            job.status_payload_json["operator_note"] = normalized_operator_note
            job.status_payload_json["review_override"] = review_stage == "is_qc_review"
        self.audit.log(
            user_id,
            "workflow.continue",
            "workflow_job",
            str(job.id),
            {
                "study_id": job.study_id,
                "assay_id": job.assay_id,
                "workflow_job_id": job.id,
                "workflow_stage": next_stage,
                "nextflow_resume": True,
                "continued_from_workflow_stage": review_stage,
                "apply_rt_correction": bool(parameter_values.get("apply_rt_correction")),
                "rt_correction_decision": status_payload.get("rt_correction_decision"),
                "rt_correction_target_ids": parameter_values.get("rt_correction_target_ids") or [],
            },
        )
        self.prov.log(
            study_id=job.study_id,
            assay_id=job.assay_id,
            workflow_job_id=job.id,
            event_type="workflow.continue_queued",
            source_ref={
                "workflow_stage": next_stage,
                "nextflow_resume": True,
                "continued_from_workflow_stage": review_stage,
                "operator_note_present": bool(normalized_operator_note),
                "apply_rt_correction": bool(parameter_values.get("apply_rt_correction")),
                "rt_correction_decision": status_payload.get("rt_correction_decision"),
                "rt_correction_target_ids": parameter_values.get("rt_correction_target_ids") or [],
            },
        )
        self.db.commit()
        self.db.refresh(job)
        return job

    def _load_launchable_assay(self, assay_id: int) -> Assay:
        assay = self.db.query(Assay).filter(Assay.id == assay_id).first()
        if assay is None:
            raise not_found("Assay not found")
        if assay.platform != "metabolomics" or assay.metabolomics_assay is None:
            raise bad_request("Only metabolomics assays are supported for workflow launch")
        if assay.metabolomics_assay.assay_type != "LC_MS":
            raise bad_request(
                f"Workflow launch is currently supported only for LC-MS assays "
                f"(found {assay.metabolomics_assay.assay_type})"
            )
        return assay

    def _latest_valid_metadata(self, assay_id: int) -> MetadataSubmission:
        metadata = (
            self.db.query(MetadataSubmission)
            .filter(MetadataSubmission.assay_id == assay_id)
            .order_by(MetadataSubmission.id.desc())
            .first()
        )
        if not metadata or metadata.validation_status != "valid":
            raise bad_request("Valid assay metadata is required")
        return metadata

    def _select_files(self, assay_id: int, file_ids: list[int] | None) -> list[DatasetFile]:
        eligible_query = (
            self.db.query(DatasetFile)
            .join(Dataset, Dataset.id == DatasetFile.dataset_id)
            .filter(
                Dataset.assay_id == assay_id,
                Dataset.dataset_kind == "raw",
                DatasetFile.file_role == "thermo_raw",
                DatasetFile.status.in_(ACCEPTED_INPUT_FILE_STATES),
            )
        )

        if file_ids is None:
            return eligible_query.order_by(DatasetFile.id.asc()).all()

        requested_ids = [int(file_id) for file_id in file_ids]
        if not requested_ids:
            raise bad_request("At least one file must be selected")

        requested_ids = list(dict.fromkeys(requested_ids))
        selected = eligible_query.filter(DatasetFile.id.in_(requested_ids)).all()
        selected_by_id = {file.id: file for file in selected}
        missing_ids = [file_id for file_id in requested_ids if file_id not in selected_by_id]
        if missing_ids:
            raise bad_request(
                "Selected files are not eligible for workflow launch "
                "(must belong to the assay and be uploaded/validated): "
                f"{missing_ids}"
            )
        return [selected_by_id[file_id] for file_id in requested_ids]

    def _assay_sample_links_by_sample_id(self, assay_id: int, sample_ids: list[int]) -> dict[int, AssaySampleLink]:
        if not sample_ids:
            return {}
        links = (
            self.db.query(AssaySampleLink)
            .filter(AssaySampleLink.assay_id == assay_id, AssaySampleLink.sample_id.in_(sample_ids))
            .all()
        )
        return {int(link.sample_id): link for link in links}

    def _default_missing_batch_ids(self, assay_id: int, files: list[DatasetFile]) -> None:
        assay = self.db.get(Assay, assay_id)
        seen = set()
        for file in files:
            if file.sample_id is None:
                continue
            sample = self.db.get(Sample, file.sample_id)
            if sample is None or sample.study_id != assay.study_id:
                raise bad_request("Selected acquisition sample does not belong to this study")
            acquisition = file.acquisition
            if acquisition is None:
                # Legacy registrations are recoverable only when the shared metadata describes one file.
                count = self.db.query(DatasetFile.id).join(Dataset, Dataset.id == DatasetFile.dataset_id).filter(
                    Dataset.assay_id == assay_id, Dataset.dataset_kind == "raw",
                    DatasetFile.file_role == "thermo_raw", DatasetFile.sample_id == sample.id).count()
                if count > 1:
                    raise bad_request("Apply the acquisition migration and correct ambiguous acquisition metadata before launching")
                link = self._assay_sample_links_by_sample_id(assay_id, [sample.id]).get(sample.id)
                if link is None:
                    link = AssaySampleLink(assay_id=assay_id, sample_id=sample.id, sample_role="input", annotations_json={})
                    self.db.add(link)
                _, values = split_acquisition_annotations(effective_sample_annotations(
                    sample.annotations_json, link.annotations_json if link else {}))
                acquisition = AcquisitionService(self.db).create(assay_id, sample.id, values)
                file.acquisition = acquisition
            if acquisition.assay_id != assay_id or acquisition.sample_id != sample.id:
                raise bad_request("Selected file does not match its acquisition's assay and sample")
            if acquisition.id in seen:
                raise bad_request("Select only one file representation of each acquisition")
            seen.add(acquisition.id)
            values = annotations_dict(acquisition.annotations_json)
            if not acquisition.metadata_review_required and not str(values.get("batch_id") or "").strip():
                acquisition.annotations_json = {**values, "batch_id": "1"}
        self.db.flush()

    def _validate_required_sample_metadata(self, assay_id: int, files: list[DatasetFile]) -> None:
        sample_ids = [int(file.sample_id) for file in files if file.sample_id is not None]
        samples = self.db.query(Sample).filter(Sample.id.in_(sample_ids)).all() if sample_ids else []
        sample_by_id = {int(sample.id): sample for sample in samples}
        links_by_sample_id = self._assay_sample_links_by_sample_id(assay_id, sample_ids)

        missing_rows: list[str] = []
        for file in files:
            sample = sample_by_id.get(int(file.sample_id)) if file.sample_id is not None else None
            link = links_by_sample_id.get(int(file.sample_id)) if file.sample_id is not None else None
            link_annotations = annotations_dict(link.annotations_json) if link is not None else {}
            effective_annotations = effective_sample_annotations(sample.annotations_json if sample is not None else {}, link_annotations,
                file.acquisition.annotations_json if file.acquisition else None)
            missing_fields = self._missing_downstream_sample_fields(sample, effective_annotations)
            if file.acquisition and file.acquisition.metadata_review_required:
                missing_fields.append("acquisition metadata review")
            if missing_fields:
                label = sample.name if sample is not None else "no linked sample"
                missing_rows.append(f"{file.original_filename} ({label}): {', '.join(missing_fields)}")

        if missing_rows:
            raise bad_request(
                "Selected workflow input files are missing required sample metadata: "
                + "; ".join(missing_rows)
            )

    @staticmethod
    def _missing_downstream_sample_fields(sample: Sample | None, annotations: dict | None = None) -> list[str]:
        if sample is None:
            return ["sample", "group_label", "batch_id", "injection_order"]

        annotations = annotations_dict(annotations if annotations is not None else sample.annotations_json)
        missing: list[str] = []
        if not str(annotations.get("group_label") or "").strip():
            missing.append("group_label")
        if not str(annotations.get("batch_id") or "").strip():
            missing.append("batch_id")

        injection_order = annotations.get("injection_order")
        if injection_order in (None, ""):
            missing.append("injection_order")
        else:
            try:
                if int(injection_order) < 1:
                    missing.append("injection_order")
            except (TypeError, ValueError):
                missing.append("injection_order")
        return missing

    @staticmethod
    def select_reference_file(files: list[DatasetFile], parameters: dict) -> DatasetFile:
        reference_file_id = parameters.get("reference_file_id")
        if reference_file_id is None:
            return files[0]

        try:
            normalized_id = int(reference_file_id)
        except (TypeError, ValueError) as exc:
            raise bad_request("reference_file_id must be an integer") from exc

        for file in files:
            if file.id == normalized_id:
                return file
        raise bad_request("reference_file_id must be one of the selected workflow input files")

    @staticmethod
    def _selected_review_stage(transitions: dict[str, str], selected: list[str], start: str | None = None) -> str:
        """Find the next selected stop in the frozen chain, independent of JSON key order."""
        if (not isinstance(selected, list) or any(not isinstance(stage, str) for stage in selected)
                or len(selected) != len(set(selected)) or set(selected) - transitions.keys()):
            raise bad_request('review_stages must contain unique supported review stages')
        if not transitions:
            return 'full'
        roots = transitions.keys() - set(transitions.values())
        if len(roots) != 1:
            raise bad_request('Workflow review transitions must form one ordered chain')
        stage = next(iter(roots))
        path = []
        while stage in transitions:
            if stage in path:
                raise bad_request('Workflow review transitions contain a cycle')
            path.append(stage)
            stage = transitions[stage]
        path.append(stage)
        if len(path) != len(transitions) + 1:
            raise bad_request('Workflow review transitions must form one ordered chain')
        if start is not None:
            if start not in path:
                raise bad_request('Current stage is outside the frozen review chain')
            path = path[path.index(start):]
        return next((stage for stage in path if stage in selected), path[-1])

    @staticmethod
    def _validate_sirius_parameters(workflow, parameters: dict) -> None:
        annotation_mode = parameters.get("sirius_annotation_mode")
        if annotation_mode in (None, ""):
            return

        properties = (workflow.parameter_schema_json or {}).get("properties", {})
        allowed_modes = properties.get("sirius_annotation_mode", {}).get("enum", [])
        if str(annotation_mode) not in allowed_modes:
            raise bad_request(
                "sirius_annotation_mode must be one of "
                + ", ".join(allowed_modes)
            )

    @staticmethod
    def _review_stage_for(parameter_values: dict, status_payload: dict, transitions: dict) -> str | None:
        for payload in (parameter_values, status_payload):
            stage = str(payload.get("workflow_stage") or "").strip()
            if stage in transitions:
                return stage
        return None

    def _is_failed_is_qc_review(self, job: WorkflowJob, status_payload: dict) -> bool:
        status = str(status_payload.get("is_qc_status") or "").strip().lower()
        if status in {"fail", "failed"}:
            return True
        summary = status_payload.get("is_qc_summary")
        if isinstance(summary, dict):
            return str(summary.get("overall_status") or "").strip().lower() in {"fail", "failed"}
        artifact_summary = self._load_is_qc_summary_artifact(job.id)
        return str(artifact_summary.get("overall_status") or "").strip().lower() in {"fail", "failed"}

    def _load_is_qc_summary_artifact(self, workflow_job_id: int) -> dict:
        row = (
            self.db.query(DatasetFile)
            .join(WorkflowRunArtifact, WorkflowRunArtifact.dataset_file_id == DatasetFile.id)
            .filter(
                WorkflowRunArtifact.workflow_job_id == workflow_job_id,
                WorkflowRunArtifact.artifact_role == "is_qc_summary",
            )
            .first()
        )
        if row is None:
            return {}
        parsed = urlparse(row.storage_uri or "")
        if parsed.scheme != "file":
            return {}
        path = Path(unquote(parsed.path)).expanduser()
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @classmethod
    def _normalize_execution_target(cls, workflow, parameters: dict) -> None:
        """Validate and store the worker/Nextflow profile pair for a real job."""

        requested_execution_profile = parameters.get("execution_profile")
        execution_profile = str(requested_execution_profile or workflow.execution_profile or "local_docker").strip()
        if execution_profile not in cls.NEXTFLOW_PROFILES_BY_EXECUTION_PROFILE:
            supported = ", ".join(sorted(cls.NEXTFLOW_PROFILES_BY_EXECUTION_PROFILE))
            raise bad_request(f"Unsupported execution_profile '{execution_profile}'. Supported values: {supported}")

        requested_nextflow_profile = parameters.get("nextflow_profile")
        if requested_nextflow_profile not in (None, ""):
            nextflow_profile = str(requested_nextflow_profile).strip()
        elif requested_execution_profile not in (None, ""):
            nextflow_profile = cls.DEFAULT_NEXTFLOW_PROFILE_BY_EXECUTION_PROFILE[execution_profile]
        else:
            nextflow_profile = str(
                workflow.default_nextflow_profile
                or workflow.container_profile
                or cls.DEFAULT_NEXTFLOW_PROFILE_BY_EXECUTION_PROFILE[execution_profile]
            ).strip()

        allowed_nextflow_profiles = cls.NEXTFLOW_PROFILES_BY_EXECUTION_PROFILE[execution_profile]
        if nextflow_profile not in allowed_nextflow_profiles:
            allowed = ", ".join(sorted(allowed_nextflow_profiles))
            raise bad_request(
                f"nextflow_profile '{nextflow_profile}' is not valid for execution_profile "
                f"'{execution_profile}'. Allowed nextflow_profile value(s): {allowed}"
            )

        parameters["execution_profile"] = execution_profile
        parameters["nextflow_profile"] = nextflow_profile
