from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.models import User
from app.schemas.metadata import MetadataProfileOut
from app.services.metadata_service import MetadataService

router = APIRouter()


@router.get("/metadata/profiles", response_model=list[MetadataProfileOut])
def list_metadata_profiles(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return MetadataService(db).list_profiles(user.id)
