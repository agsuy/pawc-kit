"""Tests for custom RuntimeBackend injection in WorkflowSession and AsyncWorkflowSession."""

from __future__ import annotations

import asyncio
import warnings
from pathlib import Path

import pytest

from conftest import MinimalReviewer, MinimalWorker, make_simple_graph
from pawc_kit._time import utc_now
from pawc_kit.async_session import AsyncWorkflowSession
from pawc_kit.contracts import RootConfig, SkillConfig
from pawc_kit.contracts.errors import StateNotFoundError
from pawc_kit.contracts.execution import ExecutionRequest
from pawc_kit.contracts.state import SessionState
from pawc_kit.ports.runtime import (
    AsyncResolvedBackend,
    ResolvedBackend,
)
from pawc_kit.ports.state import SessionMetadata, StoredSession
from pawc_kit.session import WorkflowSession
from pawc_kit.workflow.roles import ExecutionResult

# ---------------------------------------------------------------------------
# In-memory state/artifact stores used by the custom backend doubles
# ---------------------------------------------------------------------------


class _InMemoryStateStore:
    """Minimal in-memory StateStore: no filesystem, no real persistence."""

    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {}
        self._revisions: dict[str, int] = {}
        self.resolve_call_count = 0

    def load(self, session_id: str) -> StoredSession:
        if session_id not in self._states:
            raise StateNotFoundError(f"Session not found: {session_id}")
        return StoredSession(state=self._states[session_id], revision=self._revisions[session_id])

    def save(self, snapshot: SessionState, *, expected_revision: str | int) -> StoredSession:
        session_id = snapshot.session_id
        if int(self._revisions.get(session_id, 0)) != int(expected_revision):
            from pawc_kit.contracts.errors import ConcurrencyError

            raise ConcurrencyError("Revision mismatch")
        new_rev = int(expected_revision) + 1
        self._states[session_id] = snapshot
        self._revisions[session_id] = new_rev
        return StoredSession(state=snapshot, revision=new_rev)

    def initialize(self, session_metadata: SessionMetadata) -> StoredSession:
        state = SessionState(
            session_id=session_metadata.session_id,
            context_id=session_metadata.context_id,
            skill_name=session_metadata.skill_name,
            skill_version=session_metadata.skill_version,
            started_at=utc_now(),
            current_phase=session_metadata.first_phase,
            status="initialized",
        )
        self._states[session_metadata.session_id] = state
        self._revisions[session_metadata.session_id] = 0
        return StoredSession(state=state, revision=0)

    def list(self) -> list[StoredSession]:
        return [
            StoredSession(state=s, revision=self._revisions[sid]) for sid, s in self._states.items()
        ]

    def delete(self, session_id: str) -> None:
        self._states.pop(session_id, None)
        self._revisions.pop(session_id, None)


