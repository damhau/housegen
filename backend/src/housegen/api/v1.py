from fastapi import APIRouter
from pydantic import BaseModel

from housegen.core.config import get_settings
from housegen.projects.router import router as projects_router

router = APIRouter(prefix="/api/v1")
router.include_router(projects_router)


class HealthOut(BaseModel):
    status: str
    # the running build: image tag ("1.2.3" or "sha-abc1234"), "dev" outside the image
    version: str
    commit: str
    env: str


@router.get("/health", tags=["meta"])
def health() -> HealthOut:
    s = get_settings()
    return HealthOut(status="ok", version=s.APP_VERSION, commit=s.APP_COMMIT, env=s.ENV)
