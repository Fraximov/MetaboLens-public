from fastapi import APIRouter

from app.api.routes import admin, assays, auth, files, metadata, qc, studies, workflows

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(studies.router, prefix="/studies", tags=["studies"])
api_router.include_router(assays.router, prefix="/assays", tags=["assays"])
api_router.include_router(metadata.router, tags=["metadata"])
api_router.include_router(files.router, tags=["files"])
api_router.include_router(workflows.router, tags=["workflows"])
api_router.include_router(qc.router, tags=["qc"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
