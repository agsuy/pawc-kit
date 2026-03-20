"""Runtime backend protocol: resolves persistence stores for a session run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pawc_kit.ports.artifacts import ArtifactStore, AsyncArtifactStore
from pawc_kit.ports.state import AsyncStateStore, StateStore


@dataclass(frozen=True)
class ResolvedBackend:
    """Sync stores resolved for a single session run."""

    state_store: StateStore
    artifact_store: ArtifactStore


@dataclass(frozen=True)
class AsyncResolvedBackend:
    """Async stores resolved for a single session run."""

    state_store: AsyncStateStore
    artifact_store: AsyncArtifactStore


@runtime_checkable
class RuntimeBackend(Protocol):
    """Sync backend that resolves persistence stores for a session.

    All backend-specific configuration (directory paths, DB connection pools,
    etc.) belongs in the implementing class's constructor.  The protocol itself
    takes only ``session_id`` so that server-side backends never depend on
    ``RootConfig`` or any kit-internal config schema.
    """

    def resolve(self, *, session_id: str) -> ResolvedBackend: ...


@runtime_checkable
class AsyncRuntimeBackend(Protocol):
    """Async backend that resolves persistence stores for a session.

    Same design contract as :class:`RuntimeBackend` but for async runtimes.
    """

    async def resolve(self, *, session_id: str) -> AsyncResolvedBackend: ...


__all__ = [
    "AsyncResolvedBackend",
    "AsyncRuntimeBackend",
    "ResolvedBackend",
    "RuntimeBackend",
]
