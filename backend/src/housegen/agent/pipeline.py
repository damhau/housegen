"""Orchestration of the agentic workflow.

intake:    read the plan sheets (no photographs) → summary, sheet map, questions for the owner
generate:  builder(plans [+ photos] [+ brief], self-assessing via renders) → [critic ⇄ builder]* → version
modify:    builder(request) → verifier → [builder]? → version
resume:    after a server restart, continue an interrupted job from its files (#7)

generate and modify are written as stages (build → critic rounds, apply → verify → fix) so that
`resume` can enter them at the stage the persisted events show was in progress.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
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
    FIRST_RUN_ADDENDUM,
    MODIFY_ADDENDUM,
    PLAN_ONLY_ADDENDUM,
    RESUME_ADDENDUM,
)
from housegen.agent.run_settings import ResolvedRunSettings, resolve
from housegen.agent.schemas import Critique, Intake
from housegen.agent.tools import BuilderTools, ImageSources
from housegen.agent.workspace import Workspace
from housegen.core.config import Settings, get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import JobContext, JobManager
from housegen.llm import Completion, ImagePart, Message, Usage, get_provider
from housegen.projects import crud
from housegen.projects.models import Job
from housegen.projects.schemas import standard_views
from housegen.projects.storage import PlanSheet, ProjectStorage
from housegen.render.renderer import renderer

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

    def __init__(self, ctx: JobContext, rs: ResolvedRunSettings | None = None) -> None:
        self.ctx = ctx
        self.settings = get_settings()
        # what this job runs with (#18): the snapshot taken when it was started, so a settings
        # change never affects a running job; .env defaults when there is none
        self.rs = rs or resolve(self.settings)
        self.provider = get_provider(self.rs.provider)
        self.storage = ProjectStorage(ctx.project_id)
        self.storage.ensure()
        self.storage.init_scene_from_template()
        self.workspace = Workspace(self.storage.scene_dir, readonly={"kit": self.settings.KIT_DIR})
        self.scene_url = self.settings.render_base_url + self.storage.scene_url()
        self.renders_dir = self.storage.scene_dir / "renders"
        self.last_run: BuilderRun | None = (
            None  # the most recent builder pass (suggestions, questions)
        )
        self.last_render_errors: list[str] = []  # from the most recent version render
        self.usage = Usage()  # everything, builder + critic
        self.critic_usage = Usage()  # the critic's share, reported separately in the usage event
        self.metrics = RunMetrics(self.settings)  # time and tokens per turn (#13)
        self.views = standard_views(True)  # what every saved version is rendered from
        self.tools = BuilderTools(
            self.workspace, renderer, self.scene_url, self.renders_dir, on_render=self._on_render
        )

    @classmethod
    async def create(cls, ctx: JobContext) -> _Run:
        async with session_factory()() as session:
            job = await crud.get_job(session, ctx.job_id)
            stored = job.settings
        rs = ResolvedRunSettings.model_validate(stored) if stored else None
        run = cls(ctx, rs)
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
        await self._on_render(res.images, res.errors)
        return res.images

    async def build_and_render(
        self, messages: list[Message], system: str = BUILDER_SYSTEM
    ) -> tuple[str, dict[str, Path]]:
        """A builder pass followed by the version render. When the render produces no image at
        any quality, the builder hears why and gets one more pass before the version is saved
        (an empty version would otherwise go to the critic as a 0/100 placeholder)."""
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
        dst = self.storage.snapshot(n)
        for view, p in renders.items():
            shutil.copy2(p, dst / "renders" / f"{view}.jpg")
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
                questions=last.questions if last else None,
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

    async def build(self, messages: list[Message], system: str = BUILDER_SYSTEM) -> str:
        run = await run_builder(
            self.provider,
            self.rs.model,
            system,
            messages,
            self.tools,
            max_steps=self.rs.max_steps,
            max_tokens=self.rs.max_tokens,
            effort=self.rs.builder_effort,
            on_step=self.on_step,
            progress=lambda step: LiveProgress(self.ctx, "builder", step=step, show_text=True),
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

    async def set_version_critique(self, version: int, verdict: Critique) -> None:
        async with session_factory()() as session, session.begin():
            await crud.set_version_critique(
                session, self.ctx.project_id, version, verdict.overall_score, verdict.model_dump()
            )

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
        if _verdict_ends_rounds(verdict, i, s, rs.critic_rounds):
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
    await ctx.emit(
        "critic",
        iteration=iteration,
        score=verdict.overall_score,
        done=verdict.done,
        summary=verdict.summary,
        issues=[i.model_dump() for i in verdict.issues],
    )


# --------------------------------------------------------------------------
# modify
# --------------------------------------------------------------------------


async def modify(ctx: JobContext) -> None:
    run = await _Run.create(ctx)
    async with session_factory()() as session:
        job = await crud.get_job(session, ctx.job_id)
        request = job.request_text
        history = await crud.list_chat(session, ctx.project_id)
    inp = await run.inputs()

    await ctx.emit("phase", name="render", message="Rendering the current state")
    before = await run.render_standard()
    before_copy = _keep_before_renders(run, before)

    parts = _modify_message(request, history, inp, before)
    messages = [Message.user(*parts)]
    await _modify_apply(run, inp, request, messages, before_copy)


def _keep_before_renders(run: _Run, before: dict[str, Path]) -> dict[str, Path]:
    """Copy the "before" renders aside: the working copy's renders are overwritten by the
    builder's own render calls (and the copies survive a restart, see `resume`)."""
    keep = run.storage.scene_dir / "renders_before"
    shutil.rmtree(keep, ignore_errors=True)
    keep.mkdir(exist_ok=True)
    return {v: Path(shutil.copy2(p, keep / p.name)) for v, p in before.items()}


