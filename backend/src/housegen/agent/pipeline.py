"""Orchestration of the agentic workflow.

intake:    read the plan sheets (no photographs) → summary, sheet map, questions for the owner
generate:  builder(plans [+ photos] [+ brief], self-assessing via renders) → [critic ⇄ builder]* → version
modify:    builder(request) → verifier → [builder]? → version
resume:    after a server restart, continue an interrupted job from its files (#7)

generate and modify are written as stages (build → critic rounds, apply → verify) so that
`resume` can enter them at the stage the persisted events show was in progress.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import unicodedata
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from housegen.agent import critic
from housegen.agent import intake as intake_agent
from housegen.agent.builder import BuilderRun, run_builder
from housegen.agent.metrics import Role, RunMetrics, TurnMetric
from housegen.agent.progress import LiveProgress
from housegen.agent.prompts import (
    BUILDER_SYSTEM,
    CHAT_BUSY_NOTE,
    CHAT_INTERIOR_SYSTEM,
    CHAT_SYSTEM,
    FIRST_RUN_ADDENDUM,
    INTERIOR_SYSTEM,
    MODIFY_ADDENDUM,
    MODIFY_INTERIOR_SYSTEM,
    PLAN_ONLY_ADDENDUM,
    RESUME_ADDENDUM,
)
from housegen.agent.run_settings import ResolvedRunSettings, RunSettings, resolve
from housegen.agent.schemas import Critique, Intake, Verification
from housegen.agent.tools import (
    CHECK_PLAN_SPEC,
    MEASURE_SPEC,
    TOOL_SPECS,
    BuilderTools,
    ImageSources,
    chat_specs,
)
from housegen.agent.workspace import Workspace
from housegen.core.config import Settings, get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import JobContext, JobManager
from housegen.llm import Completion, ImagePart, Message, Usage, get_provider
from housegen.projects import crud
from housegen.projects.models import Job
from housegen.projects.schemas import questions_of, standard_views
from housegen.projects.storage import PlanSheet, ProjectStorage
from housegen.render import renderer
from housegen.render.kits import kit_dir, kit_with, pinned_kit

logger = logging.getLogger(__name__)

BRIEF_HEADING = "## About this house, from the owner"
# a job interrupted this many times is given up on: a crash loop must not burn money forever
RESUME_MAX_ATTEMPTS = 3
# the renders shown to a resumed builder (the whole standard set would be nine images)
RESUME_VIEWS = ("aerial", "north", "south", "east", "west")


@dataclass
class _Inputs:
    """What the builder and the critic are given: the project's files as stored."""

    photos: dict[str, Path]
    extras: list[Path]
    pages: list[Path]
    brief: str
    intake: Intake | None
    # photos attached to this job's request (#8): ground truth for the request, not extras
    attachments: list[Path] = field(default_factory=list)
    # the sheets with their documents (#10); `pages` is their PNGs in the same order
    sheets: list[PlanSheet] = field(default_factory=list)
    documents: dict[int, str] = field(default_factory=dict)  # document number → label

    @property
    def has_photos(self) -> bool:
        return bool(self.photos or self.extras)

    @property
    def elevations(self) -> dict[str, int]:
        # (a sheet number the intake got wrong must not fail the run after version 1 is saved)
        return {
            side: page
            for side, page in (self.intake.elevation_pages() if self.intake else {}).items()
            if 1 <= page <= len(self.pages)
        }

    @property
    def reference(self) -> str | None:
        """What the critic compares the renders with, or None when there is nothing."""
        if self.has_photos:
            return "photos"
        return "elevation drawings" if self.elevations else None


