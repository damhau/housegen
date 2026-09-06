from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from housegen.agent.prompts import CRITIC_MODIFY_SYSTEM, CRITIC_SYSTEM
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
        render = renders.get(side)
        if render is None:
            continue
        pairs += 1
        parts.append(f"--- Façade: {side} ---")
        parts.append(ImagePart.from_file(photo, label=f"PHOTO of the {side} side (ground truth)"))
        parts.append(
            ImagePart.from_file(render, label=f"RENDER of the model, camera on the {side} side")
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
        for view, p in renders.items():
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


async def verify_modification(
    provider: Provider,
    model: str,
    request: str,
    before: dict[str, Path],
    after: dict[str, Path],
    max_tokens: int,
    on_progress: ProgressCallback | None = None,
    effort: str | None = None,
) -> tuple[Critique, Usage]:
    parts: list[ImagePart | str] = [f"User request:\n{request}", "Renders BEFORE the change:"]
    for view, p in before.items():
        parts.append(ImagePart.from_file(p, label=f"BEFORE — view {view}"))
    parts.append("Renders AFTER the change:")
    for view, p in after.items():
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
