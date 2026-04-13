"""Discovery-specific contracts: Q&A models, question signals, and config."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from pawc_kit.contracts.config import RoutingRuleConfig


class QuestionEntry(BaseModel):
    """Single Q&A entry stored in ``internal/q-and-a.json``."""

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
    request_changes_routing: list[RoutingRuleConfig] = Field(default_factory=list)
    tool_capabilities: list[str] | None = None
    tool_services: list[str] | None = None
    tool_overrides: dict[str, str] | None = None


class DiscoveryConfig(BaseModel):
    """Top-level discovery workflow configuration.

    Maps 1:1 to the spec's ``discovery-config.yaml`` format.  Use
    :meth:`engine_kwargs` to extract the engine-level fields as a dict
    ready for unpacking into :class:`~pawc_kit.workflow.WorkflowEngine` or
    :class:`~pawc_kit.workflow.AsyncWorkflowEngine`.

    Note: discovery defaults differ intentionally from engine defaults --
    ``confidence_threshold=80`` (vs 85), ``max_iterations=3`` (vs 10),
    ``adhoc_questions=True`` (vs False) -- to reflect tighter discovery
    workflow constraints.
    """

    phases: list[DiscoveryPhaseConfig]
    adhoc_questions: bool = True
    require_human_approval: bool = True
    confidence_threshold: int = Field(80, ge=0, le=100)
    max_iterations: int = Field(3, ge=1)
    max_feedback_rounds: int = Field(3, ge=0)
    confidence_floor: int | None = None
    finding_categories: list[str] | None = None
    require_context_summary_doc: bool = True
    artifact_backfill_retries: int = Field(1, ge=0)
    run_directory: str | None = None
    state_filename: str | None = None

    def engine_kwargs(self) -> dict[str, Any]:
        """Engine constructor kwargs extracted from this discovery config.

        Returns a dict suitable for unpacking into either engine constructor::

            engine = WorkflowEngine(graph, state_store, artifact_store,
                                    **config.engine_kwargs())
            engine = AsyncWorkflowEngine(graph, state_store, artifact_store,
                                         **config.engine_kwargs())

        ``confidence_floor`` is omitted when ``None`` so the engine uses its
        own default.  ``finding_categories`` and ``require_context_summary_doc``
        are intentionally excluded -- they are consumer-side concerns (prompt
        assembly and post-run checks), not engine constructor params.
        """
        d: dict[str, Any] = {
            "confidence_threshold": self.confidence_threshold,
            "max_iterations": self.max_iterations,
            "max_feedback_rounds": self.max_feedback_rounds,
            "adhoc_questions": self.adhoc_questions,
            "artifact_backfill_retries": self.artifact_backfill_retries,
        }
        if self.confidence_floor is not None:
            d["confidence_floor"] = self.confidence_floor
        return d


__all__ = [
    "DiscoveryConfig",
    "DiscoveryPhaseConfig",
    "QuestionEntry",
    "QuestionRequest",
]
