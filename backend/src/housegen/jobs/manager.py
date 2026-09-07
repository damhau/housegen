"""In-process background job runner with a per-job event stream.

Good enough for a single-instance deployment. Persisted events (JobEvent) let a
reloaded page replay history; they are also fanned out live to SSE subscribers.
Transient events (progress ticks, text deltas) are fanned out only.
Swap for arq/Celery + Redis pub/sub when scaling out.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from housegen.core.db import session_factory
from housegen.projects import crud

logger = logging.getLogger(__name__)

Event = dict[str, Any]
JobBody = Callable[["JobContext"], Awaitable[None]]


class JobContext:
    def __init__(self, manager: JobManager, job_id: str, project_id: str) -> None:
        self.manager = manager
        self.job_id = job_id
        self.project_id = project_id
        self._seq = 0

    async def emit(self, type_: str, **payload: Any) -> None:
        """Persisted event: replayed on reload, shown in the timeline."""
        self._seq += 1
        ev: Event = {
            "seq": self._seq,
            "type": type_,
            "payload": payload,
            "created_at": datetime.now(UTC).isoformat(),
        }
        async with session_factory()() as session, session.begin():
            await crud.add_job_event(session, self.job_id, self._seq, type_, payload)
        self.manager._fanout(self.job_id, ev)
        logger.info("job.event", extra={"job_id": self.job_id, "event": type_, "seq": self._seq})

    def emit_transient(self, type_: str, **payload: Any) -> None:
        """Live-only event (progress ticks, text deltas): not stored, not replayed."""
        ev: Event = {
            "seq": 0,
            "transient": True,
            "type": type_,
            "payload": payload,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.manager._fanout(self.job_id, ev)


class JobManager:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[Event | None]]] = {}
        self._shutting_down = False

    def submit(self, job_id: str, project_id: str, body: JobBody) -> None:
        """Run `body` for the job. The same job id may be submitted again after a restart:
        the event sequence continues where the persisted events stopped (#7)."""
        ctx = JobContext(self, job_id, project_id)
        task = asyncio.create_task(self._run(ctx, body), name=f"job-{job_id}")
        self._tasks[job_id] = task

    async def _run(self, ctx: JobContext, body: JobBody) -> None:
        job_id = ctx.job_id
        async with session_factory()() as session, session.begin():
            ctx._seq = await crud.last_event_seq(session, job_id)
            await crud.update_job(session, job_id, status="running")
        try:
            await body(ctx)
            async with session_factory()() as session, session.begin():
                await crud.update_job(session, job_id, status="done")
            logger.info("job.done", extra={"job_id": job_id})
        except asyncio.CancelledError:
            # a graceful stop (deploy, reload) leaves the job `interrupted`: the next process
            # resumes it with the same id. Any other cancellation is a failure.
            async with session_factory()() as session, session.begin():
                if self._shutting_down:
                    await crud.update_job(session, job_id, status="interrupted")
                    logger.info("job.interrupted", extra={"job_id": job_id})
                else:
                    await crud.update_job(session, job_id, status="failed", error="cancelled")
                    await crud.settle_project_status(session, ctx.project_id)
            raise
        except Exception as exc:
            logger.exception("job.failed", extra={"job_id": job_id})
            async with session_factory()() as session, session.begin():
                await crud.update_job(session, job_id, status="failed", error=str(exc)[:2000])
                await crud.settle_project_status(session, ctx.project_id)
            await ctx.emit("error", message=str(exc)[:2000])
        finally:
            self._fanout(job_id, None)
            self._tasks.pop(job_id, None)

    def _fanout(self, job_id: str, ev: Event | None) -> None:
        for q in self._subscribers.get(job_id, []):
            q.put_nowait(ev)
        if ev is None:
            self._subscribers.pop(job_id, None)

    def is_running(self, job_id: str) -> bool:
        return job_id in self._tasks

    async def stream(self, job_id: str, after_seq: int = 0) -> AsyncIterator[Event]:
        """Replay persisted events, then follow live ones until the job ends."""
        q: asyncio.Queue[Event | None] = asyncio.Queue()
        running = self.is_running(job_id)
        if running:
            self._subscribers.setdefault(job_id, []).append(q)
        async with session_factory()() as session:
            past = await crud.list_job_events(session, job_id, after_seq)
        last = after_seq
        for row in past:
            last = row.seq
            yield {
                "seq": row.seq,
                "type": row.type,
                "payload": json.loads(row.payload_json),
                "created_at": row.created_at.isoformat(),
            }
        if not running:
            return
        while True:
            ev = await q.get()
            if ev is None:
                return
            if ev.get("transient"):
                yield ev
                continue
            if ev["seq"] <= last:
                continue
            last = ev["seq"]
            yield ev

    async def shutdown(self) -> None:
        """Stop every job now (a deploy cannot wait 40 min for a build): they are marked
        `interrupted` and resumed by the next process, see `pipeline.resume_interrupted_jobs`."""
        self._shutting_down = True
        for t in list(self._tasks.values()):
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


job_manager = JobManager()
