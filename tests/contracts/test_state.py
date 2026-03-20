"""Contract tests: state models (SessionState, IterationEntry, ReviewEntry, ArtifactRef)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pawc_kit.contracts.state import (
    ArtifactRef,
    DataCommandEntry,
    IterationEntry,
    ReviewEntry,
    SessionState,
)

# ---------------------------------------------------------------------------
# ArtifactRef
# ---------------------------------------------------------------------------


def test_artifact_ref_accepts_valid_fields() -> None:
    ref = ArtifactRef(type="handoff", ref="handoffs/x-1.json", description="desc")
    assert ref.type == "handoff"
    assert ref.ref == "handoffs/x-1.json"
    assert ref.description == "desc"


def test_artifact_ref_requires_all_fields() -> None:
    with pytest.raises(ValidationError):
        ArtifactRef(type="handoff", ref="x.json")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# DataCommandEntry
# ---------------------------------------------------------------------------


def test_data_command_entry_all_optional() -> None:
    entry = DataCommandEntry()
    assert entry.at is None
    assert entry.command is None
    assert entry.kind is None


def test_data_command_entry_with_values() -> None:
    entry = DataCommandEntry(at="2026-01-01T00:00:00Z", command="git commit", kind="vcs")
    assert entry.command == "git commit"


# ---------------------------------------------------------------------------
# IterationEntry
# ---------------------------------------------------------------------------


def test_iteration_entry_minimal_valid() -> None:
    entry = IterationEntry(
        iteration=1,
        phase_id="work",
        role_id="worker",
        confidence_score=80,
        ended_at="2026-01-01T00:00:00Z",
        summary="done",
    )
    assert entry.iteration == 1
    assert entry.confidence_score == 80


def test_iteration_entry_rejects_zero_iteration() -> None:
    with pytest.raises(ValidationError):
        IterationEntry(
            iteration=0,
            phase_id="work",
            role_id="worker",
            confidence_score=80,
            ended_at="2026-01-01T00:00:00Z",
            summary="done",
        )


def test_iteration_entry_rejects_confidence_above_100() -> None:
    with pytest.raises(ValidationError):
        IterationEntry(
            iteration=1,
            phase_id="work",
            role_id="worker",
            confidence_score=101,
            ended_at="2026-01-01T00:00:00Z",
            summary="done",
        )


def test_iteration_entry_rejects_agent_id_with_version() -> None:
    with pytest.raises(ValidationError, match="agent_id"):
        IterationEntry(
            iteration=1,
            phase_id="work",
            role_id="worker",
            confidence_score=80,
            ended_at="2026-01-01T00:00:00Z",
            summary="done",
            agent_id="my-agent/1.2.3",
        )


def test_iteration_entry_rejects_model_id_with_version() -> None:
    with pytest.raises(ValidationError, match="model_id"):
        IterationEntry(
            iteration=1,
            phase_id="work",
            role_id="worker",
            confidence_score=80,
            ended_at="2026-01-01T00:00:00Z",
            summary="done",
            model_id="gpt@4.0.0",
        )


def test_iteration_entry_plain_agent_id_accepted() -> None:
    entry = IterationEntry(
        iteration=1,
        phase_id="work",
        role_id="worker",
        confidence_score=80,
        ended_at="2026-01-01T00:00:00Z",
        summary="done",
        agent_id="my-agent",
    )
    assert entry.agent_id == "my-agent"


# ---------------------------------------------------------------------------
# ReviewEntry
# ---------------------------------------------------------------------------


def test_review_entry_minimal_valid() -> None:
    entry = ReviewEntry(review=1, phase_id="review", role_id="reviewer")
    assert entry.review == 1
    assert entry.decision is None


def test_review_entry_rejects_zero_review() -> None:
    with pytest.raises(ValidationError):
        ReviewEntry(review=0, phase_id="review", role_id="reviewer")


@pytest.mark.parametrize("decision", ["APPROVE", "REQUEST_CHANGES"])
def test_review_entry_accepts_valid_decision(decision: str) -> None:
    entry = ReviewEntry(review=1, phase_id="review", role_id="reviewer", decision=decision)
    assert entry.decision == decision


def test_review_entry_rejects_invalid_decision() -> None:
    with pytest.raises(ValidationError):
        ReviewEntry(review=1, phase_id="review", role_id="reviewer", decision="MAYBE")


# ---------------------------------------------------------------------------
# SessionState
# ---------------------------------------------------------------------------


def test_session_state_initialized_completed_at_none() -> None:
    state = SessionState(
        session_id="s1",
        skill_name="skill",
        skill_version="1.0.0",
        started_at="2026-01-01T00:00:00Z",
        current_phase="work",
        status="initialized",
        completed_at=None,
    )
    assert state.status == "initialized"
    assert state.completed_at is None


def test_session_state_in_progress_completed_at_none() -> None:
    state = SessionState(
        session_id="s1",
        skill_name="skill",
        skill_version="1.0.0",
        started_at="2026-01-01T00:00:00Z",
        current_phase="work",
        status="in_progress",
    )
    assert state.completed_at is None


def test_session_state_initialized_rejects_completed_at_set() -> None:
    with pytest.raises(ValidationError, match="completed_at must be null"):
        SessionState(
            session_id="s1",
            skill_name="skill",
            skill_version="1.0.0",
            started_at="2026-01-01T00:00:00Z",
            current_phase="work",
            status="initialized",
            completed_at="2026-01-01T01:00:00Z",
        )


def test_session_state_completed_requires_completed_at() -> None:
    with pytest.raises(ValidationError, match="completed_at must be set"):
        SessionState(
            session_id="s1",
            skill_name="skill",
            skill_version="1.0.0",
            started_at="2026-01-01T00:00:00Z",
            current_phase="work",
            status="completed",
            completed_at=None,
        )


def test_session_state_completed_with_completed_at() -> None:
    state = SessionState(
        session_id="s1",
        skill_name="skill",
        skill_version="1.0.0",
        started_at="2026-01-01T00:00:00Z",
        current_phase="work",
        status="completed",
        completed_at="2026-01-01T02:00:00Z",
    )
    assert state.status == "completed"
    assert state.completed_at == "2026-01-01T02:00:00Z"


def test_session_state_abandoned_requires_completed_at() -> None:
    with pytest.raises(ValidationError):
        SessionState(
            session_id="s1",
            skill_name="skill",
            skill_version="1.0.0",
            started_at="2026-01-01T00:00:00Z",
            current_phase="work",
            status="abandoned",
        )


def test_session_state_rejects_invalid_semver_skill_version() -> None:
    with pytest.raises(ValidationError):
        SessionState(
            session_id="s1",
            skill_name="skill",
            skill_version="not-semver",
            started_at="2026-01-01T00:00:00Z",
            current_phase="work",
        )


def test_session_state_json_roundtrip(minimal_session: SessionState) -> None:
    reloaded = SessionState.model_validate_json(minimal_session.model_dump_json())
    assert reloaded.session_id == minimal_session.session_id
    assert reloaded.skill_version == minimal_session.skill_version


def test_session_state_run_metadata_defaults_to_none(minimal_session: SessionState) -> None:
    assert minimal_session.run_metadata is None


def test_session_state_run_metadata_round_trips() -> None:
    state = SessionState(
        session_id="s1",
        skill_name="skill",
        skill_version="1.0.0",
        started_at="2026-01-01T00:00:00Z",
        current_phase="work",
        run_metadata={"model": "gpt-4", "temperature": 0.7},
    )
    reloaded = SessionState.model_validate_json(state.model_dump_json())
    assert reloaded.run_metadata == {"model": "gpt-4", "temperature": 0.7}


def test_session_state_run_metadata_absent_in_json_defaults_to_none() -> None:
    json_without_metadata = (
        '{"session_id":"s1","skill_name":"skill","skill_version":"1.0.0",'
        '"started_at":"2026-01-01T00:00:00Z","current_phase":"work",'
        '"phase_iterations":[],"reviews":[],"feedback_loops":0,'
        '"status":"initialized","completed_at":null}'
    )
    state = SessionState.model_validate_json(json_without_metadata)
    assert state.run_metadata is None