class _Run:
    """Shared plumbing for one job."""

    def __init__(
        self, ctx: JobContext, rs: ResolvedRunSettings | None = None, kit: str | None = None
    ) -> None:
        self.ctx = ctx
        self.settings = get_settings()
        # what this job runs with (#18): the snapshot taken when it was started, so a settings
        # change never affects a running job; .env defaults when there is none
        self.rs = rs or resolve(self.settings)
        self.provider = get_provider(self.rs.provider)
        self.storage = ProjectStorage(ctx.project_id)
        self.storage.ensure()
        self.storage.init_scene_from_template()
        # the renderer snapshot of the build path (frozen): what the builder reads as kit/*.js
        # and what every render of this job is drawn with
        self.kit = kit or pinned_kit(self.settings)
        self.workspace = Workspace(
            self.storage.scene_dir, readonly={"kit": kit_dir(self.kit, self.settings)}
        )
        self.scene_url = f"{self.settings.render_base_url}{self.storage.scene_url()}?kit={self.kit}"
        self.renders_dir = self.storage.scene_dir / "renders"
        self.last_run: BuilderRun | None = (
            None  # the most recent builder pass (suggestions, questions)
        )
        self.last_render_errors: list[str] = []  # from the most recent version render
        self.last_report: dict[str, Any] | None = None  # the scene report of that render
        # the builder's system prompt for this job (a house with interiors: the interior kit too)
        self.system = BUILDER_SYSTEM
        # a house with interiors (#45): its rooms as the scene reports them, room-N order
        self.interior = False
        self.rooms: list[dict[str, Any]] = []
        # the version the working copy was at the start, unedited: what "before" can be drawn from
        self.base_version: int | None = None
        self.usage = Usage()  # everything, builder + critic
        self.critic_usage = Usage()  # the critic's share, reported separately in the usage event
        self.metrics = RunMetrics(self.settings)  # time and tokens per turn (#13)
        self.views = standard_views(True)  # what every saved version is rendered from
        self.tools = BuilderTools(
            self.workspace, renderer, self.scene_url, self.renders_dir, on_render=self._on_render
        )

    @classmethod
    async def create(cls, ctx: JobContext, kit: str | None = None) -> _Run:
        async with session_factory()() as session:
            job = await crud.get_job(session, ctx.job_id)
            stored = job.settings
        rs = ResolvedRunSettings.model_validate(stored) if stored else None
        run = cls(ctx, rs, kit)
        run.tools.default_quality = run.rs.render_quality
        return run

    def set_image_sources(
        self, photos: dict[str, Path], extras: list[Path], attached: list[Path] | None = None
    ) -> None:
        self.tools.images = ImageSources(
            photos, extras, attached=attached, plan_sheets=self.storage.plan_sheets()
        )

    async def _on_render(self, images: dict[str, Path], errors: list[str]) -> None:
        urls = {
            v: f"/scenes/{self.ctx.project_id}/scene/renders/{p.name}?t={int(p.stat().st_mtime)}"
            for v, p in images.items()
        }
        await self.ctx.emit("render", renders=urls, errors=errors[:5])

    async def on_step(self, ev: dict[str, Any]) -> None:
        kind = ev.get("kind")
        if kind == "text":
            await self.ctx.emit("builder_text", text=ev["text"], step=ev.get("step"))
        elif kind == "turn":
            payload = {k: v for k, v in ev.items() if k != "kind"}
            self.metrics.add(TurnMetric.model_validate(payload))
            await self.ctx.emit("turn", **payload)
        else:
            await self.ctx.emit("builder_step", **{k: v for k, v in ev.items() if k != "kind"})

    async def add_call(self, role: Role, completion: Completion, step: int = 1) -> None:
        """Book a critic or intake call: usage, and a persisted `turn` record like the builder's."""
        self.usage = self.usage + completion.usage
        if role == "critic":
            self.critic_usage = self.critic_usage + completion.usage
        turn = self.metrics.add(
            TurnMetric(
                step=step,
                role=role,
                model=completion.model or self.rs.critic_model,
                duration_ms=completion.duration_ms,
                thinking_ms=completion.thinking_ms,
                input_tokens=completion.usage.input_tokens,
                cached_tokens=completion.usage.cache_read_tokens,
                cache_write_tokens=completion.usage.cache_write_tokens,
                output_tokens=completion.usage.output_tokens,
                reasoning_tokens=completion.usage.reasoning_tokens,
            )
        )
        logger.info(
            "llm.turn",
            extra={
                "role": role,
                "step": step,
                "duration_ms": turn.duration_ms,
                "in": turn.input_tokens,
                "cached": turn.cached_tokens,
                "out": turn.output_tokens,
            },
        )
        await self.ctx.emit("turn", **turn.event_payload())

    async def photos(self, exclude: set[str] | None = None) -> tuple[dict[str, Path], list[Path]]:
        """(façade photos by side, other photos in upload order), minus `exclude` file names
        (this job's own attachments, which are not extras for it)."""
        exclude = exclude or set()
        async with session_factory()() as session:
            project = await crud.get_project(session, self.ctx.project_id)
            facades = {
                p.side: self.storage.photos_dir / p.filename
                for p in project.photos
                if p.side != "other"
            }
            extras = [
                self.storage.photos_dir / p.filename
                for p in project.photos
                if p.side == "other" and p.filename not in exclude
            ]
            return facades, extras

    async def context(self) -> tuple[str, Intake | None]:
        """(the owner's brief, the intake's reading of the plans) as stored on the project."""
        async with session_factory()() as session:
            project = await crud.get_project(session, self.ctx.project_id)
            brief = (project.brief or "").strip()
            intake = (
                Intake.model_validate(json.loads(project.intake_json))
                if project.intake_json
                else None
            )
        return brief, intake

    async def inputs(self) -> _Inputs:
        """Load the project's files and point the tools and the version views at them."""
        async with session_factory()() as session:
            job = await crud.get_job(session, self.ctx.job_id)
            names = job.attachments
            project = await crud.get_project(session, self.ctx.project_id)
            documents = {d.number: d.label for d in project.plans}
        attached = [
            self.storage.photos_dir / n for n in names if (self.storage.photos_dir / n).exists()
        ]
        photos, extras = await self.photos(exclude=set(names))
        brief, intake = await self.context()
        sheets = self.storage.plan_sheets()
        inp = _Inputs(
            photos, extras, [sh.png for sh in sheets], brief, intake, attached, sheets, documents
        )
        self.views = standard_views(inp.has_photos)
        self.set_image_sources(photos, extras, attached)
        return inp

    async def render_standard(self) -> dict[str, Path]:
        """The views every saved version is rendered from, at quality=high; when that produces
        nothing (the scene did not become ready in time, a crash), once more at medium so the
        version still has pictures. The errors stay in `last_render_errors` for the builder."""
        res = await renderer.render(self.scene_url, self.views, self.renders_dir, quality="high")
        if not res.images:
            logger.warning(
                "render.version_failed",
                extra={"project_id": self.ctx.project_id, "errors": res.errors[:3]},
            )
            await self.ctx.emit(
                "phase", name="render", message="The full-quality render failed, retrying at medium"
            )
            res = await renderer.render(
                self.scene_url, self.views, self.renders_dir, quality="medium"
            )
        self.last_render_errors = res.errors
        self.last_report = res.report
        await self._on_render(res.images, res.errors)
        return res.images

    async def render_views(self, views: list[str]) -> dict[str, Path]:
        """Some views of the working copy at the version pictures' quality, shown in the timeline."""
        if not views:
            return {}
        res = await renderer.render(self.scene_url, views, self.renders_dir, quality="high")
        await self._on_render(res.images, res.errors)
        return res.images

    async def scene_report(self) -> dict[str, Any] | None:
        """What the runtime reports about the working copy (its rooms, its storeys' plan views),
        from a picture-less load."""
        res = await renderer.render(self.scene_url, [], self.renders_dir, quality="low")
        return res.report

    def unchanged_since(self, version: int) -> bool:
        """The working copy is that saved version, file for file (no job left edits since)."""
        vdir = self.storage.versions_dir / str(version)
        if version <= 0 or not vdir.exists():
            return False
        page, saved = self.storage.scene_dir / "index.html", vdir / "index.html"
        return (
            page.exists()
            and saved.exists()
            and page.read_bytes() == saved.read_bytes()
            and self.storage.scene_files() == self.storage.scene_files(version)
        )

    async def before_renders(self, version: int) -> dict[str, Path]:
        """The pictures of the scene as a modification starts: the current version's own when the
        working copy is that version and they were drawn with this job's renderer (#35: a render
        of the whole set took up to 98 s), the views it lacks rendered; else a render."""
        vdir = self.storage.versions_dir / str(version) / "renders"
        saved = {v: vdir / f"{v}.jpg" for v in self.views if (vdir / f"{v}.jpg").exists()}
        if (
            saved
            and self.storage.version_kit(version) == self.kit
            and self.unchanged_since(version)
        ):
            self.renders_dir.mkdir(parents=True, exist_ok=True)
            images = {v: Path(shutil.copy2(p, self.renders_dir / p.name)) for v, p in saved.items()}
            await self.ctx.emit(
                "phase", name="render", message=f"The current state: version {version}'s pictures"
            )
            await self._on_render(images, [])
            missing = [v for v in self.views if v not in images]
            return images | await self.render_views(missing)
        await self.ctx.emit("phase", name="render", message="Rendering the current state")
        return await self.render_standard()

    async def build_and_render(
        self, messages: list[Message], system: str | None = None
    ) -> tuple[str, dict[str, Path]]:
        """A builder pass followed by the version render. When the render produces no image at
        any quality, the builder hears why and gets one more pass before the version is saved
        (an empty version would otherwise go to the critic as a 0/100 placeholder)."""
        system = system or self.system
        summary = await self.build(messages, system)
        renders = await self.render_standard()
        if not renders:
            await self.ctx.emit(
                "phase",
                name="builder",
                message="The final render failed: asking the builder to fix it",
            )
            messages.append(Message.user(_render_failure_text(self.last_render_errors)))
            summary = await self.build(messages, system)
            renders = await self.render_standard()
        return summary, renders

    async def snapshot(
        self, kind: str, label: str, summary: str, score: int | None, renders: dict[str, Path]
    ) -> int:
        n = self.storage.next_version_number()
        dst = self.storage.snapshot(n, kit=self.kit)
        for view, p in renders.items():
            shutil.copy2(p, dst / "renders" / f"{view}.jpg")
        if renders and self.last_report is not None:
            # the rooms and storeys the runtime reported for these pictures, kept with the version
            # so the next job knows them without loading the scene (a heavy interior: 15-30 s)
            _write_report(dst, self.last_report)
        last = self.last_run
        async with session_factory()() as session, session.begin():
            await crud.add_version(
                session,
                self.ctx.project_id,
                n,
                kind,
                label,
                summary,
                score,
                suggestions=last.suggestions if last else None,
                questions=last.question_details if last else None,
            )
            await crud.update_job(session, self.ctx.job_id, result_version=n)
        await self.ctx.emit(
            "version",
            number=n,
            kind=kind,
            label=label,
            summary=summary,
            critic_score=score,
            scene_url=self.storage.scene_url(n),
            render_urls=self.storage.render_urls(n),
        )
        return n

    async def build(
        self,
        messages: list[Message],
        system: str | None = None,
        max_steps: int | None = None,
        *,
        effort: str | None = None,
        cache_key: str | None = None,
        end_tools: frozenset[str] = frozenset(),
        reply_ok: Callable[[], bool] | None = None,
    ) -> str:
        """A builder pass; the chat turn (#35) runs the same loop with its own effort, cache key
        (the project's: one conversation), ending tools and plain answers."""
        run = await run_builder(
            self.provider,
            self.rs.model,
            system or self.system,
            messages,
            self.tools,
            max_steps=max_steps or self.rs.max_steps,
            max_tokens=self.rs.max_tokens,
            effort=effort or self.rs.builder_effort,
            cache_key=cache_key or self.ctx.job_id,
            prune_above_tokens=self.settings.BUILDER_PRUNE_ABOVE_TOKENS,
            on_step=self.on_step,
            progress=lambda step: LiveProgress(self.ctx, "builder", step=step, show_text=True),
            end_tools=end_tools,
            reply_ok=reply_ok,
        )
        self.usage = self.usage + run.usage
        self.last_run = run
        await self.ctx.emit(
            "builder_done",
            summary=run.summary,
            steps=run.steps,
            finished=run.finished,
            suggestions=run.suggestions,
            questions=run.questions,
        )
        return run.summary

    async def set_version_critique(
        self, version: int, verdict: Critique, scored: bool = True
    ) -> None:
        """Store a review on a version, with its score unless `scored` is false (a verifier that
        could not see the change)."""
        async with session_factory()() as session, session.begin():
            await crud.set_version_critique(
                session,
                self.ctx.project_id,
                version,
                verdict.overall_score if scored else None,
                verdict.model_dump(),
            )

    async def prepare_interior(self, inp: _Inputs, version: int | None = None) -> None:
        """A house with interiors (#45): the interior tools and audit, its rooms, and the views of
        its storeys and rooms in the version pictures, as the interior job saves them. The rooms
        come from the scene report kept with `version` when the working copy is that version,
        else from a load of the scene (then kept with the version for the next job)."""
        _interior_tools(self, inp)
        same = version is not None and self.unchanged_since(version)
        vdir = self.storage.versions_dir / str(version or 0)
        report = _read_report(vdir) if same else None
        if report is None:
            report = await self.scene_report()
            if same and report is not None:
                _write_report(vdir, report)
        self.rooms = list((report or {}).get("rooms") or [])
        self.views = list(self.views) + _interior_views(report)

    def finish_extras(self) -> dict[str, Any]:
        last = self.last_run
        return {
            "suggestions": last.suggestions if last else [],
            "questions": last.questions if last else [],
        }

    async def emit_usage(self) -> None:
        """The run's totals: the usage event (with the summary) and the summary on the job."""
        summary = self.metrics.summary()
        fields = {
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "cache_read_tokens": self.usage.cache_read_tokens,
            "critic_input_tokens": self.critic_usage.input_tokens,
            "critic_output_tokens": self.critic_usage.output_tokens,
        }
        logger.info("run.usage", extra=fields)
        logger.info(
            "run.summary",
            extra={
                "wall_ms": summary.wall_ms,
                "turns": summary.turns,
                "builder_llm_ms": summary.builder.llm_ms,
                "builder_tools_ms": summary.builder.tools_ms,
                "render_ms": summary.builder.render_ms,
                "critic_llm_ms": summary.critic.llm_ms,
                "cache_miss_turns": summary.builder.cache_miss_turns,
                "single_edit_turns": summary.single_edit_turns,
                "cost_usd": summary.cost_usd,
            },
        )
        async with session_factory()() as session, session.begin():
            await crud.update_job(session, self.ctx.job_id, metrics=summary.model_dump())
        await self.ctx.emit("usage", **fields, metrics=summary.model_dump())

    def current_renders(self, views: tuple[str, ...] = RESUME_VIEWS) -> list[ImagePart]:
        """The latest renders of the working copy, for a builder that has no conversation."""
        out: list[ImagePart] = []
        for view in views:
            p = self.renders_dir / f"{view}.jpg"
            if p.exists():
                out.append(ImagePart.from_file(p, label=f"Current render — {view}"))
        return out


# --------------------------------------------------------------------------
# intake
# --------------------------------------------------------------------------


