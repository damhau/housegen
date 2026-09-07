"""`finish` extras (suggestions/questions) and the step-budget pacing notes."""

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
