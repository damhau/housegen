"""Structured outputs exchanged between agents."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CritiqueIssue(BaseModel):
    view: str
    severity: Literal["major", "minor"]
    description: str
    fix: str


class Critique(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    summary: str
    done: bool
    issues: list[CritiqueIssue]

    def as_builder_feedback(self) -> str:
        lines = [f"Critic score: {self.overall_score}/100. {self.summary}", ""]
        if not self.issues:
            lines.append("No issues listed.")
        for i, issue in enumerate(self.issues, 1):
            lines.append(
                f"{i}. [{issue.severity}] view={issue.view}: {issue.description}\n   Fix: {issue.fix}"
            )
        return "\n".join(lines)
