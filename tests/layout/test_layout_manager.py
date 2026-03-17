"""Tests for LayoutManager: paths, directory creation, read/write state."""

from __future__ import annotations

from pathlib import Path

import pytest

from pawc_kit.contracts.errors import ConfigurationError, StateError
from pawc_kit.contracts.state import SessionState
from pawc_kit.layout import LayoutManager


def _manager(
    base: Path,
    *,
    run_directory: str = "sessions/execution",
    session_id: str = "sess-1",
    state_filename: str = "state.json",
) -> LayoutManager:
    return LayoutManager(
        state_directory=base,
        run_directory=run_directory,
        session_id=session_id,
        state_filename=state_filename,
    )


def _state(session_id: str = "sess-1") -> SessionState:
    return SessionState(
        session_id=session_id,
        skill_name="skill",
        skill_version="1.0.0",
        started_at="2026-01-01T00:00:00Z",
        current_phase="work",
        status="initialized",
    )


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_requires_nonempty_state_directory() -> None:
    with pytest.raises(ConfigurationError, match="state_directory is required"):
        LayoutManager(state_directory="", run_directory="sessions/execution", session_id="s1")


# ---------------------------------------------------------------------------
# run_dir
# ---------------------------------------------------------------------------


def test_run_dir_basic_path(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    assert mgr.run_dir == tmp_path / "sessions" / "execution" / "sess-1"


def test_run_dir_nested_run_directory(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, run_directory="sessions/discovery")
    assert mgr.run_dir == tmp_path / "sessions" / "discovery" / "sess-1"


def test_run_dir_custom_session_id(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, session_id="my-run-xyz")
    assert mgr.run_dir.name == "my-run-xyz"


# ---------------------------------------------------------------------------
# state_path
# ---------------------------------------------------------------------------


def test_state_path_default(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    assert mgr.state_path == mgr.run_dir / "state.json"


def test_state_path_custom_filename(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, state_filename="discovery_state.json")
    assert mgr.state_path.name == "discovery_state.json"


# ---------------------------------------------------------------------------
# ensure_state_directory
# ---------------------------------------------------------------------------


def test_ensure_state_directory_creates_sessions_and_contexts(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.ensure_state_directory()
    assert (tmp_path / "sessions").is_dir()
    assert (tmp_path / "contexts").is_dir()


def test_ensure_state_directory_idempotent(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.ensure_state_directory()
    mgr.ensure_state_directory()  # should not raise


# ---------------------------------------------------------------------------
# initialize_run_directory
# ---------------------------------------------------------------------------


def test_initialize_run_directory_creates_run_dir(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.initialize_run_directory()
    assert mgr.run_dir.is_dir()


def test_initialize_run_directory_creates_decisions_subdir(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.initialize_run_directory()
    assert (mgr.run_dir / "decisions").is_dir()


def test_initialize_run_directory_creates_extra_subdirs(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.initialize_run_directory(subdirs=["handoffs", "logs"])
    assert (mgr.run_dir / "handoffs").is_dir()
    assert (mgr.run_dir / "logs").is_dir()


def test_initialize_run_directory_idempotent(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.initialize_run_directory()
    mgr.initialize_run_directory()  # should not raise


# ---------------------------------------------------------------------------
# write_state / read_state
# ---------------------------------------------------------------------------


def test_write_state_creates_file(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.initialize_run_directory()
    mgr.write_state(_state())
    assert mgr.state_path.exists()


def test_read_state_roundtrip(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.initialize_run_directory()
    mgr.write_state(_state())
    loaded = mgr.read_state()
    assert loaded.session_id == "sess-1"
    assert loaded.skill_version == "1.0.0"


def test_read_state_raises_when_file_missing(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    with pytest.raises(StateError, match="State file not found"):
        mgr.read_state()


def test_write_read_state_custom_filename(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, state_filename="discovery_state.json")
    mgr.initialize_run_directory()
    mgr.write_state(_state())
    loaded = mgr.read_state()
    assert loaded.session_id == "sess-1"
