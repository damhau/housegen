"""One-off data migrations run at startup (idempotent): projects from before accounts (#104), the
default organisation's limits (#107)."""

from __future__ import annotations

import logging

from sqlalchemy import text

from housegen.accounts import crud
from housegen.core.db import session_factory
from housegen.limits.service import NO_LIMITS

logger = logging.getLogger(__name__)


async def migrate_organisations() -> str:
    """Every project without an organisation moves into the default one (made the first time),
    which has no limits until the admin sets some. Returns its id."""
    async with session_factory()() as session, session.begin():
        # the column came by ALTER TABLE on an existing database, which adds no index
        await session.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_projects_organisation_id "
                "ON projects (organisation_id)"
            )
        )
        organisation, created = await crud.default_organisation(session)
        moved = await crud.adopt_unassigned_projects(session, organisation.id)
        # the column came by ALTER TABLE, null: the limits were never set
        lifted = organisation.limits_json is None
        if lifted:
            organisation.limits_json = NO_LIMITS
    logger.info(
        "accounts.default_organisation",
        extra={
            "organisation_id": organisation.id,
            "organisation_name": organisation.name,
            "organisation_created": created,
            "projects_moved": moved,
            "limits_lifted": lifted,
        },
    )
    return organisation.id
