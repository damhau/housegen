"""Structured outputs exchanged between agents."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CritiqueIssue(BaseModel):
    view: str
    severity: Literal["major", "minor"]
    # fidelity: the model differs from the reference; plausibility: the scene is physically
    # impossible (objects intersecting, floating, impossible scale), whatever the reference says
    kind: Literal["fidelity", "plausibility"] = "fidelity"
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
            tag = (
                f"{issue.severity}, {issue.kind}"
                if issue.kind == "plausibility"
                else issue.severity
            )
            lines.append(
                f"{i}. [{tag}] view={issue.view}: {issue.description}\n   Fix: {issue.fix}"
            )
        return "\n".join(lines)


# ---- intake: what the plan set says, and what it cannot say (asked to the owner) ----

SheetKind = Literal[
    "floor_plan", "elevation", "section", "site_plan", "roof_plan", "detail", "other"
]
Facade = Literal["north", "south", "east", "west"]


class SheetInfo(BaseModel):
    page: int = Field(ge=1, description="1-based sheet number, in upload order")
    kind: SheetKind
    label: str = Field(description="short, e.g. 'ground floor plan 1:100', 'south elevation'")
    elevations: list[Facade] = Field(
        default_factory=list,
        description="for elevation sheets: the façades drawn on it (by compass side)",
    )


class IntakeQuestion(BaseModel):
    question: str
    why: str = Field(description="one line: what in the model depends on the answer")
    suggested: str = Field(description="your best-guess answer, usable as is")


class Intake(BaseModel):
    summary: str = Field(description="the house as drawn: footprint, storeys, roof, façades, site")
    sheets: list[SheetInfo]
    questions: list[IntakeQuestion] = Field(default_factory=list, max_length=6)

    def elevation_pages(self) -> dict[str, int]:
        """Façade side → the first sheet that draws its elevation."""
        out: dict[str, int] = {}
        for s in self.sheets:
            for side in s.elevations:
                out.setdefault(side, s.page)
        return out

    def sheet_map(self) -> str:
        lines = []
        for s in self.sheets:
            extra = f" ({', '.join(s.elevations)})" if s.elevations else ""
            lines.append(f"- Sheet {s.page}: {s.kind.replace('_', ' ')}{extra}: {s.label}")
        return "\n".join(lines)

    def as_builder_text(self) -> str:
        parts = [f"## What the plan set shows\n{self.summary}"]
        if self.sheets:
            parts.append("## Sheet map\n" + self.sheet_map())
        return "\n\n".join(parts)
