from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.models import User
from app.schemas.dataset_file import DatasetFileFinalize, DatasetFileOut, DatasetFileRegister
from app.services.file_service import FileService

router = APIRouter()


@router.post("/studies/{study_id}/files/register", response_model=DatasetFileOut)
def register_file(
    study_id: int,
    payload: DatasetFileRegister,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return FileService(db).register(user.id, study_id, payload)


@router.post("/files/{file_id}/finalize", response_model=DatasetFileOut)
def finalize_file(
    file_id: int,
    payload: DatasetFileFinalize,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return FileService(db).finalize(user.id, file_id, payload)


@router.get("/studies/{study_id}/files", response_model=list[DatasetFileOut])
def list_files(study_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return FileService(db).list_for_study(user.id, study_id)


@router.post("/studies/{study_id}/files/upload-thermo-raw", response_model=DatasetFileOut)
def upload_thermo_raw_file(
    study_id: int,
    sample_id: int | None = Form(None),
    raw_file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return FileService(db).upload_thermo_raw(
        user_id=user.id,
        study_id=study_id,
        upload=raw_file,
        sample_id=sample_id,
    )
