"""Filesystem-backed state store implementations."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pawc_kit._util import utc_now
from pawc_kit.adapters.fs._io import atomic_write
from pawc_kit.contracts.errors import ConcurrencyError, StateError, StateNotFoundError
from pawc_kit.contracts.state import SessionState
from pawc_kit.ports.state import SessionMetadata, StoredSession


class FsStateStore:
    """Persist state snapshots and optimistic revisions under a run directory.

    Designed for single-writer use (one process/thread per run directory).
    Concurrent writers can race between the revision check and the write;
    use one store instance per session and do not share run_dir across writers.
    """

    def __init__(self, run_dir: str | Path, state_filename: str = "state.json") -> None:
        self._run_dir = Path(run_dir)
        self._state_path = self._run_dir / state_filename
        self._revision_path = self._run_dir / f".{state_filename}.revision"

    def load(self, session_id: str) -> StoredSession:
        if not self._state_path.exists():
            raise StateNotFoundError(f"State file not found: {self._state_path}")
        state = SessionState.model_validate_json(self._state_path.read_text(encoding="utf-8"))
        if state.session_id != session_id:
            raise StateError(
                f"Loaded session_id {state.session_id!r} does not match requested {session_id!r}"
            )
        return StoredSession(state=state, revision=self._read_revision())

    def save(
        self,
        snapshot: SessionState,
        *,
        expected_revision: str | int,
    ) -> StoredSession:
        if not self._state_path.exists():
            raise StateNotFoundError(f"State file not found: {self._state_path}")
        current_revision = self._read_revision()
        if current_revision != expected_revision:
            raise ConcurrencyError(
                f"Expected revision {expected_revision!r}, found {current_revision!r}"
            )
        next_revision = int(current_revision) + 1
        atomic_write(self._state_path, snapshot.model_dump_json(indent=2))
        atomic_write(self._revision_path, str(next_revision))
        return StoredSession(state=snapshot, revision=next_revision)

    def initialize(self, session_metadata: SessionMetadata) -> StoredSession:
        if self._state_path.exists():
            raise StateError(f"State file already exists: {self._state_path}")
        self._run_dir.mkdir(parents=True, exist_ok=True)
        state = SessionState(
            session_id=session_metadata.session_id,
            context_id=session_metadata.context_id,
            skill_name=session_metadata.skill_name,
            skill_version=session_metadata.skill_version,
            started_at=utc_now(),
            current_phase=session_metadata.first_phase,
            phase_iterations=[],
            reviews=[],
            feedback_loops=0,
            status="initialized",
            completed_at=None,
        )
        atomic_write(self._state_path, state.model_dump_json(indent=2))
        atomic_write(self._revision_path, "0")
        return StoredSession(state=state, revision=0)

    def _read_revision(self) -> int:
        if not self._revision_path.exists():
            return 0
        return int(self._revision_path.read_text(encoding="utf-8").strip() or "0")


class AsyncFsStateStore:
    """Async filesystem state store that offloads blocking I/O to a thread pool.

    Wraps :class:`FsStateStore` and delegates each operation via
    :func:`asyncio.to_thread` so the event loop is never blocked by file I/O.
    """

    def __init__(self, run_dir: str | Path, state_filename: str = "state.json") -> None:
        self._store = FsStateStore(run_dir, state_filename=state_filename)

    async def load(self, session_id: str) -> StoredSession:
        return await asyncio.to_thread(self._store.load, session_id)

    async def save(
        self,
        snapshot: SessionState,
        *,
        expected_revision: str | int,
    ) -> StoredSession:
        return await asyncio.to_thread(
            self._store.save, snapshot, expected_revision=expected_revision
        )

    async def initialize(self, session_metadata: SessionMetadata) -> StoredSession:
        return await asyncio.to_thread(self._store.initialize, session_metadata)


__all__ = ["AsyncFsStateStore", "FsStateStore"]