def _modify_message(
    request: str,
    history: list[Any],
    inp: _Inputs,
    renders: dict[str, Path],
    resume: bool = False,
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
    recent = [m for m in history if m.role == "user"][-6:-1]
    if recent:
        parts.append(
            "## Earlier requests on this scene (already applied)\n"
            + "\n".join(f"- {m.content}" for m in recent)
        )
    if inp.brief:
        parts.append(f"{BRIEF_HEADING}\n{inp.brief}")
    for side, p in inp.photos.items():
        parts.append(ImagePart.from_file(p, label=f"Photograph of the {side} façade (reference)"))
    for i, p in enumerate(inp.extras[:8], 1):
        parts.append(ImagePart.from_file(p, label=f"Additional photograph {i} (reference)"))
    if not inp.has_photos:
        parts.extend(_elevation_sheets(inp.intake, inp.pages))
    for view, p in renders.items():
        if view in ("aerial", "south", "north"):
            parts.append(ImagePart.from_file(p, label=f"Current render — {view}"))
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
            )
        await run.add_call("critic", completion)
        await _emit_critic(ctx, 1, verdict)
        await _modify_after_verdict(run, request, messages, verdict, version, summary)
        return
    await _modify_finish(run, version, summary, None)


async def _modify_after_verdict(
    run: _Run,
    request: str,
    messages: list[Message],
    verdict: Critique,
    version: int,
    summary: str,
) -> None:
    if not verdict.done and verdict.issues:
        messages.append(Message.user(_verifier_feedback_text(verdict)))
        version, summary = await _modify_fix(run, request, messages, verdict)
    else:
        await run.set_version_critique(version, verdict)
    await _modify_finish(run, version, summary, verdict.overall_score)


def _verifier_feedback_text(verdict: Critique) -> str:
    return (
        "A verifier compared before/after renders against the request. Address these "
        "points, render to verify, run check_scene, then finish.\n\n"
        + verdict.as_builder_feedback()
    )


async def _modify_fix(
    run: _Run, request: str, messages: list[Message], verdict: Critique
) -> tuple[int, str]:
    await run.ctx.emit("phase", name="builder", message="Fixing what the verifier flagged")
    summary, after = await run.build_and_render(messages)
    version = await run.snapshot(
        "modification", request[:80], summary, verdict.overall_score, after
    )
    return version, summary


async def _modify_finish(run: _Run, version: int, summary: str, score: int | None) -> None:
    shutil.rmtree(run.storage.scene_dir / "renders_before", ignore_errors=True)
    async with session_factory()() as session, session.begin():
        await crud.add_chat_message(
            session, run.ctx.project_id, "assistant", summary, run.ctx.job_id, version
        )
    await run.emit_usage()
    await run.ctx.emit("done", version=version, score=score, summary=summary, **run.finish_extras())


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
        """The builder was working on a critic's / verifier's findings."""
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
    elif job.kind == "generate":
        await _resume_generate(ctx, progress)
    else:
        await _resume_modify(ctx, job, progress)


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
    run = await _Run.create(ctx)
    request = job.request_text
    async with session_factory()() as session:
        history = await crud.list_chat(session, ctx.project_id)
    inp = await run.inputs()

    # the "before" renders were copied aside before the builder started; when they are not
    # there the builder had not started either, so the working copy still is the "before"
    keep = run.storage.scene_dir / "renders_before"
    kept = {p.stem: p for p in sorted(keep.glob("*.jpg"))} if keep.exists() else {}
    if progress.last_phase in (None, "render") or not kept:
        await ctx.emit("phase", name="render", message="Rendering the current state")
        before = await run.render_standard()
        before_copy = _keep_before_renders(run, before)
        messages = [Message.user(*_modify_message(request, history, inp, before))]
        await _modify_apply(run, inp, request, messages, before_copy)
        return

    await ctx.emit("phase", name="render", message="Rendering the scene as the restart left it")
    after = await run.render_standard()
    verdict = progress.last_verdict()
    if progress.last_phase == "critic" and progress.versions:
        # the version is saved: verify it again, or act on the verdict if it was persisted
        version = progress.versions[-1]
        summary = progress.builder_summary
        messages = [Message.user(*_modify_message(request, history, inp, after, resume=True))]
        if verdict is not None:
            await _modify_after_verdict(run, request, messages, verdict, version, summary)
        else:
            await _modify_verify(run, inp, request, messages, kept, after, version, summary)
        return

    parts = _modify_message(request, history, inp, after, resume=True)
    fixing = progress.fixing and verdict is not None
    if fixing and verdict is not None:
        parts.append(_verifier_feedback_text(verdict))
    messages = [Message.user(*parts)]
    if fixing and verdict is not None:
        version, summary = await _modify_fix(run, request, messages, verdict)
        await _modify_finish(run, version, summary, verdict.overall_score)
    else:
        await _modify_apply(run, inp, request, messages, kept)
