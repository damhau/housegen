import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from housegen.api.v1 import router as api_router
from housegen.core.config import get_settings
from housegen.core.db import dispose_db, init_db
from housegen.core.exceptions import register_exception_handlers
from housegen.core.logging import RequestLoggingMiddleware, configure_logging
from housegen.jobs.manager import job_manager
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
            "provider": settings.LLM_PROVIDER,
            "data_dir": str(settings.DATA_DIR),
        },
    )
    yield
    await job_manager.shutdown()
    await renderer.close()
    await dispose_db()
    logger.info("app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="housegen",
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
    app.mount("/kit", StaticFiles(directory=settings.KIT_DIR), name="kit")
    # per-project files: scene working copy, versions, renders, photos, plan pages
    app.mount("/scenes", StaticFiles(directory=settings.projects_dir), name="scenes")
    return app


app = create_app()
