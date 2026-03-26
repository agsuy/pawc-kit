"""Stable workflow event payloads."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard, get_args


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
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    model: str | None = None
    model_requested: str | None = None


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
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    model: str | None = None
    model_requested: str | None = None


@dataclass(frozen=True)
class HumanReviewPending:
    """A human review phase was reached; engine paused awaiting external input."""

    session_id: str
    phase_id: str
    role_id: str
    review: int
    revision: str | int
    occurred_at: str


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
    status: Literal["completed", "abandoned", "failed"]
    feedback_loops: int
    revision: str | int
    started_at: str
    completed_at: str
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0


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
    | HumanReviewPending
    | PhaseTransitioned
    | RunCompleted
    | RunFailed
)

_EVENT_TYPES: tuple[type, ...] = get_args(WorkflowEvent)
_EVENT_BY_NAME: dict[str, type] = {cls.__name__: cls for cls in _EVENT_TYPES}


def event_to_dict(event: WorkflowEvent) -> dict[str, Any]:
    """Serialize a workflow event to a JSON-friendly dict including ``event_type``."""
    d = dataclasses.asdict(event)
    d["event_type"] = type(event).__name__
    return d


def event_from_dict(data: dict[str, Any]) -> WorkflowEvent:
    """Deserialize a dict produced by :func:`event_to_dict` (or equivalent)."""
    raw = dict(data)
    event_type = raw.pop("event_type", None) or raw.pop("type", None)
    if not event_type or not isinstance(event_type, str):
        msg = "Missing or invalid event_type"
        raise ValueError(msg)
    cls = _EVENT_BY_NAME.get(event_type)
    if cls is None:
        msg = f"Unknown event type: {event_type}"
        raise ValueError(msg)
    return cls(**raw)


def is_workflow_event(obj: object) -> TypeGuard[WorkflowEvent]:
    """Return True if *obj* is a known :data:`WorkflowEvent` variant."""
    return isinstance(obj, _EVENT_TYPES)


def event_timestamp(event: WorkflowEvent) -> str:
    """Return the primary timestamp field for *event* (completed, ended, or occurred)."""
    for attr in ("completed_at", "ended_at", "occurred_at"):
        val = getattr(event, attr, None)
        if val is not None:
            return val  # type: ignore[no-any-return]
    msg = f"No timestamp field on {type(event).__name__}"
    raise ValueError(msg)


__all__ = [
    "HumanReviewPending",
    "IterationCommitted",
    "PhaseStarted",
    "PhaseTransitioned",
    "ReviewCommitted",
    "RunCompleted",
    "RunFailed",
    "RunResumed",
    "RunStarted",
    "WorkflowEvent",
    "event_from_dict",
    "event_timestamp",
    "event_to_dict",
    "is_workflow_event",
]