async def intake(ctx: JobContext) -> None:
    """Read the plan set before the first build of a project without photographs."""
    run = await _Run.create(ctx)
    pid = ctx.project_id
    inp = await run.inputs()
    pages = inp.pages
    brief = inp.brief

    await ctx.emit("phase", name="intake", message="Reading the plan sheets")
    async with LiveProgress(ctx, "intake", show_text=False) as live:
        result, completion = await intake_agent.read_plans(
            run.provider,
            run.rs.critic_model,
            pages,
            brief,
            run.rs.max_tokens,
            on_progress=live.on_event,
            effort=run.rs.critic_effort,
            labels=[
                sheet_label(i, len(pages), inp.sheets, inp.documents)
                for i in range(1, len(pages) + 1)
            ],
            preamble=plan_documents_text(inp.documents),
        )
    await run.add_call("intake", completion)
    await ctx.emit(
        "intake",
        summary=result.summary,
        sheets=[sh.model_dump() for sh in result.sheets],
        questions=[q.model_dump() for q in result.questions],
    )
    n = len(result.questions)
    text = result.summary.strip() + (
        f"\n\nBefore I build, {n} question{'s' if n > 1 else ''} the drawings cannot answer, "
        "each with my best guess filled in: correct what is wrong, then start the build."
        if n
        else "\n\nThe drawings answer everything I need: start the build when you are ready."
    )
    async with session_factory()() as session, session.begin():
        await crud.set_intake(session, pid, {"job_id": ctx.job_id, **result.model_dump()})
        await crud.add_chat_message(session, pid, "assistant", text, ctx.job_id, None)
    await run.emit_usage()
    await ctx.emit(
        "done",
        version=None,
        score=None,
        summary=text,
        suggestions=[],
        questions=[q.question for q in result.questions],
    )


# --------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------


async def generate(ctx: JobContext) -> None:
    run = await _Run.create(ctx)
    pid = ctx.project_id
    async with session_factory()() as session, session.begin():
        await crud.set_status(session, pid, "generating")
    inp = await run.inputs()

    # 1. builder: reads the plans (and photos) itself, renders, self-corrects
    run.storage.init_scene_from_template(force=True)
    await ctx.emit(
        "phase",
        name="builder",
        message=(
            "Reading the plans and photos, building the scene"
            if inp.has_photos
            else "Reading the plans, building the scene"
        ),
    )
    messages = [Message.user(*_first_message(inp))]
    summary, renders = await run.build_and_render(messages)
    version = await run.snapshot("generation", "Initial build", summary, None, renders)

    # 2. independent critic (optional, CRITIC_MAX_ITERATIONS=0 disables)
    await _critic_rounds(run, inp, messages, renders, version, summary)


async def _critic_rounds(
    run: _Run,
    inp: _Inputs,
    messages: list[Message],
    renders: dict[str, Path],
    version: int,
    summary: str,
    start: int = 1,
    judged: Critique | None = None,
) -> None:
    """Critic rounds `start`… against the photos, or against the elevation drawings the intake
    identified when there are none; then the run's closing bookkeeping.

    `judged` (resume): round `start` was already judged by the interrupted process and its
    verdict persisted; the builder's fix pass is what was lost.
    """
    ctx, s, rs, pid = run.ctx, run.settings, run.rs, run.ctx.project_id
    score: int | None = judged.overall_score if judged else None
    reference = inp.reference
    if reference is None and rs.critic_rounds > 0 and start == 1:
        logger.info("critic.skipped", extra={"project_id": pid, "reason": "no reference"})
        await ctx.emit(
            "phase",
            name="critic",
            message="Independent review skipped: no photographs and no elevation sheet to compare with",
        )
    rounds = rs.critic_rounds if reference else 0
    for i in range(start, rounds + 1):
        if not renders:
            # nothing to compare: a review of photos alone is a placeholder score, not a judgment
            logger.warning("critic.skipped", extra={"project_id": pid, "reason": "no renders"})
            await ctx.emit(
                "phase",
                name="critic",
                message="Independent review skipped: the scene produced no render",
            )
            break
        if judged is not None and i == start:
            verdict = judged
        else:
            await ctx.emit(
                "phase",
                name="critic",
                message=f"Independent review against the {reference} (round {i})",
            )
            async with LiveProgress(ctx, "critic", show_text=False) as live:
                if inp.has_photos:
                    verdict, completion = await critic.critique_against_photos(
                        run.provider,
                        rs.critic_model,
                        inp.photos,
                        renders,
                        s.CRITIC_SCORE_THRESHOLD,
                        rs.max_tokens,
                        extras=inp.extras,
                        on_progress=live.on_event,
                        effort=rs.critic_effort,
                    )
                else:
                    verdict, completion = await critic.critique_against_plans(
                        run.provider,
                        rs.critic_model,
                        inp.elevations,
                        inp.pages,
                        renders,
                        s.CRITIC_SCORE_THRESHOLD,
                        rs.max_tokens,
                        on_progress=live.on_event,
                        effort=rs.critic_effort,
                    )
            await run.add_call("critic", completion, step=i)
            score = verdict.overall_score
            await _emit_critic(ctx, i, verdict)
            await run.set_version_critique(version, verdict)
        if not _first_fix(i, verdict, s, rs.critic_rounds) and _verdict_ends_rounds(
            verdict, i, s, rs.critic_rounds
        ):
            break
        await ctx.emit("phase", name="builder", message=f"Fixing the critic's findings (round {i})")
        messages.append(Message.user(_critic_feedback_text(reference or "photos", verdict)))
        summary, renders = await run.build_and_render(messages)
        version = await run.snapshot("critique", f"After critic round {i}", summary, None, renders)

    async with session_factory()() as session, session.begin():
        await crud.set_status(session, pid, "ready")
        await crud.add_chat_message(session, pid, "assistant", summary, ctx.job_id, version)
    await run.emit_usage()
    await ctx.emit("done", version=version, score=score, summary=summary, **run.finish_extras())


def _first_fix(iteration: int, verdict: Critique, s: Settings, rounds: int) -> bool:
    """The first review of a build is always followed by a fix pass when it listed findings and
    the run has a round left for it (CRITIC_FIRST_FIX): the score near the threshold is noise,
    the findings are not."""
    return s.CRITIC_FIRST_FIX and iteration == 1 and rounds >= 2 and bool(verdict.issues)


def _verdict_ends_rounds(verdict: Critique, iteration: int, s: Settings, rounds: int) -> bool:
    no_major = not any(x.severity == "major" for x in verdict.issues)
    if verdict.done or (verdict.overall_score >= s.CRITIC_SCORE_THRESHOLD and no_major):
        return True
    return iteration >= rounds


def _render_failure_text(errors: list[str]) -> str:
    detail = "\n".join(errors[:5]) or "no image was produced"
    return (
        "The final render of your scene failed, so no version could be saved:\n"
        f"{detail}\n"
        "Every saved version is rendered headless at quality=high, with shadows and effects, "
        "and must be ready within a few minutes on a machine without a GPU. Reproduce it with "
        "render_views at quality 'high', fix the cause (an error in buildScene, or a scene too "
        "heavy to draw: fewer or smaller trees and bushes, less instanced geometry), check with "
        "render_views again, then call finish."
    )


def _critic_feedback_text(reference: str, verdict: Critique) -> str:
    return (
        f"An independent critic compared your renders with the {reference}. Address the "
        "points below, most impactful first, verify with renders, run check_scene, "
        "then finish.\n\n" + verdict.as_builder_feedback()
    )


def _first_message(
    inp: _Inputs, resume_renders: list[ImagePart] | None = None
) -> list[ImagePart | str]:
    """The builder's first turn: the addenda, the plan sheets, the photographs (or the intake's
    reading of the plans when there are none) and the owner's brief.

    `resume_renders` (a restart, #7): the current renders replace the "placeholder scene" line.
    """
    photos, extras, pages = inp.photos, inp.extras, inp.pages
    parts: list[ImagePart | str] = [FIRST_RUN_ADDENDUM]
    if resume_renders is not None:
        parts.append(RESUME_ADDENDUM)
    if inp.has_photos:
        parts.append(
            "Build this house. Below are the plan sheets and the photographs. Photographs labelled "
            "with a side show that façade; the others show details, other angles or the surroundings."
        )
    else:
        parts.append(PLAN_ONLY_ADDENDUM)
        parts.append("Build this house. Below are the plan sheets.")
    parts.extend(plan_documents_text(inp.documents))
    for i, p in enumerate(pages, 1):
        parts.append(
            ImagePart.from_file(p, label=sheet_label(i, len(pages), inp.sheets, inp.documents))
        )
    for side, p in photos.items():
        parts.append(ImagePart.from_file(p, label=f"Photograph of the {side} façade"))
    for i, p in enumerate(extras, 1):
        parts.append(ImagePart.from_file(p, label=f"Additional photograph {i} of {len(extras)}"))
    if inp.intake is not None:
        parts.append(inp.intake.as_builder_text())
    if inp.brief:
        parts.append(f"{BRIEF_HEADING}\n{inp.brief}")
    if resume_renders is None:
        parts.append(
            "The workspace holds a placeholder scene from a template; replace it entirely. "
            "Start whenever you are ready."
        )
    else:
        parts.append("## The scene as the interrupted session left it")
        parts.extend(resume_renders)
        parts.append("Continue from here.")
    return parts


