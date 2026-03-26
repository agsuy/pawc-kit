"""Runtime backend protocol: resolves persistence stores for a session run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pawc_kit.ports.artifacts import (
    ArtifactReader,
    ArtifactStore,
    ArtifactWriter,
    AsyncArtifactReader,
    AsyncArtifactStore,
    AsyncArtifactWriter,
)
from pawc_kit.ports.state import AsyncStateStore, StateStore


@dataclass(frozen=True)
class ResolvedBackend:
    """Sync stores resolved for a single session run.

    ``artifact_store`` is always required for backward compatibility.
    When ``artifact_reader`` and ``artifact_writer`` are both set the
    engine uses them instead, enabling split read/write backends
    (e.g. CDN reader with DB writer).
    """

    state_store: StateStore
    artifact_store: ArtifactStore
    artifact_reader: ArtifactReader | None = None
    artifact_writer: ArtifactWriter | None = None

    def __post_init__(self) -> None:
        has_reader = self.artifact_reader is not None
        has_writer = self.artifact_writer is not None
        if has_reader != has_writer:
            raise ValueError("artifact_reader and artifact_writer must both be set or both be None")


@dataclass(frozen=True)
class AsyncResolvedBackend:
    """Async stores resolved for a single session run.

    Same split semantics as :class:`ResolvedBackend`.
    """

    state_store: AsyncStateStore
    artifact_store: AsyncArtifactStore
    artifact_reader: AsyncArtifactReader | None = None
    artifact_writer: AsyncArtifactWriter | None = None

    def __post_init__(self) -> None:
        has_reader = self.artifact_reader is not None
        has_writer = self.artifact_writer is not None
        if has_reader != has_writer:
            raise ValueError("artifact_reader and artifact_writer must both be set or both be None")


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
