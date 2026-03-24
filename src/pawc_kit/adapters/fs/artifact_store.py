"""Filesystem-backed artifact store implementations."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pawc_kit.adapters.fs._io import atomic_write
from pawc_kit.contracts.artifacts import (
    DecisionPayload,
    HandoffArtifact,
    HandoffArtifactMetadata,
    HandoffArtifactPart,
    HandoffContext,
)
from pawc_kit.contracts.errors import StateNotFoundError
from pawc_kit.contracts.state import ArtifactRef


class FsArtifactStore:
    """Persist PAWC decision and handoff artifacts under a run directory."""

    def __init__(self, run_dir: str | Path) -> None:
        self._run_dir = Path(run_dir)

    def save_handoff(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        handoff: HandoffContext,
    ) -> ArtifactRef:
        del session_id
        filename = f"{phase_id}-{sequence}.json"
        rel_path = Path("handoffs") / filename
        path = self._run_dir / rel_path
        envelope = HandoffArtifact(
            metadata=HandoffArtifactMetadata(phase_id=phase_id, role_id=role_id),
            parts=[HandoffArtifactPart(body=handoff)],
        )
        atomic_write(path, envelope.model_dump_json(indent=2))
        return ArtifactRef(
            type="handoff",
            ref=rel_path.as_posix(),
            description=f"Handoff context for phase {phase_id} role {role_id} sequence {sequence}",
        )

    def save_decision(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        payload: DecisionPayload,
    ) -> ArtifactRef:
        del session_id
        filename = f"{phase_id}-{sequence}.json"
        rel_path = Path("decisions") / filename
        path = self._run_dir / rel_path
        atomic_write(path, payload.model_dump_json(indent=2))
        return ArtifactRef(
            type="decision",
            ref=rel_path.as_posix(),
            description=f"Decision payload for phase {phase_id} role {role_id} sequence {sequence}",
        )

    def save_file(
        self,
        session_id: str,
        rel_path: str,
        content: str | bytes,
    ) -> ArtifactRef:
        del session_id
        path = self._run_dir / rel_path
        text = content if isinstance(content, str) else content.decode("utf-8")
        atomic_write(path, text)
        return ArtifactRef(
            type="file",
            ref=rel_path,
            description=f"File artifact at {rel_path}",
        )

    def load_artifact(self, ref: ArtifactRef | str) -> bytes:
        relative_ref = ref.ref if isinstance(ref, ArtifactRef) else ref
        path = self._run_dir / relative_ref
        if not path.exists():
            raise StateNotFoundError(f"Artifact not found: {path}")
        return path.read_bytes()


class AsyncFsArtifactStore:
    """Async filesystem artifact store that offloads blocking I/O to a thread pool.

    Wraps :class:`FsArtifactStore` and delegates each operation via
    :func:`asyncio.to_thread` so the event loop is never blocked by file I/O.
    """

    def __init__(self, run_dir: str | Path) -> None:
        self._store = FsArtifactStore(run_dir)

    async def save_handoff(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        handoff: HandoffContext,
    ) -> ArtifactRef:
        return await asyncio.to_thread(
            self._store.save_handoff, session_id, phase_id, role_id, sequence, handoff
        )

    async def save_decision(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        payload: DecisionPayload,
    ) -> ArtifactRef:
        return await asyncio.to_thread(
            self._store.save_decision, session_id, phase_id, role_id, sequence, payload
        )

    async def save_file(
        self,
        session_id: str,
        rel_path: str,
        content: str | bytes,
    ) -> ArtifactRef:
        return await asyncio.to_thread(self._store.save_file, session_id, rel_path, content)

    async def load_artifact(self, ref: ArtifactRef | str) -> bytes:
        return await asyncio.to_thread(self._store.load_artifact, ref)


def save_handoff(
    run_dir: str | Path,
    phase_id: str,
    role_id: str,
    sequence: int,
    handoff: HandoffContext,
) -> ArtifactRef:
    """Convenience wrapper for writing a handoff artifact under a run directory."""

    store = FsArtifactStore(run_dir)
    return store.save_handoff("", phase_id, role_id, sequence, handoff)


def save_decision(
    run_dir: str | Path,
    phase_id: str,
    role_id: str,
    sequence: int,
    payload: DecisionPayload,
) -> ArtifactRef:
    """Convenience wrapper for writing a decision artifact under a run directory."""

    store = FsArtifactStore(run_dir)
    return store.save_decision("", phase_id, role_id, sequence, payload)


__all__ = [
    "AsyncFsArtifactStore",
    "FsArtifactStore",
    "save_decision",
    "save_handoff",
]
