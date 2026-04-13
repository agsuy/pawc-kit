"""Serializable execution DTOs for executor and reviewer invocations.

These types form the transport-safe boundary between the workflow engine and
role implementations.  All fields are plain data (Pydantic models, dataclasses,
or primitive types) with no live runtime references such as file handles or
store connections.

``ContextPayload`` replaces ``ContextPack`` in role-facing contracts,
stripping the filesystem ``Path`` field while preserving all data roles actually
need.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from pawc_kit.contracts.artifacts import HandoffContext
from pawc_kit.contracts.state import SessionState

if TYPE_CHECKING:
    from pawc_kit.workflow.graph import PhaseDefinition
    from pawc_kit.workflow.roles import WorkflowHistoryView


@dataclass(frozen=True)
class ContextPayload:
    """Serializable context data passed to roles.

    Replaces ``ContextPack`` in role-facing contracts.  Carries the same
    data that roles and prompt assemblers actually use — ``request_files``,
    ``discovery_handoff``, and ``children`` — without the filesystem ``Path``
    that makes ``ContextPack`` non-serializable.

    ``context_id`` comes from ``ContextPack.metadata.context_id`` and is
    required for child attribution in prompt sections and for
    ``accessible_packs()`` scoping (which the engine applies on the original
    ``ContextPack`` before converting to ``ContextPayload``).
    """

    context_id: str
    request_files: dict[str, str]
    discovery_handoff: HandoffContext | None = None
    discovery_files: dict[str, str] = field(default_factory=dict)
    children: list[ContextPayload] = field(default_factory=list)

    @classmethod
    def empty(cls) -> ContextPayload:
        """Return an empty payload with no context data."""
        return cls(context_id="", request_files={})


@dataclass(frozen=True)
class ExecutionRequest:
    """Serializable input to an executor role invocation.

    All fields are transport-safe: no live artifact readers, no filesystem paths.
    """

    session: SessionState
    phase: PhaseDefinition
    history: WorkflowHistoryView
    context: ContextPayload
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ReviewRequest:
    """Serializable input to a reviewer role invocation.

    All fields are transport-safe: no live artifact readers, no filesystem paths.
    """

    session: SessionState
    phase: PhaseDefinition
    history: WorkflowHistoryView
    context: ContextPayload
    metadata: Mapping[str, Any] | None = None
    approval_targets: list[str] = field(default_factory=list)
    request_change_targets: list[str] = field(default_factory=list)


__all__ = [
    "ContextPayload",
    "ExecutionRequest",
    "ReviewRequest",
]
