from fastapi import APIRouter

from housegen.projects.router import router as projects_router

router = APIRouter(prefix="/api/v1")
router.include_router(projects_router)


@router.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
