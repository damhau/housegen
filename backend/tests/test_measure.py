"""The interior builder's measure tool (#68), the audit in its tool results and the plan gate (#67, #70)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from housegen.agent.pipeline import _plan_gate_text, plan_gaps
from housegen.agent.tools import MEASURE_SPEC, TOOL_SPECS, BuilderTools, format_measure
from housegen.agent.workspace import Workspace
from housegen.llm import anthropic_provider, openai_provider
from housegen.llm.anthropic_provider import _content_blocks
from housegen.llm.openai_provider import _to_input_items
from housegen.llm.types import Message, TextPart, ToolCallPart, ToolResultPart
from housegen.render.renderer import RenderResult

LAYOUT: dict[str, Any] = {
    "rooms": [
        {"name": "Chambre 1 — App. 1", "storey": 1, "use": "bedroom", "area": 12.4, "plan": 12.0},
        {"name": "SDB — App. 1", "storey": 2, "use": "bath", "area": 4.5, "plan": None},
    ],
    "pieces": [
        {
            "name": "bed-oak-linen",
            "type": "bed",
            "room": "Chambre 1 — App. 1",
            "storey": 1,
            "at": [1.2, -2.1],
            "w": 2.3,
            "d": 2.25,
            "h": 1.02,
            "rot": 90,
            "bottom": 0.0,
            "wall": 0.02,
        },
        {
            "name": "plant-small",
            "type": "plant",
            "room": "SDB — App. 1",
            "storey": 2,
            "at": [3.9, 0.2],
            "w": 0.2,
            "d": 0.2,
            "h": 0.27,
            "rot": 0,
            "bottom": 1.2,
            "wall": None,
        },
    ],
    "findings": [
        {
            "kind": "support",
            "room": "SDB — App. 1",
            "piece": "plant-small",
            "text": '"plant-small" floats 1.5 cm above "oak wall shelf"',
        },
        {
            "kind": "plan",
            "room": None,
            "piece": None,
            "text": "no area from the plan recorded for 1 room",
        },
    ],
}

AUDIT = [
    'room area off the plan: "Hall — App. 2" measures 4.39 m², the plan prints 5.25 m² (-16.4 %, -0.86 m²): redraw it',
    'support: "plant-small" in "SDB" floats 1.5 cm above "oak wall shelf"',
    'room incomplete: "WC" (wc): no hand basin',
]


class FakeRenderer:
    def __init__(
        self, layout: dict[str, Any] | None = LAYOUT, audit: list[str] | None = None
    ) -> None:
        self.layout, self.audit = layout, audit or []

    async def render(
        self,
        scene_url: str,
        views: list[str],
        out_dir: Path,
        quality: str = "high",
        camera: Any = None,
    ) -> RenderResult:
        return RenderResult(images={}, errors=[], audit=self.audit, report={"layout": self.layout})


def _tools(tmp_path: Path, renderer: FakeRenderer) -> BuilderTools:
    ws = Workspace(tmp_path)
    ws.write("src/scene.js", "export async function buildScene() {}\n")
    return BuilderTools(ws, renderer, "http://x/index.html", tmp_path / "renders")  # type: ignore[arg-type]


def test_measure_lists_the_pieces_and_the_findings_of_its_scope() -> None:
    house = format_measure(LAYOUT, None, None)
    assert house.startswith("measure — the whole house: 2 pieces, 2 findings")
    assert (
        "bed-oak-linen · bed · Chambre 1 — App. 1 · [1.20, -2.10] · 2.30 x 2.25 x 1.02 · 90° · 0.00 · 0.02"
        in house
    )
    assert '- support: "plant-small" floats 1.5 cm' in house
    room = format_measure(LAYOUT, "sdb", None)
    assert 'rooms matching "sdb": 1 pieces, 1 findings' in room
    assert "bed-oak-linen" not in room
    assert "plant-small · plant" in room
    first = format_measure(LAYOUT, None, 1)
    assert "storey 1: 1 pieces, 0 findings" in first
    assert "No findings" in first
    assert "older kit" in format_measure(None, None, None)


async def test_the_tool_measures_through_the_renderer(tmp_path: Path) -> None:
    tools = _tools(tmp_path, FakeRenderer())
    content, is_error = await tools.call("measure", {"room": "Chambre"})
    assert not is_error
    text = content[0].text  # type: ignore[union-attr]
    assert "bed-oak-linen" in text
    assert "plant-small" not in text


async def test_the_audit_comes_with_the_results_only_when_the_job_asks(tmp_path: Path) -> None:
    tools = _tools(tmp_path, FakeRenderer(audit=AUDIT))
    out, _ = await tools.call("check_scene", {})
    assert len(out) == 1
    tools.show_audit = True
    out, _ = await tools.call("check_scene", {})
    assert out[1].text.startswith("Scene audit")  # type: ignore[union-attr]
    assert "- room area off the plan" in out[1].text  # type: ignore[union-attr]
    out, _ = await tools.call("render_views", {"views": ["north"]})
    assert any(getattr(p, "text", "").startswith("Scene audit") for p in out)


def test_the_plan_gate_picks_the_gaps_and_lists_what_else_is_flagged() -> None:
    assert plan_gaps(AUDIT) == AUDIT[:1]
    text = _plan_gate_text(plan_gaps(AUDIT), AUDIT)
    assert '- "Hall — App. 2" measures 4.39 m²' in text
    assert '- support: "plant-small"' in text
    assert "room incomplete" not in text
    assert text.endswith("Then run check_scene and call finish.")


@pytest.mark.parametrize("provider", [anthropic_provider, openai_provider])
def test_both_providers_send_the_measure_tool(provider: Any) -> None:
    defs = provider._tool_defs([*TOOL_SPECS, MEASURE_SPEC])
    names = [d["name"] for d in defs]
    assert names[-1] == "measure"
    m = defs[-1]
    schema = m.get("input_schema") or m.get("parameters")
    assert schema["properties"].keys() == {"room", "storey"}
    if provider is openai_provider:
        assert m["type"] == "function"
    else:
        assert m["cache_control"] == {"type": "ephemeral"}


def test_a_measure_call_and_its_result_round_trip_in_both_wire_formats() -> None:
    call = Message.assistant(ToolCallPart(id="call_m", name="measure", input={"room": "SDB"}))
    result = Message(
        role="user",
        content=[
            ToolResultPart(tool_call_id="call_m", content=[TextPart(text="measure — rooms …")])
        ],
    )
    blocks = _content_blocks(call.content) + _content_blocks(result.content)
    assert blocks[0]["type"] == "tool_use"
    assert blocks[0]["name"] == "measure"
    assert blocks[0]["input"] == {"room": "SDB"}
    assert blocks[1]["type"] == "tool_result"
    assert blocks[1]["tool_use_id"] == "call_m"
    items = _to_input_items([Message.user("furnish it"), call, result])
    assert items[1]["type"] == "function_call"
    assert items[1]["name"] == "measure"
    assert '"room": "SDB"' in items[1]["arguments"]
    assert items[2]["type"] == "function_call_output"
    assert items[2]["output"].startswith("measure")
