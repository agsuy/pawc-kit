"""Shared workflow-test infrastructure: MemoryStateStore, MemoryArtifactStore, RecordingObserver."""

from __future__ import annotations

import pytest

from pawc_kit.contracts.artifacts import HandoffContext
from pawc_kit.contracts.errors import ConcurrencyError, StateNotFoundError
from pawc_kit.contracts.state import ArtifactRef, SessionState
from pawc_kit.ports.state import SessionMetadata, StoredSession


class MemoryArtifactStore:
    """Minimal in-memory ArtifactStore that satisfies the port contract."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def save_handoff(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        handoff: HandoffContext,
    ) -> ArtifactRef:
        del session_id
        ref = f"handoffs/{phase_id}-{sequence}.json"
        self.files[ref] = (
            HandoffContext(summary=handoff.summary, key_artifacts=handoff.key_artifacts)
            .model_dump_json(indent=2)
            .encode("utf-8")
        )
        return ArtifactRef(type="handoff", ref=ref, description="handoff")

    def save_decision(
        self,
        session_id: str,
        phase_id: str,
        role_id: str,
        sequence: int,
        payload: object,
    ) -> ArtifactRef:
        del session_id, role_id
        ref = f"decisions/{phase_id}-{sequence}.json"
        if hasattr(payload, "model_dump_json"):
            self.files[ref] = payload.model_dump_json(indent=2).encode("utf-8")  # type: ignore[union-attr]
        else:
            self.files[ref] = b"{}"
        return ArtifactRef(type="decision", ref=ref, description="decision")

    def save_file(
        self,
        session_id: str,
        rel_path: str,
        content: str | bytes,
    ) -> ArtifactRef:
        del session_id
        data = content.encode("utf-8") if isinstance(content, str) else content
        self.files[rel_path] = data
        return ArtifactRef(type="file", ref=rel_path, description=f"file at {rel_path}")

    def load_artifact(self, ref: ArtifactRef | str) -> bytes:
        key = ref.ref if isinstance(ref, ArtifactRef) else ref
        return self.files[key]


class MemoryStateStore:
    """Minimal in-memory StateStore that enforces optimistic concurrency."""

    def __init__(self) -> None:
        self._stored: StoredSession | None = None
        self.load_calls = 0
        self.save_calls = 0
        self.initialize_calls = 0

    @property
    def current(self) -> StoredSession:
        assert self._stored is not None
        return self._stored

    def load(self, session_id: str) -> StoredSession:
        self.load_calls += 1
        if self._stored is None:
            raise StateNotFoundError(f"missing: {session_id}")
        assert self._stored.state.session_id == session_id
        return StoredSession(
            state=self._stored.state.model_copy(deep=True), revision=self._stored.revision
        )

    def initialize(self, session_metadata: SessionMetadata) -> StoredSession:
        self.initialize_calls += 1
        state = SessionState(
            session_id=session_metadata.session_id,
            context_id=session_metadata.context_id,
            skill_name=session_metadata.skill_name,
            skill_version=session_metadata.skill_version,
            started_at="2026-01-01T00:00:00Z",
            current_phase=session_metadata.first_phase,
            status="initialized",
        )
        self._stored = StoredSession(state=state, revision=0)
        return StoredSession(state=state.model_copy(deep=True), revision=0)

    def save(self, snapshot: SessionState, *, expected_revision: str | int) -> StoredSession:
        self.save_calls += 1
        if self._stored is None:
            raise StateNotFoundError("missing")
        if self._stored.revision != expected_revision:
            raise ConcurrencyError("stale revision")
        self._stored = StoredSession(
            state=snapshot.model_copy(deep=True),
            revision=int(expected_revision) + 1,
        )
        return StoredSession(
            state=self._stored.state.model_copy(deep=True), revision=self._stored.revision
        )


class RecordingObserver:
    """WorkflowObserver that records events and validates revision ordering."""

    def __init__(self, store: MemoryStateStore) -> None:
        self._store = store
        self.events: list[object] = []

    def on_event(self, event: object) -> None:
        assert self._store.current.revision == getattr(event, "revision")
        self.events.append(event)


class AsyncMemoryStateStore:
    def __init__(self, sync: MemoryStateStore) -> None:
        self._sync = sync

    async def load(self, session_id: str) -> StoredSession:
        return self._sync.load(session_id)

    async def initialize(self, session_metadata: SessionMetadata) -> StoredSession:
        return self._sync.initialize(session_metadata)

    async def save(self, snapshot: SessionState, *, expected_revision: str | int) -> StoredSession:
        return self._sync.save(snapshot, expected_revision=expected_revision)


class AsyncMemoryArtifactStore:
    def __init__(self, sync: MemoryArtifactStore) -> None:
        self._sync = sync

    async def save_handoff(
        self, session_id: str, phase_id: str, role_id: str, sequence: int, handoff: HandoffContext
    ) -> ArtifactRef:
        return self._sync.save_handoff(session_id, phase_id, role_id, sequence, handoff)

    async def save_decision(
        self, session_id: str, phase_id: str, role_id: str, sequence: int, payload: object
    ) -> ArtifactRef:
        return self._sync.save_decision(session_id, phase_id, role_id, sequence, payload)

    async def save_file(self, session_id: str, rel_path: str, content: str | bytes) -> ArtifactRef:
        return self._sync.save_file(session_id, rel_path, content)

    async def load_artifact(self, ref: ArtifactRef | str) -> bytes:
        return self._sync.load_artifact(ref)


class AsyncRecordingObserver:
    def __init__(self, store: MemoryStateStore) -> None:
        self._store = store
        self.events: list[object] = []

    async def on_event(self, event: object) -> None:
        assert self._store.current.revision == getattr(event, "revision")
        self.events.append(event)


RUN_KW = dict(session_id="s1", skill_name="skill", skill_version="1.0.0")


@pytest.fixture()
def mem_state_store() -> MemoryStateStore:
    return MemoryStateStore()


@pytest.fixture()
def mem_artifact_store() -> MemoryArtifactStore:
    return MemoryArtifactStore()
