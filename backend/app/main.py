from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from app.api.router import api_router
from app.config import get_settings
from app.db.session import SessionLocal
from app.services.seed_service import SeedService

settings = get_settings()

app = FastAPI(title="MetaboLens MVP API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The aligned-feature listing is the response that makes this worth having: about
# 2.4 MB for an 8.7k feature run, and roughly a tenth of that compressed, because
# it is one JSON key set repeated per feature. Ships with Starlette, so it is not
# a new dependency. Responses under the threshold are sent as they are.
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.on_event("startup")
def startup_event() -> None:
    with SessionLocal() as db:
        SeedService(db).run()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "backend"}


app.include_router(api_router)
