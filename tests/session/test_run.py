"""Tests for WorkflowSession.run(): dir structure, skill extraction, completion, resume."""

from __future__ import annotations

from pathlib import Path

from conftest import MinimalReviewer, MinimalWorker, make_simple_graph
from pawc_kit.contracts import RootConfig, SkillConfig
from pawc_kit.session import WorkflowSession


def _config(state_directory: str) -> RootConfig:
    return RootConfig(
        skill=SkillConfig(name="test-skill", version="1.0.0"),
        state_directory=state_directory,
    )


# ---------------------------------------------------------------------------
# Directory structure creation
# ---------------------------------------------------------------------------


def test_run_creates_sessions_directory(tmp_path: Path) -> None:
    config = _config(str(tmp_path))
    session = WorkflowSession(config=config, graph=make_simple_graph())
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    session.run(session_id="s1")
    assert (tmp_path / "sessions").is_dir()


def test_run_creates_contexts_directory(tmp_path: Path) -> None:
    config = _config(str(tmp_path))
    session = WorkflowSession(config=config, graph=make_simple_graph())
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    session.run(session_id="s1")
    assert (tmp_path / "contexts").is_dir()


def test_run_creates_state_file(tmp_path: Path) -> None:
    config = _config(str(tmp_path))
    session = WorkflowSession(config=config, graph=make_simple_graph())
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    session.run(session_id="s1")
    assert (tmp_path / "sessions" / "execution" / "s1" / "state.json").exists()


def test_run_creates_decisions_subdirectory(tmp_path: Path) -> None:
    config = _config(str(tmp_path))
    session = WorkflowSession(config=config, graph=make_simple_graph())
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    session.run(session_id="s1")
    assert (tmp_path / "sessions" / "execution" / "s1" / "decisions").is_dir()


# ---------------------------------------------------------------------------
# Skill extraction from config
# ---------------------------------------------------------------------------


def test_run_uses_skill_name_from_config(tmp_path: Path) -> None:
    config = _config(str(tmp_path))
    session = WorkflowSession(config=config, graph=make_simple_graph())
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    state = session.run(session_id="s1")
    assert state.skill_name == "test-skill"


def test_run_uses_skill_version_from_config(tmp_path: Path) -> None:
    config = _config(str(tmp_path))
    session = WorkflowSession(config=config, graph=make_simple_graph())
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    state = session.run(session_id="s1")
    assert state.skill_version == "1.0.0"


# ---------------------------------------------------------------------------
# Completed state and context_id
# ---------------------------------------------------------------------------


def test_run_returns_completed_state(tmp_path: Path) -> None:
    config = _config(str(tmp_path))
    session = WorkflowSession(config=config, graph=make_simple_graph())
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    state = session.run(session_id="s1")
    assert state.status == "completed"


def test_run_passes_context_id(tmp_path: Path) -> None:
    config = _config(str(tmp_path))
    session = WorkflowSession(config=config, graph=make_simple_graph())
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    state = session.run(session_id="s1", context_id="ctx-abc")
    assert state.context_id == "ctx-abc"


def test_run_no_context_id_is_none(tmp_path: Path) -> None:
    config = _config(str(tmp_path))
    session = WorkflowSession(config=config, graph=make_simple_graph())
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    state = session.run(session_id="s1")
    assert state.context_id is None


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_run_idempotent_on_completed_state(tmp_path: Path) -> None:
    """Running twice with the same session_id on a completed session should not fail."""
    config = _config(str(tmp_path))
    session = WorkflowSession(config=config, graph=make_simple_graph())
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    state1 = session.run(session_id="s1")
    state2 = session.run(session_id="s1")
    assert state1.status == "completed"
    assert state2.status == "completed"


# ---------------------------------------------------------------------------
# Custom run_directory
# ---------------------------------------------------------------------------


def test_run_custom_run_directory(tmp_path: Path) -> None:
    config = _config(str(tmp_path))
    session = WorkflowSession(
        config=config,
        graph=make_simple_graph(),
        run_directory="sessions/discovery",
    )
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    session.run(session_id="s1")
    assert (tmp_path / "sessions" / "discovery" / "s1" / "state.json").exists()
