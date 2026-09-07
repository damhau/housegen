"""`finish` extras (suggestions/questions) and the step-budget pacing notes."""

import pytest

from housegen.agent.builder import _str_list, budget_note


def test_budget_note_at_half_three_quarters_and_end() -> None:
    assert budget_note(1, 60) is None
    assert "half" in (budget_note(30, 60) or "")
    assert "three quarters" in (budget_note(45, 60) or "")
    assert budget_note(55, 60) is not None
    assert budget_note(59, 60) is not None
    assert budget_note(60, 60) is None
    assert budget_note(40, 60) is None


def test_str_list_trims_and_caps() -> None:
    assert _str_list(["a", "  ", 3, " b "], 8) == ["a", "3", "b"]
    assert _str_list(list("abcdefghij"), 8) == list("abcdefgh")
    assert _str_list("not a list", 8) == []
    assert _str_list(None, 8) == []


def test_run_summary_totals_and_cost(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Turns add up by phase and activity; cost follows the price table (#13)."""
    from housegen.agent.metrics import RunMetrics, ToolCallMetric, TurnMetric
    from housegen.core.config import Settings

    s = Settings(MODEL_PRICES={"m": {"input": 10.0, "cached": 1.0, "output": 100.0}})
    m = RunMetrics(s)
    m.add(
        TurnMetric(
            step=1,
            role="builder",
            model="m",
            duration_ms=10_000,
            thinking_ms=6_000,
            tools_ms=2_000,
            render_ms=1_500,
            input_tokens=100_000,
            cached_tokens=0,
            output_tokens=1_000,
            tool_calls=[ToolCallMetric(name="write_file"), ToolCallMetric(name="render_views")],
        )
    )
    m.add(
        TurnMetric(
            step=2,
            role="builder",
            model="m",
            duration_ms=5_000,
            thinking_ms=1_000,
            input_tokens=150_000,
            cached_tokens=10_000,  # lost the cache
            output_tokens=500,
            tool_calls=[ToolCallMetric(name="edit_file")],
        )
    )
    m.add(
        TurnMetric(
            step=1,
            role="critic",
            model="m",
            duration_ms=3_000,
            input_tokens=50_000,
            output_tokens=200,
        )
    )
    out = m.summary()
    assert out.turns == 3
    assert out.builder.turns == 2
    assert out.builder.llm_ms == 15_000
    assert out.builder.thinking_ms == 7_000
    assert out.builder.writing_ms == 8_000
    assert out.builder.render_ms == 1_500
    assert out.builder.renders == 1
    assert out.builder.cache_miss_turns == 1
    assert out.critic.turns == 1
    assert out.critic.llm_ms == 3_000
    assert out.edits == 2
    assert out.edits_per_turn_avg == 1.0
    assert out.single_edit_turns == 1
    # 100k in @10 + 1k out @100 = 1.1; 140k in @10 + 10k cached @1 + 0.5k out @100 = 1.46; 50k @10 + 0.2k @100 = 0.52
    assert out.cost_usd == pytest.approx(3.08)
    assert out.builder.cost_usd == pytest.approx(2.56)
    # an unpriced model → no cost at all
    m2 = RunMetrics(Settings(MODEL_PRICES={}))
    m2.add(TurnMetric(step=1, role="builder", model="x", input_tokens=10))
    assert m2.summary().cost_usd is None
    assert Settings().price_for("claude-opus-5-20991231") is not None  # prefix match
