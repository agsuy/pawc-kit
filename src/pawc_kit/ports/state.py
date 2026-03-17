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


__all__ = [
    "AsyncStateStore",
    "SessionMetadata",
    "StateStore",
    "StoredSession",
]
