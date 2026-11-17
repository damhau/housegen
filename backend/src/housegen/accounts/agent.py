"""The agent token (#112): Claude starts jobs on dev in one organisation. It reads like a member of
that organisation and writes only through AGENT_WRITES, matched on the route's template: a write
route added later stays refused to it until it is added here on purpose."""

from __future__ import annotations

from fastapi import Request
from fastapi.routing import iter_route_contexts
from starlette.routing import Match

AGENT_WRITES = frozenset(
    {
        ("POST", "/api/v1/projects/{project_id}/modify"),
        ("POST", "/api/v1/projects/{project_id}/chat"),
        ("POST", "/api/v1/projects/{project_id}/interior"),
        ("POST", "/api/v1/projects/{project_id}/versions/{number}/fix"),
        ("POST", "/api/v1/projects/{project_id}/versions/{number}/restore"),
        ("POST", "/api/v1/projects/{project_id}/versions/{number}/restyle"),
        ("POST", "/api/v1/projects/{project_id}/jobs/{job_id}/cancel"),
        ("POST", "/api/v1/projects/{project_id}/listing"),
    }
)


def route_template(request: Request) -> str | None:
    """The path template of the route that matched, with the prefixes of every router above it
    ("/api/v1/projects/{project_id}/chat"). The matched route only knows its own router's."""
    matched = request.scope.get("route")
    if matched is None:
        return None
    for context in iter_route_contexts(request.app.routes):
        if context.original_route is matched and context.matches(request.scope)[0] is Match.FULL:
            return context.path
    return None


def may_write(request: Request) -> bool:
    return (request.method, route_template(request)) in AGENT_WRITES
