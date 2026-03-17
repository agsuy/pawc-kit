"""Contract tests: all 8 workflow event dataclasses."""

from __future__ import annotations

from pawc_kit.contracts.events import (
    IterationCommitted,
    PhaseStarted,
    PhaseTransitioned,
    ReviewCommitted,
    RunCompleted,
    RunFailed,
    RunResumed,
    RunStarted,
    WorkflowEvent,
)

TS = "2026-01-01T00:00:00Z"


def test_run_started_fields() -> None:
    ev = RunStarted(
        session_id="s1",
        skill_name="skill",
        phase_id="work",
        role_id="worker",
        revision=0,
        occurred_at=TS,
    )
    assert ev.session_id == "s1"
    assert ev.revision == 0


def test_run_resumed_fields() -> None:
    ev = RunResumed(
        session_id="s1",
        phase_id="work",
        role_id="worker",
        phase_kind="executor",
        revision=1,
        occurred_at=TS,
    )
    assert ev.phase_id == "work"
    assert ev.phase_kind == "executor"


def test_phase_started_fields() -> None:
    ev = PhaseStarted(
        session_id="s1",
        phase_id="work",
        role_id="worker",
        phase_kind="executor",
        revision=1,
        occurred_at=TS,
    )
    assert ev.phase_kind == "executor"


def test_iteration_committed_fields() -> None:
    ev = IterationCommitted(
        session_id="s1",
        phase_id="work",
        role_id="worker",
        iteration=1,
        confidence_score=85,
        feedback_loops=0,
        revision=2,
        started_at=TS,
        ended_at=TS,
        chosen_next=None,
        handoff_context_ref="handoffs/work-1.json",
    )
    assert ev.iteration == 1
    assert ev.handoff_context_ref == "handoffs/work-1.json"


def test_review_committed_fields() -> None:
    ev = ReviewCommitted(
        session_id="s1",
        phase_id="review",
        role_id="reviewer",
        review=1,
        decision="APPROVE",
        confidence_score=90,
        feedback_loops=0,
        revision=3,
        started_at=TS,
        ended_at=TS,
        target_phase=None,
        chosen_next=None,
        findings_ref="decisions/review-1.json",
    )
    assert ev.decision == "APPROVE"
    assert ev.findings_ref == "decisions/review-1.json"


def test_phase_transitioned_fields() -> None:
    ev = PhaseTransitioned(
        session_id="s1",
        from_phase_id="work",
        from_role_id="worker",
        to_phase_id="review",
        to_role_id="reviewer",
        revision=4,
        occurred_at=TS,
    )
    assert ev.from_phase_id == "work"
    assert ev.to_phase_id == "review"


def test_run_completed_fields() -> None:
    ev = RunCompleted(
        session_id="s1",
        status="completed",
        feedback_loops=0,
        revision=5,
        started_at=TS,
        completed_at=TS,
    )
    assert ev.status == "completed"


def test_run_failed_fields() -> None:
    ev = RunFailed(
        session_id="s1",
        phase_id="work",
        role_id="worker",
        revision=2,
        occurred_at=TS,
        error_type="ValueError",
        error_message="something went wrong",
    )
    assert ev.error_type == "ValueError"


def test_events_are_frozen() -> None:
    ev = RunStarted(
        session_id="s1",
        skill_name="skill",
        phase_id="work",
        role_id="worker",
        revision=0,
        occurred_at=TS,
    )
    try:
        ev.session_id = "mutated"  # type: ignore[misc]
        assert False, "should have raised"
    except (AttributeError, TypeError):
        pass


def test_workflow_event_type_alias_is_union() -> None:
    # WorkflowEvent is a type alias -- verify each event is an instance of its class
    events: list[WorkflowEvent] = [
        RunStarted(
            session_id="s", skill_name="sk", phase_id="p", role_id="r", revision=0, occurred_at=TS
        ),
        RunCompleted(
            session_id="s",
            status="completed",
            feedback_loops=0,
            revision=1,
            started_at=TS,
            completed_at=TS,
        ),
    ]
    assert isinstance(events[0], RunStarted)
    assert isinstance(events[1], RunCompleted)
