from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.models import User
from app.schemas.admin import RoleAssignmentCreate
from app.schemas.common import MessageResponse
from app.services.role_service import RoleService

router = APIRouter()


@router.post("/roles", response_model=MessageResponse)
def add_role(
    payload: RoleAssignmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    RoleService(db).grant(current_user.id, payload.user_id, payload.role, payload.study_id)
    return MessageResponse(message="role assigned")