def sheet_label(index: int, total: int, sheets: list[PlanSheet], documents: dict[int, str]) -> str:
    """ "Plan sheet 3 of 6" plus, with several documents, which document and page it is."""
    base = f"Plan sheet {index} of {total}"
    if len(documents) <= 1 or index > len(sheets):
        return base
    sh = sheets[index - 1]
    label = documents.get(sh.document, "")
    return f"{base} (document {sh.document}{f' “{label}”' if label else ''}, page {sh.page})"


def plan_documents_text(documents: dict[int, str]) -> list[str]:
    """The list of plan documents and the rule for disagreements, when there are several."""
    if len(documents) <= 1:
        return []
    lines = [f"- document {n}: {label or f'plans {n}'}" for n, label in sorted(documents.items())]
    return [
        "## Plan documents\nThe plan set comes from several documents, in the order they were "
        "added:\n" + "\n".join(lines) + "\nWhere they disagree, the most recent one (the last) "
        "describes the house as it is today, unless the owner says otherwise below."
    ]


def _elevation_sheets(intake: Intake | None, pages: list[Path], limit: int = 4) -> list[ImagePart]:
    """The elevation sheets the intake identified, each once, as reference images."""
    if intake is None:
        return []
    by_page: dict[int, list[str]] = {}
    for side, page in intake.elevation_pages().items():
        if 1 <= page <= len(pages):
            by_page.setdefault(page, []).append(side)
    return [
        ImagePart.from_file(
            pages[page - 1],
            label=f"Plan sheet {page}: elevation drawing of the {', '.join(sides)} façade(s) (reference)",
        )
        for page, sides in sorted(by_page.items())[:limit]
    ]


async def _emit_critic(ctx: JobContext, iteration: int, verdict: Critique) -> None:
    seen = not (isinstance(verdict, Verification) and not verdict.visible)
    await ctx.emit(
        "critic",
        iteration=iteration,
        score=verdict.overall_score if seen else None,
        done=verdict.done,
        summary=verdict.summary,
        issues=[i.model_dump() for i in verdict.issues],
        **({} if seen else {"visible": False}),
    )


# --------------------------------------------------------------------------
# modify
# --------------------------------------------------------------------------


async def modify(ctx: JobContext) -> None:
    run = await _modify_run(ctx)
    async with session_factory()() as session:
        job = await crud.get_job(session, ctx.job_id)
        request = job.request_text
        history = await crud.list_chat(session, ctx.project_id)
        base = (await crud.get_project(session, ctx.project_id)).current_version
    inp = await run.inputs()
    if run.interior:
        await run.prepare_interior(inp, base)
    run.base_version = base if run.unchanged_since(base) else None

    before = await run.before_renders(base)
    # a house with interiors: the rooms the request names, seen before the change (#45)
    before |= await run.render_views(room_views(touched_rooms(request, run.rooms)))
    before_copy = _keep_before_renders(run, before)

    thread = [m for m in history if m.job_id != ctx.job_id]
    parts = _modify_message(request, thread, inp, before, rooms=run.rooms)
    messages = [Message.user(*parts)]
    await _modify_apply(run, inp, request, messages, before_copy)


async def _modify_run(ctx: JobContext) -> _Run:
    """A modification's run: a house with interiors draws with a renderer that has the interior
    modules and its builder knows the interior kit (#45); an exterior keeps the pinned one."""
    interior = has_interior(ProjectStorage(ctx.project_id).scene_dir)
    run = await _Run.create(ctx, kit=kit_with("interior.js") if interior else None)
    if interior:
        run.interior = True
        run.system = MODIFY_INTERIOR_SYSTEM
    return run


def _keep_before_renders(run: _Run, before: dict[str, Path]) -> dict[str, Path]:
    """Copy the "before" renders aside: the working copy's renders are overwritten by the
    builder's own render calls (and the copies survive a restart, see `resume`)."""
    keep = run.storage.scene_dir / "renders_before"
    shutil.rmtree(keep, ignore_errors=True)
    keep.mkdir(exist_ok=True)
    return {v: Path(shutil.copy2(p, keep / p.name)) for v, p in before.items()}


THREAD_MESSAGES = 10  # the conversation the builder of a modification is given (#35)
THREAD_REPLY_CHARS = 700  # an answer longer than this is cut: the builder needs its gist


def thread_text(history: list[Any]) -> str | None:
    """The conversation so far, the owner's requests and what was done in reply, oldest first:
    "make it bigger" and "the other table" need it (#35)."""
    lines = []
    for m in history[-THREAD_MESSAGES:]:
        text = " ".join(str(m.content).split())
        if m.role == "user":
            lines.append(f"- Owner: {text}")
        else:
            if len(text) > THREAD_REPLY_CHARS:
                text = text[:THREAD_REPLY_CHARS].rsplit(" ", 1)[0] + " …"
            where = f" (version {m.version_number})" if m.version_number else ""
            lines.append(f"- You{where}: {text}")
    if not lines:
        return None
    return "## The conversation so far (already applied)\n" + "\n".join(lines)


def rooms_text(rooms: list[dict[str, Any]]) -> str | None:
    """A house with interiors: its rooms with the number of their room-N view."""
    if not rooms:
        return None
    lines = []
    for i, r in enumerate(rooms, 1):
        area = f", {r['area']:g} m²" if isinstance(r.get("area"), int | float) else ""
        floor = f", floor at y={r['y']:.2f}" if isinstance(r.get("y"), int | float) else ""
        lines.append(f"{i}. {r.get('name') or '?'} ({r.get('use') or 'room'}{area}{floor})")
    return '## The rooms (render_views(["room-N"]) shows room N at eye height)\n' + "\n".join(lines)


def _modify_message(
    request: str,
    history: list[Any],
    inp: _Inputs,
    renders: dict[str, Path],
    resume: bool = False,
    rooms: list[dict[str, Any]] | None = None,
) -> list[ImagePart | str]:
    parts: list[ImagePart | str] = [MODIFY_ADDENDUM]
    if resume:
        parts.append(RESUME_ADDENDUM)
    parts.append(f"## Request\n{request}")
    if inp.attachments:
        parts.append(
            "## Photographs attached to this request (ground truth for what is asked; "
            "zoom with inspect_image('attached-1', …))"
        )
        for i, p in enumerate(inp.attachments, 1):
            parts.append(ImagePart.from_file(p, label=f"Attached photograph {i}"))
    thread = thread_text(history)
    if thread:
        parts.append(thread)
    listed = rooms_text(rooms or [])
    if listed:
        parts.append(listed)
    if inp.brief:
        parts.append(f"{BRIEF_HEADING}\n{inp.brief}")
    for side, p in inp.photos.items():
        parts.append(ImagePart.from_file(p, label=f"Photograph of the {side} façade (reference)"))
    for i, p in enumerate(inp.extras[:8], 1):
        parts.append(ImagePart.from_file(p, label=f"Additional photograph {i} (reference)"))
    if not inp.has_photos:
        parts.extend(_elevation_sheets(inp.intake, inp.pages))
    names = {f"room-{i}": r.get("name") or "" for i, r in enumerate(rooms or [], 1)}
    for view, p in renders.items():
        if view in ("aerial", "south", "north"):
            parts.append(ImagePart.from_file(p, label=f"Current render — {view}"))
        elif view in names:
            parts.append(ImagePart.from_file(p, label=f"Current render — {view} ({names[view]})"))
    return parts


async def _modify_apply(
    run: _Run, inp: _Inputs, request: str, messages: list[Message], before: dict[str, Path]
) -> None:
    await run.ctx.emit("phase", name="builder", message="Applying the modification")
    summary, after = await run.build_and_render(messages)
    version = await run.snapshot("modification", request[:80], summary, None, after)
    await _modify_verify(run, inp, request, messages, before, after, version, summary)


async def _modify_verify(
    run: _Run,
    inp: _Inputs,
    request: str,
    messages: list[Message],
    before: dict[str, Path],
    after: dict[str, Path],
    version: int,
    summary: str,
) -> None:
    ctx, rs = run.ctx, run.rs
    if rs.critic_rounds > 0:
        await ctx.emit("phase", name="critic", message="Verifying the modification")
        before, after = dict(before), dict(after)
        rooms = await _room_pictures(run, before, after)
        async with LiveProgress(ctx, "critic", show_text=False) as live:
            verdict, completion = await critic.verify_modification(
                run.provider,
                rs.critic_model,
                request,
                before,
                after,
                rs.max_tokens,
                on_progress=live.on_event,
                effort=rs.critic_effort,
                attachments=inp.attachments,
                rooms=rooms,
            )
        await run.add_call("critic", completion)
        await _emit_critic(ctx, 1, verdict)
        await _modify_after_verdict(run, verdict, version, summary)
        return
    await _modify_finish(run, version, summary, None)


ROOM_VIEW = re.compile(r"^room-(\d+)$")
VERIFY_ROOMS = 4  # room views the verifier compares at most


async def _room_pictures(
    run: _Run, before: dict[str, Path], after: dict[str, Path]
) -> dict[str, str]:
    """A house with interiors (#45): the room views its modification is checked on, added to
    `before` and `after`; returns view → the room's name. The rooms the request names (seen
    before the build) and the rooms the builder looked at; AFTER from the working copy, BEFORE
    from the version the job started from when the start did not draw it."""
    if not run.rooms:
        return {}
    seen = sorted(v for v in run.tools.rendered_views if ROOM_VIEW.match(v))
    views = [v for v in before if ROOM_VIEW.match(v)] + [v for v in seen if v not in before]
    names = {
        v: str(run.rooms[int(m.group(1)) - 1].get("name") or v)
        for v in views[:VERIFY_ROOMS]
        if (m := ROOM_VIEW.match(v)) and 0 < int(m.group(1)) <= len(run.rooms)
    }
    if not names:
        return {}
    res = await renderer.render(run.scene_url, list(names), run.renders_dir, quality="high")
    after.update(res.images)
    missing = [v for v in names if v not in before]
    if missing and run.base_version:
        page = run.storage.scene_url(run.base_version)
        url = f"{run.settings.render_base_url}{page}?kit={run.kit}"
        res = await renderer.render(
            url, missing, run.storage.scene_dir / "renders_before", quality="high"
        )
        before.update(res.images)
    return {v: n for v, n in names.items() if v in after}


