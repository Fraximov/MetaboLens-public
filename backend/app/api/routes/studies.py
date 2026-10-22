from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session
from fastapi.responses import FileResponse

from app.core.errors import bad_request
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.models import User
from app.schemas.assay import AssayCreate, AssayOut
from app.schemas.study import (
    RawFolderBrowseRequest,
    RawFolderBrowseResponse,
    RawFolderScanItem,
    RawFolderScanRequest,
    SampleBulkCreateItem,
    SampleBulkPathCreateItem,
    SampleCreate,
    SampleDeleteOut,
    SampleOut,
    SamplePatch,
    StudyActivityEventOut,
    StudyCreate,
    StudyListOut,
    StudyOut,
    StudyPatch,
    StudyStructureOut,
    StudySummaryOut,
)
from app.services.study_service import StudyService
from app.services.file_service import FileService
from app.schemas.study_package import PackageCheck, PackageRequest
from app.services.study_packaging_service import StudyPackagingService
from app.schemas.study_metadata import StudyMetadataView, StudySubmissionMetadata
from app.services.study_metadata_service import StudyMetadataService

router = APIRouter()


@router.get("/{study_id}/submission-metadata", response_model=StudyMetadataView)
def get_submission_metadata(study_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyMetadataService(db).get(user.id, study_id)


@router.put("/{study_id}/submission-metadata", response_model=StudyMetadataView)
def save_submission_metadata(study_id: int, payload: StudySubmissionMetadata,
                             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyMetadataService(db).save(user.id, study_id, payload)


@router.get("/{study_id}/packaging")
def packaging_choices(study_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyPackagingService(db).choices(user.id, study_id)


@router.post("/{study_id}/packaging/check")
def check_package(study_id: int, payload: PackageCheck, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyPackagingService(db).check(user.id, study_id, payload)


@router.get("/{study_id}/packages")
def list_packages(study_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyPackagingService(db).list(user.id, study_id)


@router.post("/{study_id}/packages", status_code=202)
def request_package(study_id: int, payload: PackageRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service = StudyPackagingService(db)
    return service.serialize(service.request_build(user.id, study_id, payload))


@router.get("/{study_id}/packages/{package_id}")
def get_package(study_id: int, package_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service = StudyPackagingService(db)
    return service.serialize(service.get(user.id, study_id, package_id))


@router.post("/{study_id}/packages/{package_id}/export", status_code=202)
def export_package(study_id: int, package_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service = StudyPackagingService(db)
    return service.serialize(service.request_export(user.id, study_id, package_id))


@router.delete("/{study_id}/packages/{package_id}")
def delete_package(study_id: int, package_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyPackagingService(db).delete(user.id, study_id, package_id)


@router.get("/{study_id}/packages/{package_id}/files")
def package_files(study_id: int, package_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyPackagingService(db).files(user.id, study_id, package_id)


@router.get("/{study_id}/packages/{package_id}/files/{path:path}")
def package_file(study_id: int, package_id: int, path: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    return StudyPackagingService(db).file_preview(user.id, study_id, package_id, path)


@router.get("/{study_id}/packages/{package_id}/metadata")
def package_metadata(study_id: int, package_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    path = StudyPackagingService(db).metadata_download(user.id, study_id, package_id)
    return FileResponse(path, media_type="application/zip", filename=f"study-{study_id}-package-{package_id}-metadata-review.zip")


@router.post("", response_model=StudyOut)
def create_study(
    payload: StudyCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    studies = StudyService(db)
    return studies.serialize_study(studies.create(user.id, payload))


@router.get("", response_model=list[StudyListOut])
def list_studies(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyService(db).list_for_user(user.id)


@router.get("/{study_id}", response_model=StudyOut)
def get_study(study_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    studies = StudyService(db)
    return studies.serialize_study(studies.get(user.id, study_id))


@router.get("/{study_id}/summary", response_model=StudySummaryOut)
def get_study_summary(study_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyService(db).get_summary(user.id, study_id)


@router.get("/{study_id}/structure", response_model=StudyStructureOut)
def get_study_structure(study_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyService(db).get_structure(user.id, study_id)


@router.get("/{study_id}/activity", response_model=list[StudyActivityEventOut])
def get_study_activity(
    study_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return StudyService(db).list_activity(user.id, study_id, limit=limit)


@router.patch("/{study_id}", response_model=StudyOut)
def patch_study(
    study_id: int,
    payload: StudyPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    studies = StudyService(db)
    return studies.serialize_study(studies.patch(user.id, study_id, payload))


@router.post("/{study_id}/submit", response_model=StudyOut)
def submit_study(study_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    studies = StudyService(db)
    return studies.serialize_study(studies.submit(user.id, study_id))


@router.post("/{study_id}/reopen", response_model=StudyOut)
def reopen_study(study_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    studies = StudyService(db)
    return studies.serialize_study(studies.reopen_to_draft(user.id, study_id))


@router.post("/{study_id}/transition/{new_status}", response_model=StudyOut)
def transition_study(
    study_id: int,
    new_status: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    studies = StudyService(db)
    return studies.serialize_study(studies.transition(user.id, study_id, new_status))


@router.post("/{study_id}/samples", response_model=SampleOut)
def create_sample(
    study_id: int,
    payload: SampleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return StudyService(db).add_sample(user.id, study_id, payload)


@router.get("/{study_id}/samples", response_model=list[SampleOut])
def list_samples(study_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyService(db).list_samples(user.id, study_id)


@router.patch("/{study_id}/samples/{sample_id}", response_model=SampleOut)
def update_sample(
    study_id: int,
    sample_id: int,
    payload: SamplePatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return StudyService(db).update_sample(user.id, study_id, sample_id, payload)


@router.delete("/{study_id}/samples/{sample_id}", response_model=SampleDeleteOut)
def delete_sample(
    study_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return StudyService(db).delete_sample(user.id, study_id, sample_id)


@router.post("/{study_id}/samples/with-files", response_model=list[SampleOut])
def create_samples_with_files(
    study_id: int,
    samples_json: str = Form(...),
    raw_files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        sample_payload = TypeAdapter(list[SampleBulkCreateItem]).validate_json(samples_json)
    except ValidationError as exc:
        raise bad_request(f"Invalid samples_json payload: {exc.errors()}")

    return FileService(db).create_samples_with_files(
        user_id=user.id,
        study_id=study_id,
        samples_payload=sample_payload,
        uploads=raw_files,
    )


@router.post("/{study_id}/samples/with-paths", response_model=list[SampleOut])
def create_samples_with_paths(
    study_id: int,
    payload: list[SampleBulkPathCreateItem],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return FileService(db).create_samples_with_paths(
        user_id=user.id,
        study_id=study_id,
        rows=payload,
    )


@router.post("/{study_id}/raw-paths/scan", response_model=list[RawFolderScanItem])
def scan_raw_folder(
    study_id: int,
    payload: RawFolderScanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return FileService(db).scan_raw_folder(
        user_id=user.id,
        study_id=study_id,
        folder_path=payload.folder_path,
    )


@router.post("/{study_id}/raw-paths/browse", response_model=RawFolderBrowseResponse)
def browse_raw_folders(
    study_id: int,
    payload: RawFolderBrowseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return FileService(db).browse_raw_folders(
        user_id=user.id,
        study_id=study_id,
        folder_path=payload.folder_path,
    )


@router.get("/{study_id}/assays", response_model=list[AssayOut])
def list_study_assays(study_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return StudyService(db).list_assays(user.id, study_id)


@router.post("/{study_id}/assays", response_model=AssayOut)
def create_study_assay(
    study_id: int,
    payload: AssayCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return StudyService(db).create_assay(user.id, study_id, payload)
