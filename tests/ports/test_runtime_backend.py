"""Protocol conformance and behaviour tests for RuntimeBackend / FsRuntimeBackend."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pawc_kit.adapters.fs.runtime import AsyncFsRuntimeBackend, FsRuntimeBackend
from pawc_kit.contracts.errors import StateNotFoundError
from pawc_kit.ports.runtime import AsyncRuntimeBackend, RuntimeBackend
from pawc_kit.ports.state import SessionMetadata


# ---------------------------------------------------------------------------
# Protocol conformance (isinstance via runtime_checkable)
# ---------------------------------------------------------------------------


def test_fs_runtime_backend_satisfies_runtime_backend_protocol(tmp_path: Path) -> None:
    backend = FsRuntimeBackend(tmp_path)
    assert isinstance(backend, RuntimeBackend)


def test_async_fs_runtime_backend_satisfies_async_runtime_backend_protocol(
    tmp_path: Path,
) -> None:
    backend = AsyncFsRuntimeBackend(tmp_path)
    assert isinstance(backend, AsyncRuntimeBackend)


def test_plain_object_does_not_satisfy_runtime_backend() -> None:
    assert not isinstance(object(), RuntimeBackend)


def test_plain_object_does_not_satisfy_async_runtime_backend() -> None:
    assert not isinstance(object(), AsyncRuntimeBackend)


# ---------------------------------------------------------------------------
# FsRuntimeBackend.resolve() — directory structure
# ---------------------------------------------------------------------------


def test_resolve_creates_sessions_directory(tmp_path: Path) -> None:
    FsRuntimeBackend(tmp_path).resolve(session_id="s1")
    assert (tmp_path / "sessions").is_dir()


def test_resolve_creates_contexts_directory(tmp_path: Path) -> None:
    FsRuntimeBackend(tmp_path).resolve(session_id="s1")
    assert (tmp_path / "contexts").is_dir()


def test_resolve_creates_run_directory(tmp_path: Path) -> None:
    FsRuntimeBackend(tmp_path).resolve(session_id="s1")
    assert (tmp_path / "sessions" / "execution" / "s1").is_dir()


def test_resolve_creates_decisions_subdirectory(tmp_path: Path) -> None:
    FsRuntimeBackend(tmp_path).resolve(session_id="s1")
    assert (tmp_path / "sessions" / "execution" / "s1" / "decisions").is_dir()


def test_resolve_custom_run_directory(tmp_path: Path) -> None:
    FsRuntimeBackend(tmp_path, run_directory="sessions/discovery").resolve(session_id="s1")
    assert (tmp_path / "sessions" / "discovery" / "s1").is_dir()


def test_resolve_custom_state_filename(tmp_path: Path) -> None:
    backend = FsRuntimeBackend(tmp_path, state_filename="run.json")
    resolved = backend.resolve(session_id="s1")
    metadata = SessionMetadata(
        session_id="s1",
        skill_name="test",
        skill_version="1.0.0",
        first_phase="work",
    )
    resolved.state_store.initialize(metadata)
    assert (tmp_path / "sessions" / "execution" / "s1" / "run.json").exists()


# ---------------------------------------------------------------------------
# FsRuntimeBackend.resolve() — store round-trip
# ---------------------------------------------------------------------------


def test_resolve_state_store_round_trip(tmp_path: Path) -> None:
    """Initialize via resolved state store, save, then load back."""
    resolved = FsRuntimeBackend(tmp_path).resolve(session_id="s1")
    metadata = SessionMetadata(
        session_id="s1",
        skill_name="test-skill",
        skill_version="1.0.0",
        first_phase="work",
    )
    stored = resolved.state_store.initialize(metadata)
    assert stored.state.session_id == "s1"
    assert stored.revision == 0

    updated = stored.state.model_copy(update={"status": "in_progress"})
    saved = resolved.state_store.save(updated, expected_revision=0)
    assert saved.state.status == "in_progress"
    assert saved.revision == 1

    loaded = resolved.state_store.load("s1")
    assert loaded.state.status == "in_progress"


def test_resolve_state_store_load_raises_when_not_initialized(tmp_path: Path) -> None:
    resolved = FsRuntimeBackend(tmp_path).resolve(session_id="s1")
    with pytest.raises(StateNotFoundError):
        resolved.state_store.load("s1")


def test_resolve_returns_working_artifact_store(tmp_path: Path) -> None:
    from pawc_kit.contracts.artifacts import HandoffContext

    resolved = FsRuntimeBackend(tmp_path).resolve(session_id="s1")
    ref = resolved.artifact_store.save_handoff(
        "s1", "work", "worker-role", 1, HandoffContext(summary="done")
    )
    data = resolved.artifact_store.load_artifact(ref)
    assert b"done" in data


# ---------------------------------------------------------------------------
# AsyncFsRuntimeBackend.resolve() — directory structure and round-trip
# ---------------------------------------------------------------------------


def test_async_resolve_creates_run_directory(tmp_path: Path) -> None:
    asyncio.run(AsyncFsRuntimeBackend(tmp_path).resolve(session_id="s1"))
    assert (tmp_path / "sessions" / "execution" / "s1").is_dir()


def test_async_resolve_state_store_round_trip(tmp_path: Path) -> None:
    async def _run() -> None:
        resolved = await AsyncFsRuntimeBackend(tmp_path).resolve(session_id="s1")
        metadata = SessionMetadata(
            session_id="s1",
            skill_name="test-skill",
            skill_version="1.0.0",
            first_phase="work",
        )
        stored = await resolved.state_store.initialize(metadata)
        assert stored.state.session_id == "s1"

        updated = stored.state.model_copy(update={"status": "in_progress"})
        saved = await resolved.state_store.save(updated, expected_revision=0)
        assert saved.state.status == "in_progress"

        loaded = await resolved.state_store.load("s1")
        assert loaded.state.status == "in_progress"

    asyncio.run(_run())
