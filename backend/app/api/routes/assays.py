from app.services.workflow_feature_review_service import WorkflowFeatureReviewService
from app.schemas.workflow import WorkflowFeatureReviewPatch
from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.models import User
from app.schemas.dataset_file import DatasetFileOut
from app.schemas.metadata import MetadataOut, MetadataSubmit
from app.schemas.study import RawFolderImportRequest, RawFolderImportResponse, SampleBulkPathCreateItem, SampleOut
from app.schemas.workflow import WorkflowContinueRequest, WorkflowEicFeatureOut, WorkflowEicSummaryOut, WorkflowEicTraceOut, WorkflowJobOut, WorkflowLaunch, WorkflowOutputFileOut
from app.services.file_service import FileService
from app.services.metadata_service import MetadataService
from app.schemas.workflow import WorkflowFeatureMapOut, WorkflowFeaturePageOut, WorkflowFeatureTraceOut
from app.services.workflow_feature_service import WorkflowFeatureService
from app.services.workflow_eic_service import WorkflowEicService
from app.services.workflow_is_qc_service import WorkflowIsQcService
from app.services.workflow_job_deletion_service import WorkflowJobDeletionService
from app.services.workflow_job_read_service import WorkflowJobReadService
from app.services.workflow_launch_service import WorkflowLaunchService
from app.services.acquisition_service import AcquisitionService
from app.schemas.acquisition import AcquisitionOut, AcquisitionPatch

router = APIRouter()


