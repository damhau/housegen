"""The static file mounts: the kit, and a project's files under /scenes/<project>/ for those who may
see the project (#105)."""

from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import Response
from starlette.staticfiles import PathLike, StaticFiles
from starlette.types import Scope

from housegen.core.db import session_factory
from housegen.core.exceptions import AppError, error_response
from housegen.projects.access import check_scene_access


class RevalidatedStaticFiles(StaticFiles):
    """Static files the browser must revalidate on every load (Cache-Control: no-cache, answered
    by a 304 when unchanged). The kit's working copy and a project's scene sources change under
    the same URL, and a browser's heuristic caching would keep showing the old ones: a renderer
    switch in the viewer or a build in progress then looks like nothing happened."""

    def file_response(
        self,
        full_path: PathLike,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache"
        return response


class SceneFiles(RevalidatedStaticFiles):
    """/scenes/<project>/…: the scene working copy, versions, renders, jobs' pictures, photos, plan
    sheets and surroundings of a project, checked by `check_scene_access` before any file is looked
    up."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        project_id = path.replace(os.sep, "/").split("/", 1)[0]
        async with session_factory()() as session:
            try:
                await check_scene_access(Request(scope), session, project_id)
            except AppError as e:
                return error_response(e)
        return await super().get_response(path, scope)
