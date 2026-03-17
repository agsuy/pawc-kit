"""Stable workflow event payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RunStarted:
    """Run entered active execution and was durably persisted."""

    session_id: str
    skill_name: str
    phase_id: str
    role_id: str
    revision: str | int
    occurred_at: str


@dataclass(frozen=True)
class PhaseStarted:
    """A phase became the active current phase and was durably persisted."""

    session_id: str
    phase_id: str
    role_id: str
    phase_kind: Literal["executor", "review"]
    revision: str | int
    occurred_at: str


@dataclass(frozen=True)
class IterationCommitted:
    """An executor iteration was durably committed."""

    session_id: str
    phase_id: str
    role_id: str
    iteration: int
    confidence_score: int
    feedback_loops: int
    revision: str | int
    started_at: str
    ended_at: str
    chosen_next: str | None
    handoff_context_ref: str | None


@dataclass(frozen=True)
class ReviewCommitted:
    """A review result was durably committed."""

    session_id: str
    phase_id: str
    role_id: str
    review: int
    decision: Literal["APPROVE", "REQUEST_CHANGES"]
    confidence_score: int
    feedback_loops: int
    revision: str | int
    started_at: str
    ended_at: str
    target_phase: str | None
    chosen_next: str | None
    findings_ref: str | None


@dataclass(frozen=True)
class RunResumed:
    """A run was picked up from a previously persisted in_progress state.

    No new phase change occurred; this signals re-entry into active execution
    so that observers can distinguish a resume from an initial phase activation.
    The revision reflects the last durable commit already in the store.
    """

    session_id: str
    phase_id: str
    role_id: str
    phase_kind: Literal["executor", "review"]
    revision: str | int
    occurred_at: str


@dataclass(frozen=True)
class PhaseTransitioned:
    """Workflow moved from one phase to another and persisted the change."""

    session_id: str
    from_phase_id: str
    from_role_id: str
    to_phase_id: str
    to_role_id: str
    revision: str | int
    occurred_at: str


@dataclass(frozen=True)
class RunCompleted:
    """Run reached a terminal state and was durably persisted."""

    session_id: str
    status: Literal["completed", "abandoned"]
    feedback_loops: int
    revision: str | int
    started_at: str
    completed_at: str


@dataclass(frozen=True)
class RunFailed:
    """Run failed with an exception after the last durable commit."""

    session_id: str
    phase_id: str | None
    role_id: str | None
    revision: str | int
    occurred_at: str
    error_type: str
    error_message: str


WorkflowEvent = (
    RunStarted
    | RunResumed
    | PhaseStarted
    | IterationCommitted
    | ReviewCommitted
    | PhaseTransitioned
    | RunCompleted
    | RunFailed
)

__all__ = [
    "IterationCommitted",
    "PhaseStarted",
    "PhaseTransitioned",
    "ReviewCommitted",
    "RunCompleted",
    "RunFailed",
    "RunResumed",
    "RunStarted",
    "WorkflowEvent",
]
