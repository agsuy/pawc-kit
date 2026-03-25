"""Stable state contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from pawc_kit._versioning import NoVersionId, SemVerStr


class ArtifactRef(BaseModel):
    """Artifact reference: type, ref (relative path), description."""

    type: str
    ref: str
    description: str


class DataCommandEntry(BaseModel):
    """Optional data-command audit entry."""

    at: str | None = None
    command: str | None = None
    kind: str | None = None


class IterationEntry(BaseModel):
    """Per-phase iteration entry. ``iteration`` is 1-based within the phase."""

    iteration: int = Field(..., ge=1)
    phase_id: str
    role_id: str
    confidence_score: int = Field(..., ge=0, le=100)
    ended_at: str
    summary: str
    started_at: str | None = None
    artifacts: list[ArtifactRef] | None = None
    agent_id: NoVersionId = None
    agent_version: str | None = None
    model_id: NoVersionId = None
    model_version: str | None = None
    handoff_context_ref: str | None = None


class ReviewEntry(BaseModel):
    """Review entry in session state."""

    review: int = Field(..., ge=1)
    phase_id: str
    role_id: str
    decision: Literal["APPROVE", "REQUEST_CHANGES", "PENDING"] | None = None
    target_phase: str | None = None
    confidence_score: int | None = Field(None, ge=0, le=100)
    ended_at: str | None = None
    summary: str | None = None
    findings_ref: str | None = None
    agent_id: NoVersionId = None
    agent_version: str | None = None
    model_id: NoVersionId = None
    model_version: str | None = None
    counts_verified: bool | None = None


class SessionState(BaseModel):
    """PAWC session state (shared schema for runner and discovery)."""

    session_id: str
    context_id: str | None = None
    skill_name: str
    skill_version: SemVerStr
    started_at: str
    current_phase: str
    phase_iterations: list[IterationEntry] = Field(default_factory=list)
    reviews: list[ReviewEntry] = Field(default_factory=list)
    feedback_loops: int = Field(0, ge=0)
    data_commands: list[DataCommandEntry] | None = None
    status: Literal["initialized", "in_progress", "completed", "abandoned", "failed"] = "initialized"
    completed_at: str | None = None
    metrics: dict | None = None
    run_metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _completed_at_invariant(self) -> "SessionState":
        if self.status in ("initialized", "in_progress"):
            if self.completed_at is not None:
                raise ValueError(f"completed_at must be null when status is {self.status!r}")
        elif self.status in ("completed", "abandoned", "failed") and self.completed_at is None:
            raise ValueError(f"completed_at must be set when status is {self.status!r}")
        return self


__all__ = [
    "ArtifactRef",
    "DataCommandEntry",
    "IterationEntry",
    "ReviewEntry",
    "SessionState",
]