async def _modify_after_verdict(run: _Run, verdict: Critique, version: int, summary: str) -> None:
    """The verifier's findings are a proposition, not an order (2026-09-13): they are stored on
    the version like a critic review, where the owner reads them and applies them with one
    request ("Apply the review's findings") if they deserve a pass. Until then a fix pass ran
    unasked on findings nobody had read, a full builder pass, and its version was saved under
    the score of the version the verifier had inspected. A verifier whose pictures do not show
    the change gives no score (#35: beds judged from outside got 50/100)."""
    scored = not (isinstance(verdict, Verification) and not verdict.visible)
    await run.set_version_critique(version, verdict, scored=scored)
    await _modify_finish(run, version, summary, verdict.overall_score if scored else None)


async def _modify_finish(run: _Run, version: int, summary: str, score: int | None) -> None:
    shutil.rmtree(run.storage.scene_dir / "renders_before", ignore_errors=True)
    async with session_factory()() as session, session.begin():
        await crud.add_chat_message(
            session, run.ctx.project_id, "assistant", summary, run.ctx.job_id, version
        )
    await run.emit_usage()
    await run.ctx.emit("done", version=version, score=score, summary=summary, **run.finish_extras())


# --------------------------------------------------------------------------
# interior (#33): rooms, partitions, doors and furniture inside the existing exterior
# --------------------------------------------------------------------------

# the kit modules an interior scene imports, by import-map name
INTERIOR_IMPORTS = {
    "housekit/interior": "/kit/interior.js",
    "housekit/furnish": "/kit/furnish.js",
    "housekit/finishes": "/kit/finishes.js",
}
# eye-height views of the first rooms, in the version pictures
INTERIOR_ROOM_VIEWS = 6
_INTERIOR_IMPORT = re.compile(r"""from\s*["']housekit/(?:interior|furnish|finishes)["']""")


def has_interior(scene_dir: Path) -> bool:
    """The scene's modules import the interior kit: the interior job built rooms in it (#45)."""
    src = scene_dir / "src"
    return src.is_dir() and any(
        _INTERIOR_IMPORT.search(p.read_text(encoding="utf-8", errors="replace"))
        for p in src.rglob("*.js")
    )


def _write_report(version_dir: Path, report: dict[str, Any]) -> None:
    """Keep the runtime's scene report (rooms, plan sections) with a version: report.json."""
    kept = {k: report.get(k) for k in ("rooms", "planSections")}
    (version_dir / "report.json").write_text(json.dumps(kept), encoding="utf-8")


def _read_report(version_dir: Path) -> dict[str, Any] | None:
    p = version_dir / "report.json"
    try:
        return dict(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None
    except (OSError, ValueError):
        return None


def _interior_tools(run: _Run, inp: _Inputs) -> None:
    """The interior's tools: the plan check and measure, the scene audit after every render
    (#67, #70), the sheets' labels for the plan check's scale."""
    run.tools.specs = [*TOOL_SPECS, CHECK_PLAN_SPEC, MEASURE_SPEC]
    run.tools.show_audit = True
    run.tools.sheet_labels = {sh.page: sh.label for sh in (inp.intake.sheets if inp.intake else [])}


def _interior_views(report: dict[str, Any] | None) -> list[str]:
    """The version pictures of an interior beyond the exterior views: the plan of each storey and
    the first rooms, as a photographer frames them (photo-<n>: the room's viewpoint, 1.3 m, a
    24 mm lens, level, #43); the builder's own checks stay on the walk's room-<n> views."""
    report = report or {}
    rooms = report.get("rooms") or []
    return [str(s["view"]) for s in report.get("planSections") or []] + [
        f"photo-{i}" for i in range(1, min(len(rooms), INTERIOR_ROOM_VIEWS) + 1)
    ]


def _plain(text: str) -> str:
    """Lower case without accents: "Séjour" and "sejour" match."""
    norm = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in norm if not unicodedata.combining(c))


# words of a request (English and French) → the room uses they name: the room views a
# modification of a house with interiors is checked on (#45)
_USE_WORDS: dict[str, tuple[str, ...]] = {
    "bath": (
        "bathroom",
        "bath",
        "shower",
        "salle de bain",
        "salle d'eau",
        "sdb",
        "douche",
        "baignoire",
    ),
    "wc": ("wc", "toilet", "toilette", "toilettes"),
    "bedroom": ("bedroom", "bed", "beds", "chambre", "lit", "lits"),
    "kitchen": ("kitchen", "cuisine"),
    "living": ("living", "lounge", "salon", "sejour", "sofa", "canape"),
    "dining": ("dining", "salle a manger"),
    "hall": ("hall", "hallway", "entrance", "entree", "corridor", "couloir"),
    "office": ("office", "study", "bureau"),
    "laundry": ("laundry", "buanderie", "washer", "lave-linge"),
    "stair": ("stair", "stairs", "staircase", "escalier"),
    "cellar": ("cellar", "cave"),
    "garage": ("garage",),
    "storage": ("storage", "rangement", "debarras"),
}
_STOREY_WORDS = {
    "top": ("attic", "combles", "top floor", "upstairs", "dernier etage"),
    "ground": ("ground floor", "rez", "rdc", "rez-de-chaussee"),
    "first": ("first floor", "1st floor", "premier etage", "1er etage"),
    "basement": ("basement", "sous-sol"),
}
_SIDE_WORDS = {
    "north": ("north", "nord"),
    "south": ("south", "sud"),
    "east": ("east",),
    "west": ("west", "ouest"),
}


def _says(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?:e?s)?(?![a-z])", text) for w in words)


def touched_rooms(
    request: str, rooms: list[dict[str, Any]], limit: int = VERIFY_ROOMS
) -> list[int]:
    """The rooms (0-based, room-N order) a request is about: by name ("Chambre 2", "Salon") when
    it names any, else by use ("the bathroom", "every bed"), narrowed by the storey ("attic",
    "ground floor") and the side ("west") it names. At most `limit`."""
    text = _plain(request)
    named = [
        i
        for i, r in enumerate(rooms)
        if len(name := _plain(str(r.get("name") or "")).strip()) >= 3 and _says(text, (name,))
    ]
    if named:
        return named[:limit]
    uses = {u for u, words in _USE_WORDS.items() if _says(text, words)}
    found = [
        i for i, r in enumerate(rooms) if any(u in str(r.get("use") or "").split("-") for u in uses)
    ]
    levels = sorted({round(float(r.get("y") or 0), 1) for r in rooms})
    above = [y for y in levels if y > -0.5] or levels
    pick = {
        "top": levels[-1:] if levels else [],
        "ground": above[:1],
        "first": above[1:2],
        "basement": [y for y in levels if y <= -0.5][:1],
    }
    wanted = {y for k, words in _STOREY_WORDS.items() if _says(text, words) for y in pick[k]}
    if wanted:
        found = [i for i in found if round(float(rooms[i].get("y") or 0), 1) in wanted] or found
    sides = [k for k, words in _SIDE_WORDS.items() if _says(text, words)]
    if sides and len(found) > 1:

        def centre(i: int) -> tuple[float, float]:
            poly = rooms[i].get("polygon") or [[0, 0]]
            return (
                sum(float(p[0]) for p in poly) / len(poly),
                sum(float(p[1]) for p in poly) / len(poly),
            )

        # +x east, +z south: the room farthest toward the side named
        far = {
            "north": lambda i: -centre(i)[1],
            "south": lambda i: centre(i)[1],
            "east": lambda i: centre(i)[0],
            "west": lambda i: -centre(i)[0],
        }[sides[0]]
        found = [max(found, key=far)]
    return found[:limit]


def room_views(indices: list[int]) -> list[str]:
    return [f"room-{i + 1}" for i in indices]


def ensure_interior_imports(index_html: Path) -> bool:
    """Add the interior modules to the scene page's import map (a scene made before them has
    only housekit); True when the page changed."""
    html = index_html.read_text(encoding="utf-8")
    missing = {k: v for k, v in INTERIOR_IMPORTS.items() if f'"{k}"' not in html}
    if not missing:
        return False
    anchor = re.search(r'"housekit"\s*:\s*"[^"]+"', html)
    if anchor is None:
        return False
    extra = "".join(f',\n    "{k}": "{v}"' for k, v in missing.items())
    index_html.write_text(html[: anchor.end()] + extra + html[anchor.end() :], encoding="utf-8")
    return True


PLAN_GATE_STEPS = 20  # the plan check's repair round: steps at most (#70)
_PLAN_GAP = "room area off the plan:"


def plan_gaps(audit: list[str]) -> list[str]:
    """The audit's lines about rooms off the areas printed on the plan (kit/layout.js, #70)."""
    return [line for line in audit if line.startswith(_PLAN_GAP)]


