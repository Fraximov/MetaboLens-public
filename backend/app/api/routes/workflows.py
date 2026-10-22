from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.models import User
from app.schemas.workflow import WorkflowDefinitionOut, WorkflowJobOut, WorkflowLibraryOptionsOut, WorkflowTemplateContentOut
from app.services.workflow_definition_service import WorkflowDefinitionService
from app.services.workflow_job_read_service import WorkflowJobReadService

router = APIRouter()


@router.get("/workflows", response_model=list[WorkflowDefinitionOut])
def list_workflows(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return WorkflowDefinitionService(db).list_definitions()


@router.get("/workflows/{workflow_definition_id}/libraries", response_model=WorkflowLibraryOptionsOut)
def list_workflow_libraries(
    workflow_definition_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowDefinitionService(db).list_libraries(user.id, workflow_definition_id)


@router.get("/workflows/{workflow_definition_id}/msdial-template", response_model=WorkflowTemplateContentOut)
def get_msdial_template_content(
    workflow_definition_id: int,
    path: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowDefinitionService(db).get_msdial_template_content(user.id, workflow_definition_id, path)


@router.get('/workflows/{workflow_definition_id}/masscube-template', response_model=WorkflowTemplateContentOut)
def get_masscube_template_content(
    workflow_definition_id: int,
    path: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return WorkflowDefinitionService(db).get_masscube_template_content(user.id, workflow_definition_id, path)


@router.get("/workflow-jobs/{job_id}", response_model=WorkflowJobOut)
def get_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return WorkflowJobReadService(db).get_job(user.id, job_id)
