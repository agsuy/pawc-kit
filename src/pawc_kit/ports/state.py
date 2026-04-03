"""State store protocols and session bootstrap types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pawc_kit.contracts.state import SessionState


@dataclass(frozen=True)
class SessionMetadata:
    """Bootstrap data used to initialize a session."""

    session_id: str
    skill_name: str
    skill_version: str
    first_phase: str
    context_id: str | None = None


@dataclass(frozen=True)
class StoredSession:
    """Persisted session snapshot plus optimistic concurrency revision."""

    state: SessionState
    revision: str | int


@dataclass(frozen=True)
class SessionSummary:
    """Lightweight session envelope without iteration/review arrays."""

    session_id: str
    skill_name: str
    skill_version: str
    current_phase: str
    status: str
    started_at: str
    completed_at: str | None
    context_id: str | None
    feedback_loops: int
    revision: str | int


@runtime_checkable
class StateStore(Protocol):
    """Sync state persistence contract."""

    def load(self, session_id: str) -> StoredSession: ...

    def save(
        self,
        snapshot: SessionState,
        *,
        expected_revision: str | int,
    ) -> StoredSession: ...

    def initialize(self, session_metadata: SessionMetadata) -> StoredSession: ...

    def list(self) -> list[StoredSession]: ...

    def list_sessions(self) -> list[SessionSummary]: ...

    def delete(self, session_id: str) -> None: ...


@runtime_checkable
class AsyncStateStore(Protocol):
    """Async state persistence contract."""

    async def load(self, session_id: str) -> StoredSession: ...

    async def save(
        self,
        snapshot: SessionState,
        *,
        expected_revision: str | int,
    ) -> StoredSession: ...

    async def initialize(self, session_metadata: SessionMetadata) -> StoredSession: ...

    async def list(self) -> list[StoredSession]: ...

    async def list_sessions(self) -> list[SessionSummary]: ...

    async def delete(self, session_id: str) -> None: ...


__all__ = [
    "AsyncStateStore",
    "SessionMetadata",
    "SessionSummary",
    "StateStore",
    "StoredSession",
]