def _plan_gate_text(gaps: list[str], audit: list[str]) -> str:
    others = [
        line
        for line in audit
        if not line.startswith(_PLAN_GAP)
        and (
            line.split(":")[0] in ("support", "window", "passage", "size")
            or line.startswith("no area from the plan")
        )
    ]
    text = (
        "Before this interior is saved, the plan check still finds rooms whose area in your model is "
        "more than 5 % and 0.5 m² off the area printed on the plan:\n"
        + "\n".join(f"- {g[len(_PLAN_GAP) :].strip()}" for g in gaps)
        + "\n\nRe-read those rooms on the plan sheet (inspect_image at full resolution, check_plan) "
        "and fix their outlines and the partitions around them, or the knee wall under the roof that "
        "the plan draws. If the model is right and the printed area is the plan's own (a different "
        "rule, a typo), leave it and say so in finish.summary."
    )
    if others:
        text += (
            "\n\nThe layout audit also still flags (fix those you can while you are at it; "
            "measure() gives the numbers):\n" + "\n".join(f"- {o}" for o in others[:10])
        )
    return text + "\n\nThen run check_scene and call finish."


async def interior(ctx: JobContext) -> None:
    run = await _Run.create(ctx, kit=kit_with("interior.js"))
    async with session_factory()() as session:
        job = await crud.get_job(session, ctx.job_id)
        request = job.request_text
    inp = await run.inputs()
    _interior_tools(run, inp)
    ensure_interior_imports(run.storage.scene_dir / "index.html")
    floor_plans = [
        sh for sh in (inp.intake.sheets if inp.intake else []) if sh.kind == "floor_plan"
    ]

    await ctx.emit("phase", name="render", message="Rendering the current state")
    before = await run.render_standard()
    parts: list[ImagePart | str] = []
    parts.append(
        "## Request\n"
        + (request.strip() or "Furnish the interior of the house as the floor plans draw it.")
    )
    if inp.brief:
        parts.append(f"{BRIEF_HEADING}\n{inp.brief}")
    pages = [sh.page for sh in floor_plans] or list(range(1, len(inp.pages) + 1))
    parts.append(
        "## Floor plans (zoom with inspect_image('plan-N', …); check_plan(sheet=N) lays your "
        "storey over one)"
    )
    for page in pages:
        if 1 <= page <= len(inp.pages):
            label = next((sh.label for sh in floor_plans if sh.page == page), "")
            parts.append(
                ImagePart.from_file(
                    inp.pages[page - 1], label=f"plan-{page}" + (f" — {label}" if label else "")
                )
            )
    for view in ("aerial", "south"):
        if view in before:
            parts.append(ImagePart.from_file(before[view], label=f"The exterior model — {view}"))
    messages = [Message.user(*parts)]

    await ctx.emit("phase", name="builder", message="Building the interior")
    summary = await run.build(messages, system=INTERIOR_SYSTEM)
    # the end-of-job gate (#70): rooms still off the areas printed on the plan get one bounded repair
    # round, with the list (and what the layout audit still flags), the way the critic's rounds work
    probe = await renderer.render(run.scene_url, [], run.renders_dir, quality="low")
    gaps = plan_gaps(probe.audit)
    if gaps:
        logger.info("interior.plan_gate", extra={"project_id": ctx.project_id, "rooms": len(gaps)})
        await ctx.emit(
            "phase",
            name="builder",
            message=f"Plan check: {len(gaps)} room(s) off the areas printed on the plan, one repair round",
        )
        messages.append(Message.user(_plan_gate_text(gaps, probe.audit)))
        summary = await run.build(
            messages, system=INTERIOR_SYSTEM, max_steps=min(run.rs.max_steps, PLAN_GATE_STEPS)
        )
        probe = await renderer.render(run.scene_url, [], run.renders_dir, quality="low")
    # the version pictures: the exterior views, the plan of each storey and the first rooms
    run.views = list(run.views) + _interior_views(probe.report)
    renders = await run.render_standard()
    label = ("Interior: " + request.strip())[:80] if request.strip() else "Interior"
    version = await run.snapshot("interior", label, summary, None, renders)
    await _modify_finish(run, version, summary, None)


# --------------------------------------------------------------------------
# chat (#35): the conversation's turn: answer, ask back, make a small change itself, hand a big
# one to the builder, go back to a version
# --------------------------------------------------------------------------

# the jobs that change the scene: while one runs, the chat only reads, and a change waits for it
BUILD_KINDS = ("intake", "generate", "modify", "interior")
CHAT_END_TOOLS = frozenset({"hand_over", "restore_version"})
CHAT_PICTURES = ("aerial", "south-photo", "south")  # the current version's, two at most


def _module_gist(text: str) -> str:
    """What a module is about: its first comment line, else what it exports."""
    for line in text.splitlines()[:6]:
        t = line.strip()
        if t.startswith("//"):
            return t.lstrip("/").strip()[:120]
        if t.startswith("/*"):
            return t.strip("/*").strip()[:120]
    names = re.findall(r"export\s+(?:async\s+)?(?:function|const|let|class)\s+(\w+)", text)
    return f"exports {', '.join(names[:8])}" if names else ""


def file_map(scene_dir: Path) -> str:
    """The scene's modules, each with its size and what it is about: where to look first."""
    lines = []
    for p in sorted((scene_dir / "src").rglob("*.js")):
        text = p.read_text(encoding="utf-8", errors="replace")
        gist = _module_gist(text)
        lines.append(
            f"- {p.relative_to(scene_dir)} ({len(text.splitlines())} lines)"
            + (f": {gist}" if gist else "")
        )
    return "## The scene's modules\n" + ("\n".join(lines) or "(none)")


def point_text(point: dict[str, Any] | None) -> str | None:
    """What the owner pointed at in the viewer with this message."""
    if not point:
        return None
    pos = point.get("position") or []
    lines = []
    if point.get("room"):
        n = point.get("room_index")
        lines.append(f"- room: {point['room']}" + (f" (view room-{n})" if n else ""))
    if point.get("object") or point.get("kind"):
        what = point.get("object") or point.get("kind")
        kind = f" ({point['kind']})" if point.get("kind") and point.get("object") else ""
        lines.append(f"- object: {what}{kind}")
    if len(pos) == 3:
        lines.append(
            f"- the spot: x={pos[0]:.2f}, y={pos[1]:.2f}, z={pos[2]:.2f} (scene metres; y up)"
        )
    return "## The owner pointed at this in the viewer\n" + "\n".join(lines)


POINTED_MODULE_LINES = 300  # a module longer than this is only named, not shown


def pointed_module(scene_dir: Path, point: dict[str, Any] | None) -> str | None:
    """The module that builds what the owner pointed at (its name appears in it), shown in full
    when it is not long: one read fewer before the change."""
    name = str((point or {}).get("object") or "").strip()
    if len(name) < 3:
        return None
    found = [
        p
        for p in sorted((scene_dir / "src").rglob("*.js"))
        if name in p.read_text(encoding="utf-8", errors="replace")
    ]
    if not found:
        return None
    rel = found[0].relative_to(scene_dir)
    text = found[0].read_text(encoding="utf-8", errors="replace")
    others = ", ".join(str(p.relative_to(scene_dir)) for p in found[1:4])
    also = f" (also named in {others})" if others else ""
    if len(text.splitlines()) > POINTED_MODULE_LINES:
        return f"## Where it is built\n'{name}' appears in {rel}{also}."
    return f"## Where it is built: {rel}{also}\n```js\n{text}\n```"


def _version_text(version: Any) -> list[str]:
    """The current version: what it is, the builder's open questions and left-out additions,
    the review's findings."""
    if version is None:
        return ["## The current version\nNone yet: the scene has not been built."]
    out = [f"## The current version: {version.number} ({version.kind}), {version.label}\n"
           f"{version.summary}"]  # fmt: skip
    questions = questions_of(version.questions_json)
    if questions:
        out.append("## The builder's open questions\n" + "\n".join(q.as_text() for q in questions))
    left = json.loads(version.suggestions_json) if version.suggestions_json else []
    if left:
        out.append(
            "## What the builder left out on purpose (the owner may ask for it)\n"
            + "\n".join(f"- {x}" for x in left)
        )
    if version.critique_json:
        review = Critique.model_validate_json(version.critique_json)
        if review.issues:
            out.append(f"## The review of version {version.number}\n{review.as_builder_feedback()}")
    return out


RECENT_VERSIONS = 6  # the versions a turn sees by number: "undo" is the one before the current


def versions_text(versions: list[Any], current: int) -> str | None:
    """The latest versions, newest first, the current one marked: what "undo" and "go back to
    version 4" refer to, including those made outside the conversation (a restore, a build)."""
    recent = sorted(versions, key=lambda v: v.number, reverse=True)[:RECENT_VERSIONS]
    if not recent:
        return None
    lines = [
        f"- {v.number} ({v.kind}): {v.label}" + ("  ← current" if v.number == current else "")
        for v in recent
    ]
    return '## The latest versions ("undo" goes back to the one before the current)\n' + "\n".join(
        lines
    )


def _chat_message(
    message: str,
    busy: bool,
    version: Any,
    history: list[Any],
    scene_dir: Path,
    rooms: list[dict[str, Any]],
    point: dict[str, Any] | None,
    pictures: dict[str, Path],
    attachments: list[Path],
    versions: list[Any] | None = None,
) -> list[ImagePart | str]:
    parts: list[ImagePart | str] = []
    if busy:
        parts.append(CHAT_BUSY_NOTE)
    parts.extend(_version_text(version))
    for text in (
        versions_text(versions or [], version.number if version else 0),
        thread_text(history),
        file_map(scene_dir),
        rooms_text(rooms),
        point_text(point),
        pointed_module(scene_dir, point),
    ):
        if text:
            parts.append(text)
    for view, p in pictures.items():
        parts.append(ImagePart.from_file(p, label=f"The current version — {view}"))
    for i, p in enumerate(attachments, 1):
        parts.append(ImagePart.from_file(p, label=f"Photograph the owner attached {i}"))
    parts.append(f"## The owner's message\n{message}")
    return parts


