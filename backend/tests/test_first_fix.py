"""A first build always gets one fix pass on the critic's findings (CRITIC_FIRST_FIX): the score
near the threshold is noise, the findings are not."""

from __future__ import annotations

from housegen.agent.pipeline import _first_fix, _verdict_ends_rounds
from housegen.agent.schemas import Critique, CritiqueIssue
from housegen.core.config import Settings


def _verdict(score: int, issues: int, done: bool) -> Critique:
    return Critique(
        overall_score=score,
        summary="s",
        done=done,
        issues=[
            CritiqueIssue(severity="minor", view="south", description=f"d{i}", fix=f"f{i}")
            for i in range(issues)
        ],
    )


def test_first_review_with_findings_is_followed_by_a_fix_even_when_done() -> None:
    s = Settings(CRITIC_FIRST_FIX=True)
    good = _verdict(82, 6, True)
    assert _verdict_ends_rounds(good, 1, s, 2)  # the old gate would stop here
    assert _first_fix(1, good, s, 2)  # the new rule does not


def test_first_fix_needs_findings_a_round_left_and_the_setting() -> None:
    s = Settings(CRITIC_FIRST_FIX=True)
    assert not _first_fix(1, _verdict(95, 0, True), s, 2)  # nothing to fix
    assert not _first_fix(1, _verdict(82, 6, True), s, 1)  # one round = review only
    assert not _first_fix(2, _verdict(82, 6, True), s, 2)  # only the first review
    assert not _first_fix(1, _verdict(82, 6, True), Settings(CRITIC_FIRST_FIX=False), 2)
