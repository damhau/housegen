"""One-off data migrations run at startup (idempotent)."""

from __future__ import annotations

import logging
from pathlib import Path

from housegen.core.db import session_factory
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage, migrate_plan_layout

logger = logging.getLogger(__name__)


async def migrate_plans(projects_dir: Path) -> int:
    """Projects from before #10: move their single plan set under plans/1/ and give them a
    PlanDocument row. Returns how many projects were migrated."""
    moved = migrate_plan_layout(projects_dir)
    n = 0
    async with session_factory()() as session, session.begin():
        for project in await crud.projects_without_plan_documents(session):
            st = ProjectStorage(project.id)
            docs = st.plan_documents()
            if not docs:
                continue
            pages = len([sh for sh in st.plan_sheets() if sh.document == docs[0]])
            label = Path(project.plan_filename).stem if project.plan_filename else "Plans"
            project.plan_pages = 0  # add_plan_document counts the pages back in
            await crud.add_plan_document(
                session, project, docs[0], label, project.plan_filename or "", pages
            )
            n += 1
    if moved or n:
        logger.info("plans.migrated", extra={"moved": len(moved), "rows": n})
    return n
