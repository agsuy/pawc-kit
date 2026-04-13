"""Stable workflow role contexts, results, and protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pawc_kit.contracts.artifacts import DecisionPayload, FileArtifact, FindingEntry, HandoffContext
from pawc_kit.contracts.discovery import QuestionRequest
from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
from pawc_kit.contracts.state import ArtifactRef, IterationEntry, ReviewEntry

if TYPE_CHECKING:
    from pawc_kit.llm.backend import TokenUsage
    from pawc_kit.llm.section_recovery import RecoveryMetadata


@dataclass(frozen=True)
class WorkflowHistoryView:
    """Read-only view of prior workflow iterations and reviews."""

    iterations: list[IterationEntry]
    reviews: list[ReviewEntry]
    previous_decision: DecisionPayload | None = None


@dataclass(frozen=True)
class ReviewDecision:
    """Structured reviewer decision returned before persistence enrichment.

    When ``gate_override_reason`` is set, the decision was overridden from
    APPROVE to REQUEST_CHANGES by the quality-gate enforcement layer because
    the model's findings violated the configured thresholds.  This field is
    ``None`` for all normal (model-decided) outcomes.
    """

    decision: Literal["APPROVE", "REQUEST_CHANGES"]
    confidence_score: int
    counts_verified: bool
    summary: str
    findings: list[FindingEntry] = field(default_factory=list)
    target_phase: str | None = None
    gate_override_reason: str | None = None


@dataclass(frozen=True)
class ExecutionResult:
    """Structured executor output consumed by the workflow engine."""

    role_id: str
    ended_at: str
    confidence_score: int
    summary: str
    artifacts: list[ArtifactRef] = field(default_factory=list)
    files: list[FileArtifact] = field(default_factory=list)
    handoff: HandoffContext | None = None
    chosen_next: str | None = None
    pending_question: QuestionRequest | None = None
    usage: TokenUsage | None = None
    recovery: RecoveryMetadata | None = None


@dataclass(frozen=True)
class ReviewResult:
    """Structured reviewer output consumed by the workflow engine."""

    role_id: str
    ended_at: str
    decision: ReviewDecision
    chosen_next: str | None = None
    usage: TokenUsage | None = None
    recovery: RecoveryMetadata | None = None


@runtime_checkable
class Executor(Protocol):
    """Sync executor role."""

    def execute(self, req: ExecutionRequest) -> ExecutionResult: ...


@runtime_checkable
class Reviewer(Protocol):
    """Sync reviewer role."""

    def review(self, req: ReviewRequest) -> ReviewResult: ...


@runtime_checkable
class AsyncExecutor(Protocol):
    """Async executor role."""

    async def execute(self, req: ExecutionRequest) -> ExecutionResult: ...


@runtime_checkable
class AsyncReviewer(Protocol):
    """Async reviewer role."""

    async def review(self, req: ReviewRequest) -> ReviewResult: ...


__all__ = [
    "AsyncExecutor",
    "AsyncReviewer",
    "ExecutionRequest",
    "ExecutionResult",
    "Executor",
    "ReviewDecision",
    "ReviewRequest",
    "ReviewResult",
    "Reviewer",
    "WorkflowHistoryView",
]
