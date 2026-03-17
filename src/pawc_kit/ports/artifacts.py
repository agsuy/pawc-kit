"""Artifact reader and writer protocols."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pawc_kit.contracts.artifacts import DecisionPayload, HandoffContext
from pawc_kit.contracts.state import ArtifactRef


@runtime_checkable
class ArtifactReader(Protocol):
    """Read-only artifact access exposed to workflow roles."""

    def load_artifact(self, ref: ArtifactRef | str) -> bytes: ...


@runtime_checkable
class AsyncArtifactReader(Protocol):
    """Async read-only artifact access exposed to workflow roles."""

    async def load_artifact(self, ref: ArtifactRef | str) -> bytes: ...


@runtime_checkable
class ArtifactStore(ArtifactReader, Protocol):
    """Sync artifact persistence contract."""

    def save_handoff(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        handoff: HandoffContext,
    ) -> ArtifactRef: ...

    def save_decision(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        payload: DecisionPayload,
    ) -> ArtifactRef: ...


@runtime_checkable
class AsyncArtifactStore(AsyncArtifactReader, Protocol):
    """Async artifact persistence contract."""

    async def save_handoff(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        handoff: HandoffContext,
    ) -> ArtifactRef: ...

    async def save_decision(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        payload: DecisionPayload,
    ) -> ArtifactRef: ...


__all__ = [
    "ArtifactReader",
    "ArtifactStore",
    "AsyncArtifactReader",
    "AsyncArtifactStore",
]
