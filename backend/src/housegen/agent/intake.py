"""The intake step: read the plan set before building when there are no photographs.

One structured call over the sheets: what the house is as drawn, which sheet is what
(the elevation sheets become the critic's ground truth), and the few questions the
drawings cannot answer, each with a suggested default the owner can accept as is.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from housegen.agent.prompts import INTAKE_SYSTEM
from housegen.agent.schemas import Intake
from housegen.core.exceptions import LLMError
from housegen.llm import ImagePart, Message, ProgressCallback, Provider, Usage

logger = logging.getLogger(__name__)


async def read_plans(
    provider: Provider,
    model: str,
    pages: list[Path],
    notes: str,
    max_tokens: int,
    on_progress: ProgressCallback | None = None,
    effort: str | None = None,
) -> tuple[Intake, Usage]:
    if not pages:
        raise LLMError("the plan set has no sheets to read")
    parts: list[ImagePart | str] = [
        "Read this plan set. There are no photographs of the house.",
    ]
    for i, p in enumerate(pages, 1):
        parts.append(ImagePart.from_file(p, label=f"Sheet {i} of {len(pages)}"))
    if notes.strip():
        parts.append("The owner wrote these notes; do not ask what they already answer:\n" + notes)
    parts.append("Return the intake as JSON matching the schema.")
    completion = await provider.complete(
        model=model,
        system=INTAKE_SYSTEM,
        messages=[Message.user(*parts)],
        response_schema=Intake.model_json_schema(),
        max_tokens=max_tokens,
        on_progress=on_progress,
        effort=effort,
    )
    return _parse(completion.message.text), completion.usage


def _parse(text: str) -> Intake:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        intake = Intake.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        logger.exception("intake.parse_failed")
        raise LLMError(f"intake returned invalid JSON: {e}") from e
    logger.info(
        "intake.done",
        extra={
            "sheets": len(intake.sheets),
            "elevations": sorted(intake.elevation_pages()),
            "questions": len(intake.questions),
        },
    )
    return intake
