"""Intake (plans only, no photographs): parsing, the builder's first message, the critic
pairing renders with elevation sheets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from housegen.agent import critic, intake
from housegen.agent.pipeline import BRIEF_HEADING, _first_message, _Inputs
from housegen.agent.prompts import PLAN_ONLY_ADDENDUM
from housegen.agent.schemas import Intake
from housegen.core.exceptions import LLMError
from housegen.llm.types import Completion, ImagePart, Message, TextPart, ToolSpec
from housegen.projects.schemas import standard_views

INTAKE_JSON: dict[str, Any] = {
    "summary": "Two storeys, 12 x 9 m, gable roof.",
    "sheets": [
        {"page": 1, "kind": "floor_plan", "label": "ground floor 1:100", "elevations": []},
        {
            "page": 2,
            "kind": "elevation",
            "label": "street and garden",
            "elevations": ["south", "north"],
        },
        {"page": 3, "kind": "elevation", "label": "sides", "elevations": ["east", "west", "north"]},
    ],
    "questions": [
        {"question": "Wall colour?", "why": "façade material", "suggested": "white render"},
    ],
}

CRITIQUE_JSON = {"overall_score": 70, "summary": "ok", "done": False, "issues": []}


class FakeProvider:
    name = "fake"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 16000,
        on_progress: Any = None,
        effort: str | None = None,
    ) -> Completion:
        self.calls.append({"system": system, "messages": messages, "schema": response_schema})
        return Completion(message=Message.assistant(self.reply), stop_reason="end_turn")


def _png(path: Path) -> Path:
    Image.new("RGB", (40, 30), (255, 255, 255)).save(path)
    return path


def _labels(msg: Message) -> list[str]:
    return [p.label or "" for p in msg.content if isinstance(p, ImagePart)]


def _texts(msg: Message) -> str:
    return "\n".join(p.text for p in msg.content if isinstance(p, TextPart))


def test_intake_parse_accepts_fenced_json_and_maps_elevations() -> None:
    parsed = intake._parse("```json\n" + json.dumps(INTAKE_JSON) + "\n```")
    assert parsed.elevation_pages() == {"south": 2, "north": 2, "east": 3, "west": 3}
    assert "Sheet 2: elevation (south, north)" in parsed.sheet_map()
    with pytest.raises(LLMError):
        intake._parse("not json")


async def test_read_plans_sends_every_sheet_and_the_notes(tmp_path: Path) -> None:
    pages = [_png(tmp_path / f"page-{i}.png") for i in (1, 2)]
    provider = FakeProvider(json.dumps(INTAKE_JSON))
    result, _ = await intake.read_plans(provider, "m", pages, "roof is dark grey", 1000)
    assert len(result.questions) == 1
    msg = provider.calls[0]["messages"][0]
    assert _labels(msg) == ["Sheet 1 of 2", "Sheet 2 of 2"]
    assert "roof is dark grey" in _texts(msg)
    assert provider.calls[0]["schema"] is not None


async def test_read_plans_refuses_an_empty_plan_set() -> None:
    with pytest.raises(LLMError):
        await intake.read_plans(FakeProvider("{}"), "m", [], "", 1000)


async def test_critic_pairs_each_elevation_render_with_its_sheet_once(tmp_path: Path) -> None:
    pages = [_png(tmp_path / f"page-{i}.png") for i in (1, 2, 3)]
    renders = {
        v: _png(tmp_path / f"{v}.png")
        for v in ("south-elevation", "north-elevation", "east", "aerial")
    }
    provider = FakeProvider(json.dumps(CRITIQUE_JSON))
    parsed = Intake.model_validate(INTAKE_JSON)
    verdict, _ = await critic.critique_against_plans(
        provider, "m", parsed.elevation_pages(), pages, renders, 80, 1000
    )
    assert verdict.overall_score == 70
    labels = _labels(provider.calls[0]["messages"][0])
    sheets = [x for x in labels if x.startswith("PLAN SHEET")]
    assert len(sheets) == 2  # sheet 2 and sheet 3, each once
    assert any("straight-on elevation view of the south" in x for x in labels)
    assert any("elevated wide camera on the east" in x for x in labels)  # fallback view
    assert not any("west" in x for x in labels if x.startswith("RENDER"))  # no render, skipped
    assert "elevation drawings" in provider.calls[0]["system"]


async def test_critic_needs_at_least_one_pair(tmp_path: Path) -> None:
    pages = [_png(tmp_path / "page-1.png")]
    with pytest.raises(LLMError):
        await critic.critique_against_plans(
            FakeProvider("{}"), "m", {"south": 1}, pages, {}, 80, 1000
        )


def test_first_message_without_photos_carries_the_plan_only_rules(tmp_path: Path) -> None:
    pages = [_png(tmp_path / "page-1.png")]
    parsed = Intake.model_validate(INTAKE_JSON)
    parts = _first_message(_Inputs({}, [], pages, "walls: white render", parsed))
    text = "\n".join(p for p in parts if isinstance(p, str))
    assert PLAN_ONLY_ADDENDUM in text
    assert "## Sheet map" in text
    assert f"{BRIEF_HEADING}\nwalls: white render" in text

    with_photos = _first_message(_Inputs({"north": pages[0]}, [], pages, "", None))
    text = "\n".join(p for p in with_photos if isinstance(p, str))
    assert PLAN_ONLY_ADDENDUM not in text
    assert BRIEF_HEADING not in text


def test_plan_only_versions_render_elevations_instead_of_photo_views() -> None:
    assert "south-elevation" in standard_views(False)
    assert "south-photo" not in standard_views(False)
    assert "south-photo" in standard_views(True)
