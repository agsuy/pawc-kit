"""Tests for FsStateStore and AsyncFsStateStore."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pawc_kit.adapters.fs.state_store import AsyncFsStateStore, FsStateStore
from pawc_kit.contracts.errors import ConcurrencyError, StateError, StateNotFoundError
from pawc_kit.ports.state import SessionMetadata, StoredSession


def _metadata(session_id: str = "sess-1") -> SessionMetadata:
    return SessionMetadata(
        session_id=session_id,
        skill_name="skill",
        skill_version="1.0.0",
        first_phase="work",
    )


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


def test_initialize_creates_state_file(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path)
    stored = store.initialize(_metadata())
    assert (tmp_path / "state.json").exists()
    assert stored.state.session_id == "sess-1"
    assert stored.state.status == "initialized"
    assert stored.revision == 0


def test_initialize_with_context_id(tmp_path: Path) -> None:
    meta = SessionMetadata(
        session_id="sess-1",
        skill_name="skill",
        skill_version="1.0.0",
        first_phase="work",
        context_id="ctx-abc",
    )
    store = FsStateStore(tmp_path)
    stored = store.initialize(meta)
    assert stored.state.context_id == "ctx-abc"


def test_initialize_twice_raises(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path)
    store.initialize(_metadata())
    with pytest.raises(StateError):
        store.initialize(_metadata())


def test_initialize_custom_state_filename(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path, state_filename="discovery_state.json")
    store.initialize(_metadata())
    assert (tmp_path / "discovery_state.json").exists()


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


def test_load_returns_stored_session(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path)
    store.initialize(_metadata())
    loaded = store.load("sess-1")
    assert loaded.state.session_id == "sess-1"
    assert isinstance(loaded, StoredSession)


def test_load_raises_when_file_missing(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path)
    with pytest.raises(StateNotFoundError):
        store.load("sess-1")


def test_load_raises_on_session_id_mismatch(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path)
    store.initialize(_metadata("sess-1"))
    with pytest.raises(StateError, match="does not match"):
        store.load("sess-other")


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


def test_save_increments_revision(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path)
    initial = store.initialize(_metadata())
    updated = initial.state.model_copy(update={"status": "in_progress"})
    saved = store.save(updated, expected_revision=initial.revision)
    assert saved.revision == 1


def test_save_persists_state_to_disk(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path)
    initial = store.initialize(_metadata())
    updated = initial.state.model_copy(update={"status": "in_progress"})
    store.save(updated, expected_revision=initial.revision)
    reloaded = store.load("sess-1")
    assert reloaded.state.status == "in_progress"


def test_save_raises_on_stale_revision(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path)
    initial = store.initialize(_metadata())
    state = initial.state.model_copy(update={"status": "in_progress"})
    store.save(state, expected_revision=initial.revision)
    with pytest.raises(ConcurrencyError):
        store.save(state, expected_revision=initial.revision)


def test_save_raises_when_file_missing(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path)
    initial = store.initialize(_metadata())
    (tmp_path / "state.json").unlink()
    updated = initial.state.model_copy(update={"status": "in_progress"})
    with pytest.raises(StateNotFoundError):
        store.save(updated, expected_revision=initial.revision)


def test_save_multiple_rounds(tmp_path: Path) -> None:
    store = FsStateStore(tmp_path)
    stored = store.initialize(_metadata())
    for expected_rev in range(5):
        state = stored.state.model_copy(update={"feedback_loops": expected_rev + 1})
        stored = store.save(state, expected_revision=expected_rev)
    assert stored.revision == 5
    assert store.load("sess-1").state.feedback_loops == 5


# ---------------------------------------------------------------------------
# AsyncFsStateStore parity
# ---------------------------------------------------------------------------


def test_async_state_store_initialize_and_load(tmp_path: Path) -> None:
    store = AsyncFsStateStore(tmp_path)
    stored = asyncio.run(store.initialize(_metadata()))
    assert stored.state.session_id == "sess-1"
    loaded = asyncio.run(store.load("sess-1"))
    assert loaded.revision == stored.revision


def test_async_state_store_save_increments_revision(tmp_path: Path) -> None:
    store = AsyncFsStateStore(tmp_path)
    initial = asyncio.run(store.initialize(_metadata()))
    updated = initial.state.model_copy(update={"status": "in_progress"})
    saved = asyncio.run(store.save(updated, expected_revision=initial.revision))
    assert saved.revision == 1


def test_async_state_store_optimistic_locking(tmp_path: Path) -> None:
    store = AsyncFsStateStore(tmp_path)
    initial = asyncio.run(store.initialize(_metadata()))
    state = initial.state.model_copy(update={"status": "in_progress"})
    asyncio.run(store.save(state, expected_revision=initial.revision))
    with pytest.raises(ConcurrencyError):
        asyncio.run(store.save(state, expected_revision=initial.revision))
