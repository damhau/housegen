"""The picture editor (#92): which of a room's viewpoints the set keeps, from their previews; which exterior views
lead the set (#93)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from housegen.agent.prompts import LISTING_EXTERIOR_PICK_SYSTEM, LISTING_PICK_SYSTEM
from housegen.core.exceptions import LLMError
from housegen.llm import Completion, ImagePart, Message, Provider

logger = logging.getLogger(__name__)


class ViewChoice(BaseModel):
    best: int = Field(description="the chosen viewpoint's number, as labelled")
    remove: str | None = Field(
        None, description="the id of one piece to take out of the chosen view, or null"
    )
    problem: str = Field(description="the chosen view's main remaining weakness, one sentence")
    why: str = Field(description="why it beats the other viewpoints, one sentence")


def pieces_text(pieces: list[dict[str, Any]]) -> str:
    """The pieces a frame shows, one per line: id | name | share | extent | cut by | distance."""
    lines = [
        f"  {p['id']} | {p.get('name', '')} | {float(p.get('share', 0)) * 100:.1f} % of the frame | "
        f"x {p['x'][0]:.2f}-{p['x'][1]:.2f}, y {p['y'][0]:.2f}-{p['y'][1]:.2f} | "
        f"cut by: {', '.join(p.get('cut_by') or []) or 'none'} | {p.get('distance_m', '?')} m from the lens"
        for p in pieces
    ]
    return "\n".join(lines) or "  (none)"


async def pick_view(
    provider: Provider,
    model: str,
    room: str,
    use: str,
    previews: list[tuple[Path, list[dict[str, Any]]]],
    max_tokens: int,
    effort: str | None = None,
) -> tuple[ViewChoice, Completion]:
    """The best of `previews` (each a picture and the pieces its frame shows), numbered from 1."""
    if not previews:
        raise LLMError("no preview to choose from")
    parts: list[ImagePart | str] = [f"Room: {room} ({use}). {len(previews)} viewpoints."]
    for i, (path, pieces) in enumerate(previews, 1):
        parts.append(ImagePart.from_file(path, label=f"VIEWPOINT {i}"))
        parts.append(
            f"Pieces in viewpoint {i}'s frame (fractions of the frame from the top left):\n{pieces_text(pieces)}"
        )
    parts.append("Return the choice as JSON matching the schema.")
    completion = await provider.complete(
        model=model,
        system=LISTING_PICK_SYSTEM,
        messages=[Message.user(*parts)],
        response_schema=ViewChoice.model_json_schema(),
        max_tokens=max_tokens,
        effort=effort,
    )
    choice = parse_choice(completion.message.text, len(previews), previews)
    return choice, completion


def parse_choice(
    text: str, n: int, previews: list[tuple[Path, list[dict[str, Any]]]]
) -> ViewChoice:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        c = ViewChoice.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        raise LLMError(f"the picture editor returned invalid JSON: {e}") from e
    if not 1 <= c.best <= n:
        raise LLMError(f"the picture editor chose viewpoint {c.best} of {n}")
    if c.remove is not None and c.remove not in {p["id"] for p in previews[c.best - 1][1]}:
        logger.info("listing.pick.unknown_piece", extra={"piece": c.remove})
        c.remove = None
    return c


class ExteriorChoice(BaseModel):
    keep: list[int] = Field(description="the kept viewpoints' numbers, the cover photo first")
    why: str = Field(description="why these views in this order, one sentence")


async def pick_exteriors(
    provider: Provider,
    model: str,
    previews: list[tuple[Path, str]],
    least: int,
    most: int,
    max_tokens: int,
    effort: str | None = None,
) -> tuple[ExteriorChoice, Completion]:
    """Which of `previews` (each a picture and the side of the building it shows) lead the set, in order."""
    if not previews:
        raise LLMError("no preview to choose from")
    parts: list[ImagePart | str] = [f"{len(previews)} viewpoints around the building."]
    for i, (path, side) in enumerate(previews, 1):
        parts.append(ImagePart.from_file(path, label=f"VIEWPOINT {i}"))
        parts.append(f"Viewpoint {i} shows the {side} side.")
    parts.append("Return the choice as JSON matching the schema.")
    completion = await provider.complete(
        model=model,
        system=LISTING_EXTERIOR_PICK_SYSTEM.format(least=least, most=most),
        messages=[Message.user(*parts)],
        response_schema=ExteriorChoice.model_json_schema(),
        max_tokens=max_tokens,
        effort=effort,
    )
    return parse_exteriors(completion.message.text, len(previews), least, most), completion


def parse_exteriors(text: str, n: int, least: int, most: int) -> ExteriorChoice:
    """The editor's views, valid and distinct, at most `most`; padded in score order up to `least`."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        c = ExteriorChoice.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        raise LLMError(f"the picture editor returned invalid JSON: {e}") from e
    keep: list[int] = []
    for i in c.keep:
        if 1 <= i <= n and i not in keep:
            keep.append(i)
    if not keep:
        raise LLMError(f"the picture editor kept none of the {n} exterior views")
    for i in range(1, n + 1):
        if len(keep) >= min(least, n):
            break
        if i not in keep:
            keep.append(i)
    c.keep = keep[:most]
    return c
