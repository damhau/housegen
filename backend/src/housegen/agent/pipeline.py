"""Orchestration of the agentic workflow.

generate:  builder(plans + photos, self-assessing via renders) → [critic ⇄ builder]* → version
modify:    builder(request) → verifier → [builder]? → version
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from housegen.agent import critic
from housegen.agent.builder import BuilderRun, run_builder
from housegen.agent.progress import LiveProgress
from housegen.agent.prompts import BUILDER_SYSTEM, FIRST_RUN_ADDENDUM, MODIFY_ADDENDUM
from housegen.agent.schemas import Critique
from housegen.agent.tools import BuilderTools
from housegen.agent.workspace import Workspace
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import JobContext
from housegen.llm import ImagePart, Message, Usage, get_provider
from housegen.projects import crud
from housegen.projects.schemas import STANDARD_VIEWS
from housegen.projects.storage import ProjectStorage
from housegen.render.renderer import renderer

logger = logging.getLogger(__name__)


class _Run:
    """Shared plumbing for one job."""

    def __init__(self, ctx: JobContext) -> None:
        self.ctx = ctx
        self.settings = get_settings()
        self.provider = get_provider()
        self.storage = ProjectStorage(ctx.project_id)
        self.storage.ensure()
        self.storage.init_scene_from_template()
        self.workspace = Workspace(self.storage.scene_dir, readonly={"kit": self.settings.KIT_DIR})
        self.scene_url = self.settings.render_base_url + self.storage.scene_url()
        self.renders_dir = self.storage.scene_dir / "renders"
        self.last_run: BuilderRun | None = (
            None  # the most recent builder pass (suggestions, questions)
        )
        self.usage = Usage()  # everything, builder + critic
        self.critic_usage = Usage()  # the critic's share, reported separately in the usage event
        self.tools = BuilderTools(
            self.workspace, renderer, self.scene_url, self.renders_dir, on_render=self._on_render
        )

    async def _on_render(self, images: dict[str, Path], errors: list[str]) -> None:
        urls = {
            v: f"/scenes/{self.ctx.project_id}/scene/renders/{p.name}?t={int(p.stat().st_mtime)}"
            for v, p in images.items()
        }
        await self.ctx.emit("render", renders=urls, errors=errors[:5])

    async def on_step(self, ev: dict[str, Any]) -> None:
        if ev.get("kind") == "text":
            await self.ctx.emit("builder_text", text=ev["text"], step=ev.get("step"))
        else:
            await self.ctx.emit("builder_step", **{k: v for k, v in ev.items() if k != "kind"})

    async def photos(self) -> tuple[dict[str, Path], list[Path]]:
        """(façade photos by side, other photos in upload order)."""
        async with session_factory()() as session:
            project = await crud.get_project(session, self.ctx.project_id)
            facades = {
                p.side: self.storage.photos_dir / p.filename
                for p in project.photos
                if p.side != "other"
            }
            extras = [
                self.storage.photos_dir / p.filename for p in project.photos if p.side == "other"
            ]
            return facades, extras

    async def render_standard(self) -> dict[str, Path]:
        res = await renderer.render(
            self.scene_url, STANDARD_VIEWS, self.renders_dir, quality="high"
        )
        await self._on_render(res.images, res.errors)
        return res.images

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
            self.settings.resolve_model("builder"),
            system,
            messages,
            self.tools,
            max_steps=self.settings.BUILDER_MAX_STEPS,
            max_tokens=self.settings.LLM_MAX_TOKENS,
            effort=self.settings.BUILDER_EFFORT,
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

    def add_critic_usage(self, usage: Usage) -> None:
        self.usage = self.usage + usage
        self.critic_usage = self.critic_usage + usage

    async def emit_usage(self) -> None:
        fields = {
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "cache_read_tokens": self.usage.cache_read_tokens,
            "critic_input_tokens": self.critic_usage.input_tokens,
            "critic_output_tokens": self.critic_usage.output_tokens,
        }
        logger.info("run.usage", extra=fields)
        await self.ctx.emit("usage", **fields)


# --------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------


async def generate(ctx: JobContext) -> None:
    run = _Run(ctx)
    s = run.settings
    pid = ctx.project_id
    async with session_factory()() as session, session.begin():
        await crud.set_status(session, pid, "generating")
    photos, extras = await run.photos()
    pages = run.storage.plan_page_paths()

    # 1. builder: reads the plans and photos itself, renders, self-corrects
    run.storage.init_scene_from_template(force=True)
    await ctx.emit(
        "phase", name="builder", message="Reading the plans and photos, building the scene"
    )
    messages = [Message.user(FIRST_RUN_ADDENDUM, *_initial_parts(photos, extras, pages))]
    summary = await run.build(messages)
    renders = await run.render_standard()
    version = await run.snapshot("generation", "Initial build", summary, None, renders)

    # 2. independent critic (optional, CRITIC_MAX_ITERATIONS=0 disables)
    score: int | None = None
    for i in range(1, s.CRITIC_MAX_ITERATIONS + 1):
        await ctx.emit(
            "phase", name="critic", message=f"Independent review against the photos (round {i})"
        )
        async with LiveProgress(ctx, "critic", show_text=False) as live:
            verdict, usage = await critic.critique_against_photos(
                run.provider,
                s.resolve_model("critic"),
                photos,
                renders,
                s.CRITIC_SCORE_THRESHOLD,
                s.LLM_MAX_TOKENS,
                extras=extras,
                on_progress=live.on_event,
                effort=s.CRITIC_EFFORT,
            )
        run.add_critic_usage(usage)
        score = verdict.overall_score
        await _emit_critic(ctx, i, verdict)
        await run.set_version_critique(version, verdict)
        no_major = not any(x.severity == "major" for x in verdict.issues)
        if verdict.done or (score >= s.CRITIC_SCORE_THRESHOLD and no_major):
            break
        if i == s.CRITIC_MAX_ITERATIONS:
            break
        await ctx.emit("phase", name="builder", message=f"Fixing the critic's findings (round {i})")
        messages.append(
            Message.user(
                "An independent critic compared your renders with the photographs. Address the "
                "points below, most impactful first, verify with renders, run check_scene, "
                "then finish.\n\n" + verdict.as_builder_feedback()
            )
        )
        summary = await run.build(messages)
        renders = await run.render_standard()
        version = await run.snapshot("critique", f"After critic round {i}", summary, None, renders)

    async with session_factory()() as session, session.begin():
        await crud.set_status(session, pid, "ready")
        await crud.add_chat_message(session, pid, "assistant", summary, ctx.job_id, version)
    await run.emit_usage()
    await ctx.emit("done", version=version, score=score, summary=summary, **run.finish_extras())


def _initial_parts(
    photos: dict[str, Path], extras: list[Path], pages: list[Path]
) -> list[ImagePart | str]:
    parts: list[ImagePart | str] = [
        "Build this house. Below are the plan sheets and the photographs. Photographs labelled "
        "with a side show that façade; the others show details, other angles or the surroundings."
    ]
    for i, p in enumerate(pages, 1):
        parts.append(ImagePart.from_file(p, label=f"Plan sheet {i} of {len(pages)}"))
    for side, p in photos.items():
        parts.append(ImagePart.from_file(p, label=f"Photograph of the {side} façade"))
    for i, p in enumerate(extras, 1):
        parts.append(ImagePart.from_file(p, label=f"Additional photograph {i} of {len(extras)}"))
    parts.append(
        "The workspace holds a placeholder scene from a template; replace it entirely. "
        "Start whenever you are ready."
    )
    return parts


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
    run = _Run(ctx)
    s = run.settings
    pid = ctx.project_id
    async with session_factory()() as session:
        job = await crud.get_job(session, ctx.job_id)
        request = job.request_text
        history = await crud.list_chat(session, pid)
    photos, extras = await run.photos()

    await ctx.emit("phase", name="render", message="Rendering the current state")
    before = await run.render_standard()
    before_copy: dict[str, Path] = {}
    keep = run.storage.scene_dir / "renders_before"
    keep.mkdir(exist_ok=True)
    for v, p in before.items():
        before_copy[v] = Path(shutil.copy2(p, keep / p.name))

    parts: list[ImagePart | str] = [MODIFY_ADDENDUM, f"## Request\n{request}"]
    recent = [m for m in history if m.role == "user"][-6:-1]
    if recent:
        parts.append(
            "## Earlier requests on this scene (already applied)\n"
            + "\n".join(f"- {m.content}" for m in recent)
        )
    for side, p in photos.items():
        parts.append(ImagePart.from_file(p, label=f"Photograph of the {side} façade (reference)"))
    for i, p in enumerate(extras[:8], 1):
        parts.append(ImagePart.from_file(p, label=f"Additional photograph {i} (reference)"))
    for view, p in before.items():
        if view in ("aerial", "south", "north"):
            parts.append(ImagePart.from_file(p, label=f"Current render — {view}"))
    messages = [Message.user(*parts)]

    await ctx.emit("phase", name="builder", message="Applying the modification")
    summary = await run.build(messages)
    after = await run.render_standard()
    version = await run.snapshot("modification", request[:80], summary, None, after)

    if s.CRITIC_MAX_ITERATIONS > 0:
        await ctx.emit("phase", name="critic", message="Verifying the modification")
        async with LiveProgress(ctx, "critic", show_text=False) as live:
            verdict, usage = await critic.verify_modification(
                run.provider,
                s.resolve_model("critic"),
                request,
                before_copy,
                after,
                s.LLM_MAX_TOKENS,
                on_progress=live.on_event,
                effort=s.CRITIC_EFFORT,
            )
        run.add_critic_usage(usage)
        await _emit_critic(ctx, 1, verdict)
        if not verdict.done and verdict.issues:
            await ctx.emit("phase", name="builder", message="Fixing what the verifier flagged")
            messages.append(
                Message.user(
                    "A verifier compared before/after renders against the request. Address these "
                    "points, render to verify, run check_scene, then finish.\n\n"
                    + verdict.as_builder_feedback()
                )
            )
            summary = await run.build(messages)
            after = await run.render_standard()
            version = await run.snapshot(
                "modification", request[:80], summary, verdict.overall_score, after
            )
        else:
            await run.set_version_critique(version, verdict)
        score: int | None = verdict.overall_score
    else:
        score = None

    shutil.rmtree(keep, ignore_errors=True)
    async with session_factory()() as session, session.begin():
        await crud.add_chat_message(session, pid, "assistant", summary, ctx.job_id, version)
    await run.emit_usage()
    await ctx.emit("done", version=version, score=score, summary=summary, **run.finish_extras())
