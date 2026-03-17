"""Tests for LoggingWorkflowObserver and AsyncLoggingWorkflowObserver."""

from __future__ import annotations

import asyncio
import logging

import pytest

from pawc_kit.adapters.logging import AsyncLoggingWorkflowObserver, LoggingWorkflowObserver
from pawc_kit.contracts.events import (
    IterationCommitted,
    PhaseStarted,
    PhaseTransitioned,
    ReviewCommitted,
    RunCompleted,
    RunFailed,
    RunResumed,
    RunStarted,
)

TS = "2026-01-01T00:00:00Z"


def _run_started() -> RunStarted:
    return RunStarted(
        session_id="s1",
        skill_name="skill",
        phase_id="work",
        role_id="worker",
        revision=0,
        occurred_at=TS,
    )


def _run_resumed() -> RunResumed:
    return RunResumed(
        session_id="s1",
        phase_id="work",
        role_id="worker",
        phase_kind="executor",
        revision=1,
        occurred_at=TS,
    )


def _phase_started(kind: str = "executor") -> PhaseStarted:
    return PhaseStarted(
        session_id="s1",
        phase_id="work",
        role_id="worker",
        phase_kind=kind,
        revision=1,
        occurred_at=TS,  # type: ignore[arg-type]
    )


def _iteration_committed() -> IterationCommitted:
    return IterationCommitted(
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
        handoff_context_ref=None,
    )


def _review_committed(decision: str = "APPROVE") -> ReviewCommitted:
    return ReviewCommitted(
        session_id="s1",
        phase_id="review",
        role_id="reviewer",
        review=1,
        decision=decision,
        confidence_score=90,  # type: ignore[arg-type]
        feedback_loops=0,
        revision=3,
        started_at=TS,
        ended_at=TS,
        target_phase=None,
        chosen_next=None,
        findings_ref=None,
    )


def _phase_transitioned() -> PhaseTransitioned:
    return PhaseTransitioned(
        session_id="s1",
        from_phase_id="work",
        from_role_id="worker",
        to_phase_id="review",
        to_role_id="reviewer",
        revision=4,
        occurred_at=TS,
    )


def _run_completed(status: str = "completed") -> RunCompleted:
    return RunCompleted(
        session_id="s1",
        status=status,
        feedback_loops=0,  # type: ignore[arg-type]
        revision=5,
        started_at=TS,
        completed_at=TS,
    )


def _run_failed() -> RunFailed:
    return RunFailed(
        session_id="s1",
        phase_id="work",
        role_id="worker",
        revision=2,
        occurred_at=TS,
        error_type="ValueError",
        error_message="boom",
    )


# ---------------------------------------------------------------------------
# Level mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event,expected_level",
    [
        (_run_started(), logging.INFO),
        (_run_resumed(), logging.INFO),
        (_phase_started(), logging.DEBUG),
        (_iteration_committed(), logging.DEBUG),
        (_review_committed("APPROVE"), logging.DEBUG),
        (_review_committed("REQUEST_CHANGES"), logging.WARNING),
        (_phase_transitioned(), logging.DEBUG),
        (_run_completed("completed"), logging.INFO),
        (_run_completed("abandoned"), logging.WARNING),
        (_run_failed(), logging.ERROR),
    ],
)
def test_logging_observer_level_mapping(
    event: object, expected_level: int, caplog: pytest.LogCaptureFixture
) -> None:
    obs = LoggingWorkflowObserver()
    with caplog.at_level(logging.DEBUG, logger="pawc_kit.workflow"):
        obs.on_event(event)  # type: ignore[arg-type]
    assert any(r.levelno == expected_level for r in caplog.records)


def test_logging_observer_custom_logger(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.custom")
    obs = LoggingWorkflowObserver(logger)
    with caplog.at_level(logging.DEBUG, logger="test.custom"):
        obs.on_event(_run_started())
    assert len(caplog.records) >= 1


def test_logging_observer_includes_event_extras(caplog: pytest.LogCaptureFixture) -> None:
    obs = LoggingWorkflowObserver()
    with caplog.at_level(logging.INFO, logger="pawc_kit.workflow"):
        obs.on_event(_run_started())
    record = caplog.records[-1]
    assert hasattr(record, "pawc_event")
    assert record.pawc_event == "RunStarted"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# AsyncLoggingWorkflowObserver
# ---------------------------------------------------------------------------


def test_async_logging_observer_logs_event(caplog: pytest.LogCaptureFixture) -> None:
    obs = AsyncLoggingWorkflowObserver()
    with caplog.at_level(logging.DEBUG, logger="pawc_kit.workflow"):
        asyncio.run(obs.on_event(_run_started()))
    assert len(caplog.records) >= 1


@pytest.mark.parametrize(
    "event,expected_level",
    [
        (_run_started(), logging.INFO),
        (_run_failed(), logging.ERROR),
        (_run_completed("abandoned"), logging.WARNING),
    ],
)
def test_async_logging_observer_level_parity(
    event: object, expected_level: int, caplog: pytest.LogCaptureFixture
) -> None:
    obs = AsyncLoggingWorkflowObserver()
    with caplog.at_level(logging.DEBUG, logger="pawc_kit.workflow"):
        asyncio.run(obs.on_event(event))  # type: ignore[arg-type]
    assert any(r.levelno == expected_level for r in caplog.records)