async def chat(ctx: JobContext) -> None:
    """The conversation's turn (#35): one call with the thread; it answers, asks back, makes a
    small change itself (a version, its view of the change in the answer), hands a bigger one
    to the builder (now, or once the running job ends) or restores a version."""
    storage = ProjectStorage(ctx.project_id)
    interior = has_interior(storage.scene_dir)
    run = await _Run.create(ctx, kit=kit_with("interior.js") if interior else None)
    run.interior = interior
    async with session_factory()() as session:
        job = await crud.get_job(session, ctx.job_id)
        project = await crud.get_project(session, ctx.project_id)
        history = await crud.list_chat(session, ctx.project_id)
        busy = await crud.active_job(session, ctx.project_id, BUILD_KINDS) is not None
    message = job.request_text
    own = [m for m in history if m.job_id == ctx.job_id and m.role == "user"]
    point = own[-1].point if own else None
    base = project.current_version
    version = next((v for v in project.versions if v.number == base), None)
    inp = await run.inputs()
    if interior and not busy:
        await run.prepare_interior(inp, base)
    run.tools.specs = chat_specs(can_change=not busy and base > 0, interior=interior)
    run.system = CHAT_INTERIOR_SYSTEM if interior else CHAT_SYSTEM
    run.base_version = base if run.unchanged_since(base) else None
    vdir = storage.versions_dir / str(base) / "renders"
    pictures = {v: vdir / f"{v}.jpg" for v in CHAT_PICTURES if (vdir / f"{v}.jpg").exists()}
    pictures = dict(list(pictures.items())[:2])
    thread = [m for m in history if m.job_id != ctx.job_id]
    parts = _chat_message(
        message, busy, version, thread, storage.scene_dir, run.rooms, point, pictures,
        inp.attachments, versions=list(project.versions),
    )  # fmt: skip
    await ctx.emit("phase", name="chat", message="Reading your message")
    await run.build(
        [Message.user(*parts)],
        max_steps=run.settings.CHAT_MAX_STEPS,
        effort=run.rs.chat_effort,
        cache_key=f"chat-{ctx.project_id}",
        end_tools=CHAT_END_TOOLS,
        reply_ok=lambda: run.tools.edits == 0,
    )
    last = run.last_run
    assert last is not None
    if last.ended_by == "hand_over":
        await _chat_hand_over(run, last.end_input, last.summary, busy, message)
    elif last.ended_by == "restore_version":
        await _chat_restore(run, int(last.end_input.get("number") or 0), last.summary)
    elif last.ended_by == "finish" or (run.tools.edits and run.tools.last_check_ok):
        await _chat_save(run, message, last.summary)
    elif run.tools.edits:
        # a change it could not finish in its steps: the builder does it from the saved version
        await _chat_hand_over(run, {"request": message}, "", busy, message)
    else:
        await _chat_answer(run, last.summary or "Sorry, I could not answer that. Try again?")


async def _chat_answer(
    run: _Run, text: str, version: int | None = None, pictures: list[str] | None = None, **done: Any
) -> None:
    async with session_factory()() as session, session.begin():
        await crud.add_chat_message(
            session, run.ctx.project_id, "assistant", text, run.ctx.job_id, version,
            pictures=pictures,
        )  # fmt: skip
    await run.emit_usage()
    await run.ctx.emit("done", version=version, summary=text, **done)


async def _chat_save(run: _Run, message: str, summary: str) -> None:
    """A small change made in the turn: a version at once, with the view that shows the change
    in the answer; the turn ends there (the owner can write again) and the version's pictures
    are drawn from its own page afterwards."""
    shown = {
        v: run.renders_dir / f"{v}.jpg"
        for v in run.tools.last_rendered
        if (run.renders_dir / f"{v}.jpg").exists()
    }
    label = " ".join(message.split())[:80] or "Small change"
    n = await run.snapshot("edit", label, summary, None, shown)
    pictures = [f"{run.storage.scene_url(n).rsplit('/', 1)[0]}/renders/{v}.jpg" for v in shown]
    async with session_factory()() as session, session.begin():
        await crud.add_chat_message(
            session, run.ctx.project_id, "assistant", summary, run.ctx.job_id, n,
            pictures=pictures,
        )  # fmt: skip
    await run.ctx.emit("answer", text=summary, version=n, pictures=pictures)
    await run.emit_usage()
    await run.ctx.emit("done", version=n, summary=summary, kind="edit")
    _in_background(_version_pictures(run, n), f"pictures-{run.ctx.project_id}-{n}")


_BACKGROUND: set[asyncio.Task[None]] = set()


def _in_background(work: Coroutine[Any, Any, None], name: str) -> None:
    """Work that outlives its job (a version's pictures): kept referenced until it ends."""
    task = asyncio.create_task(work, name=name)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


async def _version_pictures(run: _Run, n: int) -> None:
    """A version's pictures from every view, drawn from its own page (a later turn may already
    be editing the working copy), with the scene report kept next to them."""
    vdir = run.storage.versions_dir / str(n)
    url = f"{run.settings.render_base_url}{run.storage.scene_url(n)}?kit={run.kit}"
    try:
        res = await renderer.render(url, list(run.views), vdir / "renders", quality="high")
        if res.report is not None:
            _write_report(vdir, res.report)
        logger.info(
            "chat.version_pictures",
            extra={"project_id": run.ctx.project_id, "version": n, "views": len(res.images)},
        )
    except Exception:
        logger.exception("chat.version_pictures_failed", extra={"version": n})


def _revert_to(run: _Run, version: int | None) -> None:
    """Put the working copy back to the saved version the turn started from (its own edits go)."""
    if version and run.tools.edits:
        run.storage.restore(version)


async def _chat_hand_over(
    run: _Run, end: dict[str, Any], reply: str, busy: bool, message: str
) -> None:
    """A change for the builder: a modification (or the interior job) with the turn's request,
    started now, or waiting for the running job to end."""
    request = str(end.get("request") or "").strip() or message
    kind = "interior" if end.get("kind") == "interior" else "modify"
    _revert_to(run, run.base_version)
    async with session_factory()() as session, session.begin():
        project = await crud.get_project(session, run.ctx.project_id)
        stored = RunSettings.model_validate(project.settings) if project.settings else RunSettings()
        new = await crud.create_job(
            session,
            run.ctx.project_id,
            kind,
            request,
            settings=resolve(get_settings(), stored).model_dump(),
            status="waiting" if busy else "queued",
        )
    if not busy:
        run.ctx.manager.submit(new.id, run.ctx.project_id, JOB_BODIES[kind])
    text = str(end.get("reply") or reply or "").strip() or (
        "I'll do that as soon as the current job ends."
        if busy
        else "This needs the builder: it takes a few minutes."
    )
    await _chat_answer(run, text, handed_over=new.id, waiting=busy)


async def restore_to(session: Any, project_id: str, number: int) -> int:
    """Make a saved version the scene again, recorded as a new version (its pictures copied);
    returns the new version's number."""
    await crud.get_version(session, project_id, number)
    st = ProjectStorage(project_id)
    st.restore(number)
    n = st.next_version_number()
    dst = st.snapshot(n, kit=st.version_kit(number))
    src_renders = st.versions_dir / str(number) / "renders"
    if src_renders.exists():
        shutil.copytree(src_renders, dst / "renders", dirs_exist_ok=True)
    await crud.add_version(
        session, project_id, n, "restore", f"Restored version {number}",
        f"Restored from version {number}.",
    )  # fmt: skip
    return n


async def _chat_restore(run: _Run, number: int, reply: str) -> None:
    async with session_factory()() as session, session.begin():
        project = await crud.get_project(session, run.ctx.project_id)
        if number not in {v.number for v in project.versions}:
            known = ", ".join(str(v.number) for v in project.versions[-5:])
            text = f"There is no version {number}" + (f" (the latest: {known})." if known else ".")
            await crud.add_chat_message(
                session, run.ctx.project_id, "assistant", text, run.ctx.job_id
            )
            n = None
        else:
            n = await restore_to(session, run.ctx.project_id, number)
            await crud.update_job(session, run.ctx.job_id, result_version=n)
            text = reply.strip() or f"Back to version {number}, saved as version {n}."
            await crud.add_chat_message(
                session, run.ctx.project_id, "assistant", text, run.ctx.job_id, n
            )
    if n is not None:
        await run.ctx.emit(
            "version", number=n, kind="restore", label=f"Restored version {number}", summary=text,
            critic_score=None, scene_url=run.storage.scene_url(n),
            render_urls=run.storage.render_urls(n),
        )  # fmt: skip
    await run.emit_usage()
    await run.ctx.emit("done", version=n, summary=text)


async def start_waiting(manager: JobManager, project_id: str) -> str | None:
    """When a project's job ends: its oldest waiting change starts (#35). Returns its id."""
    async with session_factory()() as session, session.begin():
        if await crud.active_job(session, project_id, BUILD_KINDS):
            return None
        waiting = await crud.waiting_jobs(session, project_id)
        if not waiting:
            return None
        job = waiting[0]
        await crud.update_job(session, job.id, status="queued")
    manager.submit(job.id, project_id, JOB_BODIES[job.kind])
    logger.info("job.waiting_started", extra={"job_id": job.id, "project_id": project_id})
    return job.id


