from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from housegen.agent.prompts import CRITIC_MODIFY_SYSTEM, CRITIC_PLAN_SYSTEM, CRITIC_SYSTEM
from housegen.agent.schemas import Critique
from housegen.core.exceptions import LLMError
from housegen.llm import ImagePart, Message, ProgressCallback, Provider, Usage

logger = logging.getLogger(__name__)


async def critique_against_photos(
    provider: Provider,
    model: str,
    photos: dict[str, Path],
    renders: dict[str, Path],
    threshold: int,
    max_tokens: int,
    extras: list[Path] | None = None,
    on_progress: ProgressCallback | None = None,
    effort: str | None = None,
) -> tuple[Critique, Usage]:
    parts: list[ImagePart | str] = [f"Score threshold for done: {threshold}."]
    pairs = 0
    extras = extras or []
    for side, photo in photos.items():
        # the photo-like view shares the photographer's viewpoint (eye 1.6 m, in front of the
        # façade); the elevated wide shot is only a fallback for scenes rendered before it existed
        render = renders.get(f"{side}-photo")
        elevated = render is None
        if elevated:
            render = renders.get(side)
        if render is None:
            continue
        pairs += 1
        parts.append(f"--- Façade: {side} ---")
        parts.append(ImagePart.from_file(photo, label=f"PHOTO of the {side} side (ground truth)"))
        parts.append(
            ImagePart.from_file(
                render,
                label=(
                    f"RENDER of the model, elevated wide camera on the {side} side (heights read differently than in the photo)"
                    if elevated
                    else f"RENDER of the model, photo-like camera at eye level in front of the {side} façade"
                ),
            )
        )
    if "aerial" in renders:
        parts.append(
            ImagePart.from_file(
                renders["aerial"], label="RENDER aerial view (for massing and site)"
            )
        )
    if pairs == 0:
        # nothing labelled by side: give everything and let the critic match photos to renders
        if not extras and not renders:
            raise LLMError("no photos or renders available for the critic")
        parts.append(
            "The photographs are not labelled by side. Match each one to the render taken from "
            "the same side yourself, and name the side you believe it shows in each issue."
        )
        for i, p in enumerate(extras, 1):
            parts.append(ImagePart.from_file(p, label=f"PHOTO {i} of {len(extras)} (ground truth)"))
        for view, p in _critic_views(renders).items():
            parts.append(ImagePart.from_file(p, label=f"RENDER of the model, view '{view}'"))
    elif extras:
        parts.append(
            "Additional photographs (details, other angles, surroundings) for reference; "
            "use them to judge details the façade photos do not show:"
        )
        for i, p in enumerate(extras[:8], 1):
            parts.append(ImagePart.from_file(p, label=f"Additional photograph {i}"))
    parts.append("Return the critique as JSON matching the schema.")
    completion = await provider.complete(
        model=model,
        system=CRITIC_SYSTEM,
        messages=[Message.user(*parts)],
        response_schema=Critique.model_json_schema(),
        max_tokens=max_tokens,
        on_progress=on_progress,
        effort=effort,
    )
    return _parse(completion.message.text), completion.usage


async def critique_against_plans(
    provider: Provider,
    model: str,
    elevation_pages: dict[str, int],
    pages: list[Path],
    renders: dict[str, Path],
    threshold: int,
    max_tokens: int,
    on_progress: ProgressCallback | None = None,
    effort: str | None = None,
) -> tuple[Critique, Usage]:
    """No photographs: judge the model against the elevation drawings of the plan set.

    `elevation_pages` maps a façade side to the 1-based sheet that draws its elevation (from
    the intake); each sheet is sent once, then the straight-on `<side>-elevation` render of
    every façade it draws (the elevated wide view is the fallback for older versions).
    """
    parts: list[ImagePart | str] = [f"Score threshold for done: {threshold}."]
    by_page: dict[int, list[str]] = {}
    for side, page in elevation_pages.items():
        if 1 <= page <= len(pages):
            by_page.setdefault(page, []).append(side)
    for page, sides in sorted(by_page.items()):
        parts.append(
            ImagePart.from_file(
                pages[page - 1],
                label=f"PLAN SHEET {page}: elevation drawing of the {', '.join(sides)} façade(s) (ground truth)",
            )
        )
    pairs = 0
    for page, sides in sorted(by_page.items()):
        for side in sides:
            render = renders.get(f"{side}-elevation")
            elevated = render is None
            if elevated:
                render = renders.get(side)
            if render is None:
                continue
            pairs += 1
            parts.append(f"--- Façade: {side} (its elevation is drawn on sheet {page}) ---")
            parts.append(
                ImagePart.from_file(
                    render,
                    label=(
                        f"RENDER of the model, elevated wide camera on the {side} side (perspective: heights read differently than in the drawing)"
                        if elevated
                        else f"RENDER of the model, straight-on elevation view of the {side} façade"
                    ),
                )
            )
    if "aerial" in renders:
        parts.append(
            ImagePart.from_file(
                renders["aerial"], label="RENDER aerial view (for massing and site)"
            )
        )
    if pairs == 0:
        raise LLMError("no elevation drawing / render pair available for the critic")
    parts.append("Return the critique as JSON matching the schema.")
    completion = await provider.complete(
        model=model,
        system=CRITIC_PLAN_SYSTEM,
        messages=[Message.user(*parts)],
        response_schema=Critique.model_json_schema(),
        max_tokens=max_tokens,
        on_progress=on_progress,
        effort=effort,
    )
    return _parse(completion.message.text), completion.usage


async def verify_modification(
    provider: Provider,
    model: str,
    request: str,
    before: dict[str, Path],
    after: dict[str, Path],
    max_tokens: int,
    on_progress: ProgressCallback | None = None,
    effort: str | None = None,
    attachments: list[Path] | None = None,
) -> tuple[Critique, Usage]:
    parts: list[ImagePart | str] = [f"User request:\n{request}"]
    if attachments:
        parts.append(
            "Photographs the user attached to the request (ground truth for what was asked):"
        )
        for i, p in enumerate(attachments, 1):
            parts.append(ImagePart.from_file(p, label=f"ATTACHED photograph {i}"))
    parts.append("Renders BEFORE the change:")
    for view, p in _critic_views(before).items():
        parts.append(ImagePart.from_file(p, label=f"BEFORE — view {view}"))
    parts.append("Renders AFTER the change:")
    for view, p in _critic_views(after).items():
        parts.append(ImagePart.from_file(p, label=f"AFTER — view {view}"))
    parts.append("Return the verdict as JSON matching the schema (threshold 85).")
    completion = await provider.complete(
        model=model,
        system=CRITIC_MODIFY_SYSTEM,
        messages=[Message.user(*parts)],
        response_schema=Critique.model_json_schema(),
        max_tokens=max_tokens,
        on_progress=on_progress,
        effort=effort,
    )
    return _parse(completion.message.text), completion.usage


def _critic_views(renders: dict[str, Path]) -> dict[str, Path]:
    """The critic's set: photo-like façade views + the aerial for massing; the elevated side
    views only when no photo-like view exists (older versions)."""
    photo = {v: p for v, p in renders.items() if v.endswith("-photo")}
    if not photo:
        return renders
    if "aerial" in renders:
        photo["aerial"] = renders["aerial"]
    return photo


def _parse(text: str) -> Critique:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        c = Critique.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        logger.exception("critic.parse_failed")
        raise LLMError(f"critic returned invalid JSON: {e}") from e
    logger.info(
        "critic.done", extra={"score": c.overall_score, "done": c.done, "issues": len(c.issues)}
    )
    return c
