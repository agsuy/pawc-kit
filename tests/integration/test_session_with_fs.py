"""Integration tests: WorkflowSession end-to-end with real FS."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import MinimalReviewer, MinimalWorker, build_context_pack, make_simple_graph
from pawc_kit.contracts import RootConfig, SkillConfig
from pawc_kit.session import WorkflowSession


def _session(tmp_path: Path, *, run_directory: str = "sessions/execution") -> WorkflowSession:
    config = RootConfig(
        skill=SkillConfig(name="integration-skill", version="2.0.0"),
        state_directory=str(tmp_path),
    )
    return WorkflowSession(config=config, graph=make_simple_graph(), run_directory=run_directory)


@pytest.mark.integration
def test_session_end_to_end_completes(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    state = session.run(session_id="run-1")
    assert state.status == "completed"
    assert state.skill_name == "integration-skill"
    assert state.skill_version == "2.0.0"


@pytest.mark.integration
def test_session_state_file_at_expected_path(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    session.run(session_id="run-1")
    state_path = tmp_path / "sessions" / "execution" / "run-1" / "state.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert data["session_id"] == "run-1"
    assert data["status"] == "completed"


@pytest.mark.integration
def test_session_decisions_artifact_written(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    state = session.run(session_id="run-1")
    decision_ref = state.reviews[0].findings_ref
    assert decision_ref is not None
    path = tmp_path / "sessions" / "execution" / "run-1" / decision_ref
    assert path.exists()


@pytest.mark.integration
def test_session_resume_on_completed_state(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    state1 = session.run(session_id="run-1")
    state2 = session.run(session_id="run-1")
    assert state1.status == "completed"
    assert state2.status == "completed"


@pytest.mark.integration
def test_session_with_context_id(tmp_path: Path) -> None:
    build_context_pack(tmp_path, "ctx-main")
    session = _session(tmp_path)
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    state = session.run(session_id="run-1", context_id="ctx-main")
    assert state.context_id == "ctx-main"


@pytest.mark.integration
def test_session_custom_run_directory(tmp_path: Path) -> None:
    session = _session(tmp_path, run_directory="sessions/discovery")
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())
    session.run(session_id="run-1")
    assert (tmp_path / "sessions" / "discovery" / "run-1" / "state.json").exists()


@pytest.mark.integration
def test_session_purely_config_driven(tmp_path: Path) -> None:
    """WorkflowSession.from_config() without graph= builds everything from YAML."""
    import textwrap

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        textwrap.dedent(f"""\
            skill:
              name: config-driven-skill
              version: "1.0.0"
            state_directory: {tmp_path}
            workflow:
              phases:
                - phase_id: work
                  role_id: worker-role
                  kind: executor
                  on_complete: [review]
                - phase_id: review
                  role_id: reviewer-role
                  kind: review
                  can_request_changes_from: [work]
              run_directory: sessions/execution
              state_filename: state.json
        """)
    )
    session = WorkflowSession.from_config(config_file)
    session.register_role("worker-role", MinimalWorker())
    session.register_role("reviewer-role", MinimalReviewer())

    state = session.run(session_id="run-1")
    assert state.status == "completed"
    assert state.skill_name == "config-driven-skill"
    assert (tmp_path / "sessions" / "execution" / "run-1" / "state.json").exists()
