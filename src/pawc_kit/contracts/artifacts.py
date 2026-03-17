"""Stable artifact contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class KeyArtifactRef(BaseModel):
    """Key artifact in handoff: type, ref, description."""

    type: str
    ref: str
    description: str


class HandoffContext(BaseModel):
    """Handoff context body."""

    summary: str
    key_artifacts: list[KeyArtifactRef] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    next_steps: list[str] | None = None


class HandoffArtifactMetadata(BaseModel):
    """Identity metadata for a persisted handoff artifact."""

    phase_id: str
    role_id: str


class HandoffArtifactPart(BaseModel):
    """Single part of A2A handoff artifact."""

    contentType: Literal["application/vnd.pawc.handoff-context+json"] = (
        "application/vnd.pawc.handoff-context+json"
    )
    body: HandoffContext


class HandoffArtifact(BaseModel):
    """A2A Artifact envelope: exactly one Part with handoff context."""

    metadata: HandoffArtifactMetadata
    parts: list[HandoffArtifactPart] = Field(..., min_length=1, max_length=1)


class FindingEntry(BaseModel):
    """Single finding in a decision payload."""

    severity: Literal["critical", "high", "medium", "low", "info"]
    category: str
    title: str
    details: str
    required_change: str | None = None
    recommended_change: str | None = None


class DecisionPayload(BaseModel):
    """Persisted decision artifact."""

    phase_id: str
    role_id: str
    decision: Literal["APPROVE", "REQUEST_CHANGES"]
    confidence_score: int = Field(..., ge=0, le=100)
    counts_verified: bool
    summary: str
    ended_at: str
    findings: list[FindingEntry] = Field(default_factory=list)
    target_phase: str | None = None

    @model_validator(mode="after")
    def _require_required_change(self) -> "DecisionPayload":
        if self.decision == "REQUEST_CHANGES":
            for index, finding in enumerate(self.findings):
                if not finding.required_change:
                    raise ValueError(
                        "findings["
                        f"{index}"
                        "] must have required_change when decision is REQUEST_CHANGES"
                    )
        return self


__all__ = [
    "DecisionPayload",
    "FindingEntry",
    "HandoffArtifact",
    "HandoffArtifactMetadata",
    "HandoffArtifactPart",
    "HandoffContext",
    "KeyArtifactRef",
]
