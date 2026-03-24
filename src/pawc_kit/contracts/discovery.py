"""Discovery-specific contracts: Q&A models, question signals, and config."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from pawc_kit.contracts.config import RoutingRuleConfig


class QuestionEntry(BaseModel):
    """Single Q&A entry stored in ``discovery/q-and-a.json``."""

    question_id: str
    question: str
    phase_id: str
    asked_by: str
    asked_at: str
    answer: str | None = None
    answered_at: str | None = None
    skipped: bool = False


class QuestionRequest(BaseModel):
    """Structured signal returned by an executor to pause for a clarifying question.

    When an executor includes a ``QuestionRequest`` in its result, the engine
    writes the question to the context pack and pauses for external input.
    """

    question_id: str
    question: str


# ------------------------------------------------------------------
# Discovery config models
# ------------------------------------------------------------------


class DiscoveryPhaseConfig(BaseModel):
    """Per-phase configuration in a discovery workflow."""

    phase: str
    on_complete: list[str] | str = Field(default_factory=list)
    on_approve: list[str] | str = Field(default_factory=list)
    can_request_changes_from: list[str] | str = Field(default_factory=list)
    max_rounds: int | None = None
    max_questions: int | None = None
    human: bool = False
    role_overrides: dict[str, Any] | None = None
    routing: list[RoutingRuleConfig] = Field(default_factory=list)


class DiscoveryConfig(BaseModel):
    """Top-level discovery workflow configuration.

    Maps 1:1 to the spec's ``discovery-config.yaml`` format.  Engine-level
    fields (``confidence_threshold``, ``max_iterations``, ``confidence_floor``)
    are forwarded to :class:`AsyncWorkflowEngine` as constructor kwargs.
    """

    phases: list[DiscoveryPhaseConfig]
    adhoc_questions: bool = True
    require_human_approval: bool = True
    confidence_threshold: int = Field(80, ge=0, le=100)
    max_iterations: int = Field(3, ge=1)
    confidence_floor: int | None = None
    finding_categories: list[str] | None = None
    require_context_summary_doc: bool = True
    run_directory: str | None = None
    state_filename: str | None = None


__all__ = [
    "DiscoveryConfig",
    "DiscoveryPhaseConfig",
    "QuestionEntry",
    "QuestionRequest",
]