@router.get("/{assay_id}/acquisitions", response_model=list[AcquisitionOut])
def list_acquisitions(assay_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return AcquisitionService(db).list_for_assay(user.id, assay_id)


@router.patch("/{assay_id}/acquisitions/{acquisition_id}", response_model=AcquisitionOut)
def update_acquisition(assay_id: int, acquisition_id: int, payload: AcquisitionPatch,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return AcquisitionService(db).update(user.id, assay_id, acquisition_id, payload)


@router.post("/{assay_id}/metadata", response_model=MetadataOut)
def submit_metadata(
    assay_id: int,
    payload: MetadataSubmit,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return MetadataService(db).submit(user.id, assay_id, payload.payload)


@router.get("/{assay_id}/metadata", response_model=MetadataOut | None)
def get_latest_metadata(assay_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return MetadataService(db).latest(user.id, assay_id)


@router.post("/{assay_id}/workflows/{workflow_definition_id}/launch", response_model=WorkflowJobOut)
def launch_workflow(
    assay_id: int,
    workflow_definition_id: int,
    payload: WorkflowLaunch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowLaunchService(db).launch(
        user.id,
        assay_id,
        workflow_definition_id,
        payload.parameters,
        payload.file_ids,
        payload.msdial_template_text,
        review_stages=payload.review_stages,
        metabolensr_profile=payload.metabolensr_profile,
        metabolensr_enabled_rules=payload.metabolensr_enabled_rules,
        metabolensr_expected_image=payload.metabolensr_expected_image,
        masscube_template_text=payload.masscube_template_text,
    )


@router.post("/{assay_id}/workflows/{workflow_definition_id}/hpc-smoke-tests/slurm-ssh", response_model=WorkflowJobOut)
def launch_hpc_smoke_test(
    assay_id: int,
    workflow_definition_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowLaunchService(db).launch_hpc_smoke_test(user.id, assay_id, workflow_definition_id)


@router.get("/{assay_id}/workflow-jobs", response_model=list[WorkflowJobOut])
def list_jobs(assay_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return WorkflowJobReadService(db).list_jobs(user.id, assay_id)


@router.delete("/{assay_id}/workflow-jobs/{job_id}", response_model=WorkflowJobOut)
def delete_workflow_job(
    assay_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowJobDeletionService(db).soft_delete(user.id, assay_id, job_id)


@router.post("/{assay_id}/workflow-jobs/{job_id}/continue", response_model=WorkflowJobOut)
def continue_workflow_job(
    job_id: int,
    assay_id: int,
    payload: WorkflowContinueRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowLaunchService(db).continue_review_stage(
        user.id,
        assay_id,
        job_id,
        operator_note=payload.operator_note if payload else None,
        apply_rt_correction=payload.apply_rt_correction if payload else None,
        rt_correction_target_ids=payload.rt_correction_target_ids if payload else None,
        preview_file_id=payload.preview_file_id if payload else None,
        expected_revision=payload.expected_revision if payload else None,
    )


@router.get("/{assay_id}/workflow-output-files", response_model=list[WorkflowOutputFileOut])
def list_workflow_output_files(
    assay_id: int,
    job_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowJobReadService(db).list_output_files(user.id, assay_id, job_id=job_id)


@router.get("/{assay_id}/workflow-output-files/{file_id}/download")
def download_workflow_output_file(file_id: int, assay_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    path, media_type, filename = WorkflowJobReadService(db).get_output_file_download(user.id, assay_id, file_id)
    return FileResponse(path, media_type=media_type, filename=filename)


@router.get("/{assay_id}/workflow-is-qc/{job_id}/summary")
def get_workflow_is_qc_summary(
    job_id: int,
    assay_id: int,
    rt_correction_target_id: list[str] | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowIsQcService(db).get_summary(
        user.id, assay_id, job_id, rt_correction_target_id
    )


@router.get("/{assay_id}/workflow-is-qc/{job_id}/targets")
def list_workflow_is_qc_targets(
    job_id: int,
    assay_id: int,
    rt_correction_target_id: list[str] | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowIsQcService(db).list_targets(
        user.id, assay_id, job_id, rt_correction_target_id
    )


@router.get("/{assay_id}/workflow-is-qc/{job_id}/traces")
def get_workflow_is_qc_traces(
    job_id: int,
    assay_id: int,
    target_name: str | None = None,
    rt_correction_target_id: list[str] | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowIsQcService(db).get_traces(
        user.id,
        assay_id,
        job_id,
        target_name=target_name,
        rt_correction_target_ids=rt_correction_target_id,
    )


@router.get("/{assay_id}/workflow-eic/{eic_file_id}/summary", response_model=WorkflowEicSummaryOut)
def get_workflow_eic_summary(eic_file_id: int, assay_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return WorkflowEicService(db).get_summary(user.id, assay_id, eic_file_id)


@router.get("/{assay_id}/workflow-eic/{eic_file_id}/features", response_model=list[WorkflowEicFeatureOut])
def list_workflow_eic_features(eic_file_id: int, assay_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return WorkflowEicService(db).list_features(user.id, assay_id, eic_file_id)


@router.get("/{assay_id}/workflow-eic/{eic_file_id}/features/search", response_model=list[WorkflowEicFeatureOut])
def search_workflow_eic_features(
    eic_file_id: int,
    assay_id: int,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    include_feature_index: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowEicService(db).search_features(user.id, assay_id, eic_file_id, q, limit=limit, include_feature_index=include_feature_index)


@router.get("/{assay_id}/workflow-eic/{eic_file_id}/features/{feature_index}", response_model=WorkflowEicTraceOut)
def get_workflow_eic_trace(eic_file_id: int, feature_index: int, assay_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return WorkflowEicService(db).get_feature_trace(user.id, assay_id, eic_file_id, feature_index)


@router.get("/{assay_id}/files", response_model=list[DatasetFileOut])
def list_assay_files(
    assay_id: int,
    workflow_inputs_only: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return FileService(db).list_for_assay(
        user.id,
        assay_id,
        workflow_inputs_only=workflow_inputs_only,
    )


@router.post("/{assay_id}/samples/with-paths", response_model=list[SampleOut])
def create_assay_samples_with_paths(
    assay_id: int,
    payload: list[SampleBulkPathCreateItem],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return FileService(db).create_samples_with_paths_for_assay(
        user_id=user.id,
        assay_id=assay_id,
        rows=payload,
    )


@router.post("/{assay_id}/raw-paths/import", response_model=RawFolderImportResponse)
def import_assay_raw_folder(
    assay_id: int,
    payload: RawFolderImportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return FileService(db).import_raw_folder_for_assay(
        user_id=user.id,
        assay_id=assay_id,
        folder_path=payload.folder_path,
    )


@router.get('/{assay_id}/workflow-jobs/{job_id}/features', response_model=WorkflowFeaturePageOut)
def list_workflow_features(
    assay_id: int, job_id: int, preview_file_id: int,
    q: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(200, ge=1, le=200),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return WorkflowFeatureService(db).list_features(user.id, assay_id, job_id, preview_file_id,
                                                   query=q, page=page, page_size=page_size)


@router.get('/{assay_id}/workflow-jobs/{job_id}/features/map', response_model=list[WorkflowFeatureMapOut])
def workflow_feature_map(
    assay_id: int, job_id: int, preview_file_id: int, q: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return WorkflowFeatureService(db).feature_map(user.id, assay_id, job_id, preview_file_id, query=q)


@router.get('/{assay_id}/workflow-jobs/{job_id}/features/trace', response_model=WorkflowFeatureTraceOut)
def workflow_feature_trace(
    assay_id: int, job_id: int, preview_file_id: int, feature_id: str = Query(..., min_length=1),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return WorkflowFeatureService(db).get_feature_trace(user.id, assay_id, job_id, preview_file_id, feature_id)


@router.get('/{assay_id}/workflow-jobs/{job_id}/feature-review')
def get_feature_review(assay_id: int, job_id: int, preview_file_id: int, queue: str = 'include',
                       domain: str = 'statistics', q: str | None = None, effective: str | None = None,
                       has_ms2: bool | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return WorkflowFeatureReviewService(db).get(user.id, assay_id, job_id, preview_file_id,
        queue=queue, domain=domain, query=q, effective=effective, has_ms2=has_ms2, page=page, page_size=page_size)


@router.patch('/{assay_id}/workflow-jobs/{job_id}/feature-review')
def patch_feature_review(assay_id: int, job_id: int, payload: WorkflowFeatureReviewPatch,
                         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return WorkflowFeatureReviewService(db).patch(user.id, assay_id, job_id, **payload.model_dump())
