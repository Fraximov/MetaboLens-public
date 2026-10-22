from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.models.models import User
from app.schemas.qc import MzMLQCReport
from app.services.qc_service import QCService

router = APIRouter()


@router.get("/qc/example-mzml", response_model=MzMLQCReport)
def example_mzml_qc(_: User = Depends(get_current_user)):
    return QCService().analyze_example_mzml()