async def start_all_waiting(manager: JobManager) -> int:
    """At startup: the waiting changes of projects with nothing running start. How many."""
    async with session_factory()() as session:
        projects = {j.project_id for j in await crud.waiting_jobs(session)}
    return sum([await start_waiting(manager, pid) is not None for pid in sorted(projects)])


# --------------------------------------------------------------------------
# resume (#7): continue an interrupted job from its files after a server restart
# --------------------------------------------------------------------------


@dataclass
class _Progress:
    """How far a job got, read back from its persisted events."""

    last_phase: str | None = None  # name of the last `phase` event
    last_phase_message: str = ""
    versions: list[int] = field(default_factory=list)
    critics: list[dict[str, Any]] = field(default_factory=list)  # completed critic rounds
    done: bool = False
    builder_summary: str = ""  # from the last builder_done
    attempts: int = 0  # resumes so far

    @classmethod
    def from_events(cls, events: list[Any]) -> _Progress:
        p = cls()
        for ev in events:
            payload = json.loads(ev.payload_json) if isinstance(ev.payload_json, str) else {}
            if ev.type == "phase":
                p.last_phase = str(payload.get("name") or "")
                p.last_phase_message = str(payload.get("message") or "")
            elif ev.type == "version":
                p.versions.append(int(payload.get("number") or 0))
            elif ev.type == "critic":
                p.critics.append(payload)
            elif ev.type == "builder_done":
                p.builder_summary = str(payload.get("summary") or "")
            elif ev.type == "done":
                p.done = True
            elif ev.type == "resumed":
                p.attempts += 1
        return p

    @property
    def fixing(self) -> bool:
        """The builder was working on a critic's findings (or, in a job from before
        2026-09-13, a verifier's)."""
        return self.last_phase == "builder" and self.last_phase_message.startswith("Fixing")

    def round_number(self) -> int | None:
        """The critic round named by the last phase ("… (round 2)"), if any."""
        m = re.search(r"round (\d+)", self.last_phase_message)
        return int(m.group(1)) if m else None

    def last_verdict(self) -> Critique | None:
        if not self.critics:
            return None
        c = self.critics[-1]
        return Critique(
            overall_score=int(c.get("score") or 0),
            summary=str(c.get("summary") or ""),
            done=bool(c.get("done")),
            issues=c.get("issues") or [],
        )


async def resume_interrupted_jobs(manager: JobManager) -> int:
    """At startup: every job the previous process left `queued`, `running` (a crash) or
    `interrupted` (a graceful stop) is submitted again with the same id. Returns how many."""
    async with session_factory()() as session:
        jobs = await crud.list_unfinished_jobs(session)
    n = 0
    for job in jobs:
        async with session_factory()() as session, session.begin():
            attempts = await crud.count_job_events(session, job.id, "resumed")
            if attempts >= RESUME_MAX_ATTEMPTS:
                message = (
                    f"Interrupted {attempts} times by server restarts: not resuming again. "
                    "Start a new run to continue from the last version."
                )
                await crud.update_job(session, job.id, status="failed", error=message)
                await crud.settle_project_status(session, job.project_id)
                seq = await crud.last_event_seq(session, job.id) + 1
                await crud.add_job_event(session, job.id, seq, "error", {"message": message})
                logger.warning(
                    "job.resume.given_up", extra={"job_id": job.id, "attempts": attempts}
                )
                continue
        manager.submit(job.id, job.project_id, resume)
        logger.info(
            "job.resume", extra={"job_id": job.id, "kind": job.kind, "attempt": attempts + 1}
        )
        n += 1
    return n


async def resume(ctx: JobContext) -> None:
    """Continue an interrupted job from the stage its events show was in progress: the files
    the builder wrote are on disk, only its conversation is lost, so the stage's LLM step
    is repeated with a fresh conversation that starts from the current renders."""
    async with session_factory()() as session:
        job = await crud.get_job(session, ctx.job_id)
        events = await crud.list_job_events(session, ctx.job_id)
    progress = _Progress.from_events(events)
    if progress.done:
        # crashed between the `done` event and the status update: nothing left to do
        if job.kind == "generate":
            async with session_factory()() as session, session.begin():
                await crud.set_status(session, ctx.project_id, "ready")
        return
    await ctx.emit("resumed", reason="server restart", attempt=progress.attempts + 1)
    if job.kind == "intake":
        await intake(ctx)
    elif job.kind == "interior":
        # its conversation is lost but its files are there: a fresh pass continues from them
        await interior(ctx)
    elif job.kind == "generate":
        await _resume_generate(ctx, progress)
    elif job.kind == "chat":
        await _resume_chat(ctx, progress)
    else:
        await _resume_modify(ctx, job, progress)


async def _resume_chat(ctx: JobContext, progress: _Progress) -> None:
    """A turn is short: it starts again from the message, its unfinished edits undone (the
    working copy back to the current version). A turn that had saved its version is over."""
    if progress.versions:
        await ctx.emit("done", version=progress.versions[-1], summary=progress.builder_summary)
        return
    async with session_factory()() as session:
        project = await crud.get_project(session, ctx.project_id)
        events = await crud.list_job_events(session, ctx.job_id)
    edited = any(
        e.type == "builder_step" and json.loads(e.payload_json).get("tool") in _WRITE_TOOLS
        for e in events
    )
    if edited and project.current_version > 0:
        ProjectStorage(ctx.project_id).restore(project.current_version)
    await chat(ctx)


_WRITE_TOOLS = ("write_file", "edit_file", "apply_patch", "delete_file")


async def _resume_generate(ctx: JobContext, progress: _Progress) -> None:
    run = await _Run.create(ctx)
    async with session_factory()() as session, session.begin():
        await crud.set_status(session, ctx.project_id, "generating")
    inp = await run.inputs()
    await ctx.emit("phase", name="render", message="Rendering the scene as the restart left it")
    renders = await run.render_standard()
    rounds_done = len(progress.critics)

    if progress.last_phase == "critic" and progress.versions:
        # the version is saved; the critic's round was lost (judge it again), or it was judged
        # and the builder's fix pass is what was lost
        version = progress.versions[-1]
        summary = progress.builder_summary
        messages = [Message.user(*_first_message(inp, resume_renders=run.current_renders()))]
        current = progress.round_number() or rounds_done + 1
        judged = progress.last_verdict() if rounds_done >= current else None
        await _critic_rounds(
            run, inp, messages, renders, version, summary, start=current, judged=judged
        )
        return

    # the builder was working (first build or a fix round): give it the scene as it stands
    verdict = progress.last_verdict() if progress.fixing else None
    parts = _first_message(inp, resume_renders=run.current_renders())
    if verdict is not None:
        parts.append(_critic_feedback_text(inp.reference or "photos", verdict))
    messages = [Message.user(*parts)]
    await ctx.emit("phase", name="builder", message="Continuing the build after the restart")
    summary, renders = await run.build_and_render(messages)
    if verdict is not None:
        fixed = progress.round_number() or rounds_done
        version = await run.snapshot(
            "critique", f"After critic round {fixed}", summary, None, renders
        )
        await _critic_rounds(run, inp, messages, renders, version, summary, start=fixed + 1)
    else:
        version = await run.snapshot("generation", "Initial build", summary, None, renders)
        await _critic_rounds(run, inp, messages, renders, version, summary)


async def _resume_modify(ctx: JobContext, job: Job, progress: _Progress) -> None:
    run = await _modify_run(ctx)
    request = job.request_text
    async with session_factory()() as session:
        history = [m for m in await crud.list_chat(session, ctx.project_id) if m.job_id != job.id]
    inp = await run.inputs()
    if run.interior:
        await run.prepare_interior(inp)

    # the "before" renders were copied aside before the builder started; when they are not
    # there the builder had not started either, so the working copy still is the "before"
    keep = run.storage.scene_dir / "renders_before"
    kept = {p.stem: p for p in sorted(keep.glob("*.jpg"))} if keep.exists() else {}
    if progress.last_phase in (None, "render") or not kept:
        await ctx.emit("phase", name="render", message="Rendering the current state")
        before = await run.render_standard()
        before |= await run.render_views(room_views(touched_rooms(request, run.rooms)))
        before_copy = _keep_before_renders(run, before)
        messages = [Message.user(*_modify_message(request, history, inp, before, rooms=run.rooms))]
        await _modify_apply(run, inp, request, messages, before_copy)
        return

    await ctx.emit("phase", name="render", message="Rendering the scene as the restart left it")
    after = await run.render_standard()
    verdict = progress.last_verdict()
    if progress.versions and (progress.last_phase == "critic" or progress.fixing):
        # the version is saved: verify it again, or attach the verdict if it was persisted
        # ("fixing": a job from before 2026-09-13, interrupted in the fix pass that no longer
        # exists; its verdict goes on the version it inspected)
        version = progress.versions[-1]
        summary = progress.builder_summary
        if verdict is not None:
            await _modify_after_verdict(run, verdict, version, summary)
        else:
            messages = [
                Message.user(
                    *_modify_message(request, history, inp, after, resume=True, rooms=run.rooms)
                )
            ]
            await _modify_verify(run, inp, request, messages, kept, after, version, summary)
        return

    messages = [
        Message.user(*_modify_message(request, history, inp, after, resume=True, rooms=run.rooms))
    ]
    await _modify_apply(run, inp, request, messages, kept)


# what each kind of job runs (the router starts them; a chat turn hands over to them)
JOB_BODIES = {
    "intake": intake,
    "generate": generate,
    "modify": modify,
    "interior": interior,
    "chat": chat,
}
