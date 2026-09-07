from fastapi import APIRouter
from pydantic import BaseModel

from housegen.core.config import get_settings
from housegen.core.db import DbSession
from housegen.projects import crud
from housegen.projects.router import router as projects_router
from housegen.projects.schemas import SharedProjectOut
from housegen.projects.storage import ProjectStorage

router = APIRouter(prefix="/api/v1")
router.include_router(projects_router)


@router.get("/shared/{token}", tags=["shared"])
async def shared_project(session: DbSession, token: str) -> SharedProjectOut:
    """The read-only view behind a share link (#24): the scene (current or pinned version),
    its renders and photos. Nothing that could change the project, nothing the owner wrote."""
    project = await crud.project_by_share_token(session, token)
    st = ProjectStorage(project.id)
    pinned = project.share_version is not None and any(
        v.number == project.share_version for v in project.versions
    )
    version = project.share_version if pinned else project.current_version
    return SharedProjectOut(
        name=project.name,
        version=version,
        pinned=pinned,
        scene_url=st.scene_url(version) if version else st.scene_url(),
        render_urls=st.render_urls(version) if version else {},
        photo_urls=[st.photo_url(p.filename) for p in project.photos],
        created_at=project.created_at,
    )


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
