from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from housegen.core.db import Base, UTCDateTime


def _now() -> datetime:
    return datetime.now(UTC)


class UsageRecord(Base):
    """A job an organisation started, for its limits (#107). Not tied to the job's row: deleting a
    project deletes its jobs, and must not give the organisation its day or its month back."""

    __tablename__ = "usage_records"

    job_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, index=True)
    # a listing shoot's spend: its estimate while it runs, what it cost once it is over
    listing_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
