"""Tests for workflow event JSON helpers."""

from __future__ import annotations

import dataclasses

import pytest

from pawc_kit.contracts.events import (
    HumanReviewPending,
    IterationCommitted,
    PhaseStarted,
    PhaseTransitioned,
    ReviewCommitted,
    RunCompleted,
    RunFailed,
    RunResumed,
    RunStarted,
    WorkflowEvent,
    event_from_dict,
    event_timestamp,
    event_to_dict,
    is_workflow_event,
)


def _minimal_run_started() -> RunStarted:
    return RunStarted(
        session_id="s1",
        skill_name="sk",
        phase_id="p1",
        role_id="r1",
        revision=0,
        occurred_at="2026-01-01T00:00:00Z",
    )


def test_event_to_dict_includes_event_type() -> None:
    ev = _minimal_run_started()
    d = event_to_dict(ev)
    assert d["event_type"] == "RunStarted"
    assert d["session_id"] == "s1"


@pytest.mark.parametrize(
    "event",
    [
        _minimal_run_started(),
        RunResumed(
            session_id="s1",
            phase_id="p1",
            role_id="r1",
            phase_kind="executor",
            revision=1,
            occurred_at="2026-01-01T00:00:01Z",
        ),
        PhaseStarted(
            session_id="s1",
            phase_id="p1",
            role_id="r1",
            phase_kind="executor",
            revision=1,
            occurred_at="2026-01-01T00:00:02Z",
        ),
        IterationCommitted(
            session_id="s1",
            phase_id="p1",
            role_id="r1",
            iteration=1,
            confidence_score=80,
            feedback_loops=0,
            revision=1,
            started_at="2026-01-01T00:00:00Z",
            ended_at="2026-01-01T00:00:05Z",
            chosen_next=None,
            handoff_context_ref=None,
        ),
        ReviewCommitted(
            session_id="s1",
            phase_id="p1",
            role_id="r1",
            review=1,
            decision="APPROVE",
            confidence_score=90,
            feedback_loops=0,
            revision=1,
            started_at="2026-01-01T00:00:00Z",
            ended_at="2026-01-01T00:00:06Z",
            target_phase=None,
            chosen_next=None,
            findings_ref=None,
        ),
        HumanReviewPending(
            session_id="s1",
            phase_id="p1",
            role_id="r1",
            review=1,
            revision=1,
            occurred_at="2026-01-01T00:00:07Z",
        ),
        PhaseTransitioned(
            session_id="s1",
            from_phase_id="a",
            from_role_id="ra",
            to_phase_id="b",
            to_role_id="rb",
            revision=2,
            occurred_at="2026-01-01T00:00:08Z",
        ),
        RunCompleted(
            session_id="s1",
            status="completed",
            feedback_loops=0,
            revision=3,
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T00:00:09Z",
        ),
        RunFailed(
            session_id="s1",
            phase_id="p1",
            role_id="r1",
            revision=1,
            occurred_at="2026-01-01T00:00:10Z",
            error_type="ValueError",
            error_message="boom",
        ),
    ],
)
def test_event_round_trip(event: WorkflowEvent) -> None:
    d = event_to_dict(event)
    restored = event_from_dict(d)
    assert restored == event


def test_event_from_dict_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="Unknown event type"):
        event_from_dict({"event_type": "NotAnEvent", "session_id": "x"})


def test_event_from_dict_legacy_type_key() -> None:
    ev = _minimal_run_started()
    d = dataclasses.asdict(ev)
    d["type"] = "RunStarted"
    restored = event_from_dict(d)
    assert restored == ev


def test_is_workflow_event() -> None:
    assert is_workflow_event(_minimal_run_started())
    assert not is_workflow_event(object())


def test_event_timestamp_variants() -> None:
    assert (
        event_timestamp(
            RunCompleted(
                session_id="s1",
                status="completed",
                feedback_loops=0,
                revision=0,
                started_at="a",
                completed_at="done-time",
            )
        )
        == "done-time"
    )
    assert (
        event_timestamp(
            IterationCommitted(
                session_id="s1",
                phase_id="p",
                role_id="r",
                iteration=1,
                confidence_score=1,
                feedback_loops=0,
                revision=0,
                started_at="s",
                ended_at="end-time",
                chosen_next=None,
                handoff_context_ref=None,
            )
        )
        == "end-time"
    )
    assert event_timestamp(_minimal_run_started()) == "2026-01-01T00:00:00Z"


def test_event_timestamp_missing_raises() -> None:
    class _NoTimestamp:
        """Not a real workflow event; exercises the error path."""

    with pytest.raises(ValueError, match="No timestamp field"):
        event_timestamp(_NoTimestamp())  # type: ignore[arg-type]