class _InMemoryArtifactStore:
    """Minimal in-memory ArtifactStore: stores bytes by ref string."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def save_handoff(
        self, session_id: str, phase_id: str, role_id: str, sequence: int, handoff: object
    ) -> object:
        from pawc_kit.contracts.artifacts import (
            HandoffArtifact,
            HandoffArtifactMetadata,
            HandoffArtifactPart,
        )
        from pawc_kit.contracts.state import ArtifactRef

        ref = f"handoffs/{phase_id}-{sequence}.json"
        envelope = HandoffArtifact(
            metadata=HandoffArtifactMetadata(phase_id=phase_id, role_id=role_id),
            parts=[HandoffArtifactPart(body=handoff)],  # type: ignore[arg-type]
        )
        self._data[ref] = envelope.model_dump_json().encode()
        return ArtifactRef(type="handoff", ref=ref, description="")

    def save_decision(
        self, session_id: str, phase_id: str, role_id: str, sequence: int, payload: object
    ) -> object:
        from pawc_kit.contracts.state import ArtifactRef

        ref = f"decisions/{phase_id}-{sequence}.json"
        self._data[ref] = payload.model_dump_json().encode()  # type: ignore[union-attr]
        return ArtifactRef(type="decision", ref=ref, description="")

    def load_artifact(self, ref: object) -> bytes:
        from pawc_kit.contracts.state import ArtifactRef

        key = ref.ref if isinstance(ref, ArtifactRef) else str(ref)
        if key not in self._data:
            raise StateNotFoundError(f"Artifact not found: {key}")
        return self._data[key]


# ---------------------------------------------------------------------------
# Custom backend doubles
# ---------------------------------------------------------------------------


class _SpyRuntimeBackend:
    """Records resolve() calls and delegates to a shared in-memory store pair."""

    def __init__(self) -> None:
        self.state_store = _InMemoryStateStore()
        self.artifact_store = _InMemoryArtifactStore()
        self.resolve_calls: list[str] = []

    def resolve(self, *, session_id: str) -> ResolvedBackend:
        self.resolve_calls.append(session_id)
        return ResolvedBackend(
            state_store=self.state_store,  # type: ignore[arg-type]
            artifact_store=self.artifact_store,  # type: ignore[arg-type]
        )


class _SpyAsyncRuntimeBackend:
    """Async counterpart of _SpyRuntimeBackend."""

    def __init__(self) -> None:
        self.state_store = _InMemoryStateStore()
        self.artifact_store = _InMemoryArtifactStore()
        self.resolve_calls: list[str] = []

    async def resolve(self, *, session_id: str) -> AsyncResolvedBackend:
        self.resolve_calls.append(session_id)

        class _AsyncStateStoreAdapter:
            def __init__(self, inner: _InMemoryStateStore) -> None:
                self._inner = inner

            async def load(self, session_id: str) -> StoredSession:
                return self._inner.load(session_id)

            async def save(
                self, snapshot: SessionState, *, expected_revision: str | int
            ) -> StoredSession:
                return self._inner.save(snapshot, expected_revision=expected_revision)

            async def initialize(self, session_metadata: SessionMetadata) -> StoredSession:
                return self._inner.initialize(session_metadata)

            async def list(self) -> list[StoredSession]:
                return self._inner.list()

            async def delete(self, session_id: str) -> None:
                self._inner.delete(session_id)

        class _AsyncArtifactStoreAdapter:
            def __init__(self, inner: _InMemoryArtifactStore) -> None:
                self._inner = inner

            async def save_handoff(self, *args: object, **kwargs: object) -> object:
                return self._inner.save_handoff(*args, **kwargs)  # type: ignore[arg-type]

            async def save_decision(self, *args: object, **kwargs: object) -> object:
                return self._inner.save_decision(*args, **kwargs)  # type: ignore[arg-type]

            async def load_artifact(self, ref: object) -> bytes:
                return self._inner.load_artifact(ref)

        return AsyncResolvedBackend(
            state_store=_AsyncStateStoreAdapter(self.state_store),  # type: ignore[arg-type]
            artifact_store=_AsyncArtifactStoreAdapter(self.artifact_store),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(state_directory: str = "/does-not-matter") -> RootConfig:
    return RootConfig(
        skill=SkillConfig(name="test-skill", version="1.0.0"),
        state_directory=state_directory,
    )


# ---------------------------------------------------------------------------
# WorkflowSession — backend injection
# ---------------------------------------------------------------------------


def test_session_delegates_to_custom_backend() -> None:
    """When a backend is provided, session calls backend.resolve() instead of building FS stores."""
    spy = _SpyRuntimeBackend()
    session = WorkflowSession(config=_config(), graph=make_simple_graph(), backend=spy)
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())

    session.run(session_id="s1")

    assert spy.resolve_calls == ["s1"]


def test_session_custom_backend_end_to_end_completes() -> None:
    """Full run with a custom in-memory backend reaches completed state."""
    spy = _SpyRuntimeBackend()
    session = WorkflowSession(config=_config(), graph=make_simple_graph(), backend=spy)
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())

    state = session.run(session_id="s1")

    assert state.status == "completed"
    assert state.skill_name == "test-skill"


def test_session_custom_backend_no_fs_side_effects(tmp_path: Path) -> None:
    """With a custom backend, no filesystem directories or files are created."""
    spy = _SpyRuntimeBackend()
    session = WorkflowSession(config=_config(str(tmp_path)), graph=make_simple_graph(), backend=spy)
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())

    session.run(session_id="s1")

    assert not (tmp_path / "sessions").exists()


def test_session_backend_plus_run_directory_emits_warning() -> None:
    """Providing backend= alongside run_directory= emits a UserWarning."""
    spy = _SpyRuntimeBackend()
    with pytest.warns(UserWarning, match="run_directory and state_filename are ignored"):
        WorkflowSession(
            config=_config(),
            graph=make_simple_graph(),
            backend=spy,
            run_directory="sessions/custom",
        )


def test_session_backend_plus_state_filename_emits_warning() -> None:
    """Providing backend= alongside state_filename= emits a UserWarning."""
    spy = _SpyRuntimeBackend()
    with pytest.warns(UserWarning, match="run_directory and state_filename are ignored"):
        WorkflowSession(
            config=_config(),
            graph=make_simple_graph(),
            backend=spy,
            state_filename="custom.json",
        )


def test_session_no_backend_no_warning() -> None:
    """No warning is emitted when backend is not provided."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        WorkflowSession(
            config=_config(),
            graph=make_simple_graph(),
            run_directory="sessions/custom",
        )


# ---------------------------------------------------------------------------
# AsyncWorkflowSession — backend injection
# ---------------------------------------------------------------------------


def test_async_session_delegates_to_custom_backend() -> None:
    """AsyncWorkflowSession calls backend.resolve() instead of building FS stores."""
    spy = _SpyAsyncRuntimeBackend()

    class _MinimalAsyncWorker:
        async def execute(self, req: ExecutionRequest) -> ExecutionResult:
            from pawc_kit.contracts.artifacts import HandoffContext

            return ExecutionResult(
                role_id=req.phase.role_id,
                ended_at=utc_now(),
                confidence_score=90,
                summary="done",
                handoff=HandoffContext(summary="async handoff"),
                chosen_next=None,
            )

    class _MinimalAsyncReviewer:
        async def review(self, ctx: object) -> object:
            from pawc_kit.workflow.roles import ReviewDecision, ReviewResult

            return ReviewResult(
                role_id="reviewer-role",
                ended_at=utc_now(),
                decision=ReviewDecision(
                    decision="APPROVE",
                    confidence_score=90,
                    counts_verified=True,
                    summary="approved",
                    findings=[],
                    target_phase=None,
                ),
                chosen_next=None,
            )

    session = AsyncWorkflowSession(config=_config(), graph=make_simple_graph(), backend=spy)
    session.register_role("worker-role", _MinimalAsyncWorker())
    session.register_role("reviewer-role", _MinimalAsyncReviewer())

    state = asyncio.run(session.run(session_id="s1"))

    assert spy.resolve_calls == ["s1"]
    assert state.status == "completed"


def test_async_session_backend_plus_run_directory_emits_warning() -> None:
    """Providing backend= alongside run_directory= emits a UserWarning for async session."""
    spy = _SpyAsyncRuntimeBackend()
    with pytest.warns(UserWarning, match="run_directory and state_filename are ignored"):
        AsyncWorkflowSession(
            config=_config(),
            graph=make_simple_graph(),
            backend=spy,
            run_directory="sessions/custom",
        )
