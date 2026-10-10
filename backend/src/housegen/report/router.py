"""The surfaces and volumes report (#47, #48): its settings and its PDF.

The viewer computes the figures in the scene page (kit/quantities.js) and lays the report out; the
backend keeps what the owner sets (prices, weights, attributions) and prints the report to PDF with
its headless Chromium.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import Response

from housegen.core.db import DbSession
from housegen.core.exceptions import InvalidInputError
from housegen.geo import context as geo_context
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage
from housegen.render import renderer
from housegen.report.schemas import PdfRequest, ReportDefaults, ReportOut, ReportSettings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["report"])

MAX_HTML = 25_000_000  # the report's page with its fonts and plans inline: a few MB
_PLACE = re.compile(r"^(?P<commune>.+?)\s+(?P<number>\d{1,6})\s*\(")


def _parcel(st: ProjectStorage) -> str | None:
    """'BF n° 3013, Le Mont-sur-Lausanne': the surroundings' parcel, else the plans' title block."""
    ctx = geo_context.load(st.root)
    if ctx and ctx.get("place", {}).get("kind") == "parcel":
        m = _PLACE.match(str(ctx["place"].get("label", "")))
        if m:
            return f"BF n° {m['number']}, {m['commune']}"
    hint = geo_context.suggestion_from_plans([st.plan_pdf(d) for d in st.plan_documents()])
    if hint:
        number, _, commune = hint.partition(" ")
        return f"BF n° {number}, {commune}" if commune else f"BF n° {number}"
    return None


def _defaults(st: ProjectStorage) -> ReportDefaults:
    ctx = geo_context.load(st.root)
    align = (ctx or {}).get("alignment") or {}
    aligned = bool(align.get("set"))
    return ReportDefaults(
        parcel=_parcel(st),
        north=float(align["rotation"]) if aligned else None,
        datum=float(align["ground"]) if aligned else None,
    )


@router.get("/{project_id}/report")
async def get_report(session: DbSession, project_id: str) -> ReportOut:
    """The report's settings (prices, weights, attributions…) and what fills the blanks."""
    project = await crud.get_project(session, project_id)
    settings = (
        ReportSettings.model_validate_json(project.report_json)
        if project.report_json
        else ReportSettings()
    )
    return ReportOut(settings=settings, defaults=_defaults(ProjectStorage(project_id)))


@router.put("/{project_id}/report")
async def put_report(session: DbSession, project_id: str, body: ReportSettings) -> ReportOut:
    """Save the report's settings (the whole set: what is left out goes back to its default)."""
    await crud.set_report_settings(session, project_id, body.model_dump(exclude_defaults=True))
    await session.commit()
    logger.info("report.settings", extra={"project_id": project_id})
    return ReportOut(settings=body, defaults=_defaults(ProjectStorage(project_id)))


@router.post(
    "/{project_id}/report/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
    include_in_schema=False,
)
async def report_pdf(session: DbSession, project_id: str, body: PdfRequest) -> Response:
    """The report as the viewer laid it out, printed to A4 by the headless browser (no script runs,
    nothing is fetched: the page carries its styles, fonts and drawings)."""
    await crud.get_project(session, project_id)
    if len(body.html) > MAX_HTML:
        raise InvalidInputError("the report page is too large to print")
    pdf = await renderer.local.pdf(body.html)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", body.filename).strip("-") or "rapport.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    logger.info("report.pdf", extra={"project_id": project_id, "bytes": len(pdf)})
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )
