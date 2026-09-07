import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from housegen.agent.pipeline import resume_interrupted_jobs
from housegen.api.v1 import router as api_router
from housegen.core.config import get_settings
from housegen.core.db import dispose_db, init_db
from housegen.core.exceptions import register_exception_handlers
from housegen.core.logging import RequestLoggingMiddleware, configure_logging
from housegen.jobs.manager import job_manager
from housegen.projects.migrations import migrate_plans
from housegen.render.renderer import renderer

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, settings.ENV)
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    logger.info(
        "app.startup",
        extra={
            "env": settings.ENV,
            "version": settings.APP_VERSION,
            "commit": settings.APP_COMMIT,
            "provider": settings.LLM_PROVIDER,
            "data_dir": str(settings.DATA_DIR),
        },
    )
    await migrate_plans(settings.projects_dir)  # one-off: plan.pdf + plan/ → plans/1/ (#10)
    # jobs the previous process left behind (deploy, reload, crash) continue with the same id
    resumed = await resume_interrupted_jobs(job_manager)
    if resumed:
        logger.info("app.resumed_jobs", extra={"count": resumed})
    yield
    await job_manager.shutdown()
    await renderer.close()
    await dispose_db()
    logger.info("app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="housegen",
        # the API contract's version, deliberately not the build's (APP_VERSION, see
        # /health): it is written into every generated client file
        version="0.1.0",
        lifespan=lifespan,
        # short operationIds → clean generated hook names (useListProjects, …)
        generate_unique_id_function=lambda route: route.name,
    )
    app.add_middleware(RequestLoggingMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router)

    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    # scene runtime + component kit + vendored three.js
    app.mount(
        "/kit/vendor/three",
        StaticFiles(directory=settings.KIT_DIR / "node_modules" / "three"),
        name="three",
    )
    eztree = settings.KIT_DIR / "node_modules" / "@dgreenheck" / "ez-tree" / "build"
    if eztree.is_dir():  # vendored tree generator (#15); the kit falls back to its own trees
        app.mount("/kit/vendor/ez-tree", StaticFiles(directory=eztree), name="ez-tree")
    app.mount("/kit", StaticFiles(directory=settings.KIT_DIR), name="kit")
    # per-project files: scene working copy, versions, renders, photos, plan pages
    app.mount("/scenes", StaticFiles(directory=settings.projects_dir), name="scenes")
    if settings.STATIC_DIR is not None:
        mount_spa(app, settings.STATIC_DIR)
    return app


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built SPA from "/" (prod: one process for the API and the frontend).

    Must come last: the history-fallback route catches everything the API, the kit and
    the scene mounts did not.
    """
    static_dir = static_dir.resolve()
    index = static_dir / "index.html"
    if not index.is_file():
        logger.warning("spa.missing", extra={"static_dir": str(static_dir)})
        return
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="spa-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and static_dir in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
