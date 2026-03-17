"""Behavioral tests for OpenTelemetryWorkflowObserver and AsyncOpenTelemetryWorkflowObserver."""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from pawc_kit.adapters.otel import (
    AsyncOpenTelemetryWorkflowObserver,
    OpenTelemetryWorkflowObserver,
)
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
TS_START = "2026-01-01T00:00:00Z"
TS_END = "2026-01-01T00:00:02Z"
TS_FRAC_START = "2026-01-01T00:00:00.000000Z"
TS_FRAC_END = "2026-01-01T00:00:01.500000Z"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_observer() -> tuple[OpenTelemetryWorkflowObserver, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    obs = OpenTelemetryWorkflowObserver.__new__(OpenTelemetryWorkflowObserver)
    # Wire the observer to use our test MeterProvider instead of the global one.

    meter = provider.get_meter("pawc_kit.workflow")
    obs._runs = meter.create_counter("pawc.workflow.runs")
    obs._failures = meter.create_counter("pawc.workflow.run_failures")
    obs._iterations = meter.create_counter("pawc.workflow.iterations")
    obs._reviews = meter.create_counter("pawc.workflow.reviews")
    obs._phases = meter.create_counter("pawc.workflow.phases")
    obs._transitions = meter.create_counter("pawc.workflow.transitions")
    obs._resumes = meter.create_counter("pawc.workflow.resumes")
    obs._run_duration = meter.create_histogram("pawc.workflow.run.duration.seconds")
    obs._iteration_duration = meter.create_histogram("pawc.workflow.iteration.duration.seconds")
    obs._review_duration = meter.create_histogram("pawc.workflow.review.duration.seconds")
    return obs, reader


def _collect(reader: InMemoryMetricReader) -> dict[str, object]:
    """Return a flat {metric_name: data_point_list} mapping from the reader."""
    result: dict[str, object] = {}
    metrics_data = reader.get_metrics_data()
    for resource_metric in metrics_data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                result[metric.name] = metric.data
    return result


def _sum_counter(data: object) -> int:
    """Sum all point values for a Sum (counter) metric."""
    from opentelemetry.sdk.metrics.export import MetricExportResult  # noqa: F401

    total = 0
    for point in data.data_points:  # type: ignore[union-attr]
        total += point.value
    return total


def _histogram_counts(data: object) -> list[int]:
    return [p.count for p in data.data_points]  # type: ignore[union-attr]


def _histogram_sums(data: object) -> list[float]:
    return [p.sum for p in data.data_points]  # type: ignore[union-attr]


def _attrs(data: object) -> list[dict[str, str]]:
    return [dict(p.attributes) for p in data.data_points]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Event fixtures
# ---------------------------------------------------------------------------


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
        phase_kind=kind,  # type: ignore[arg-type]
        revision=1,
        occurred_at=TS,
    )


def _iteration_committed(start: str = TS_START, end: str = TS_END) -> IterationCommitted:
    return IterationCommitted(
        session_id="s1",
        phase_id="work",
        role_id="worker",
        iteration=1,
        confidence_score=85,
        feedback_loops=0,
        revision=2,
        started_at=start,
        ended_at=end,
        chosen_next=None,
        handoff_context_ref=None,
    )


def _review_committed(decision: str = "APPROVE") -> ReviewCommitted:
    return ReviewCommitted(
        session_id="s1",
        phase_id="review",
        role_id="reviewer",
        review=1,
        decision=decision,  # type: ignore[arg-type]
        confidence_score=90,
        feedback_loops=0,
        revision=3,
        started_at=TS_START,
        ended_at=TS_END,
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
        status=status,  # type: ignore[arg-type]
        feedback_loops=0,
        revision=5,
        started_at=TS_START,
        completed_at=TS_END,
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
# Counter tests
# ---------------------------------------------------------------------------


def test_run_started_increments_runs_counter() -> None:
    obs, reader = _make_observer()
    obs.on_event(_run_started())
    data = _collect(reader)
    assert "pawc.workflow.runs" in data
    assert _sum_counter(data["pawc.workflow.runs"]) == 1
    attrs = _attrs(data["pawc.workflow.runs"])
    assert any(a.get("outcome") == "started" for a in attrs)
    assert any(a.get("phase_id") == "work" for a in attrs)


def test_run_completed_increments_runs_counter_and_records_duration() -> None:
    obs, reader = _make_observer()
    obs.on_event(_run_completed("completed"))
    data = _collect(reader)
    assert _sum_counter(data["pawc.workflow.runs"]) == 1
    attrs = _attrs(data["pawc.workflow.runs"])
    assert any(a.get("outcome") == "completed" for a in attrs)
    assert "pawc.workflow.run.duration.seconds" in data
    assert _histogram_counts(data["pawc.workflow.run.duration.seconds"]) == [1]
    assert _histogram_sums(data["pawc.workflow.run.duration.seconds"])[0] == pytest.approx(2.0)


def test_run_failed_increments_failures_counter() -> None:
    obs, reader = _make_observer()
    obs.on_event(_run_failed())
    data = _collect(reader)
    assert "pawc.workflow.run_failures" in data
    assert _sum_counter(data["pawc.workflow.run_failures"]) == 1
    attrs = _attrs(data["pawc.workflow.run_failures"])
    assert any(a.get("phase_id") == "work" for a in attrs)


def test_run_failed_unknown_phase_uses_fallback_attribute() -> None:
    obs, reader = _make_observer()
    event = RunFailed(
        session_id="s1",
        phase_id=None,
        role_id=None,
        revision=0,
        occurred_at=TS,
        error_type="ValueError",
        error_message="boom",
    )
    obs.on_event(event)
    data = _collect(reader)
    attrs = _attrs(data["pawc.workflow.run_failures"])
    assert any(a.get("phase_id") == "unknown" for a in attrs)


def test_iteration_committed_increments_iterations_and_records_duration() -> None:
    obs, reader = _make_observer()
    obs.on_event(_iteration_committed())
    data = _collect(reader)
    assert "pawc.workflow.iterations" in data
    assert _sum_counter(data["pawc.workflow.iterations"]) == 1
    assert "pawc.workflow.iteration.duration.seconds" in data
    assert _histogram_counts(data["pawc.workflow.iteration.duration.seconds"]) == [1]
    assert _histogram_sums(data["pawc.workflow.iteration.duration.seconds"])[0] == pytest.approx(
        2.0
    )


def test_review_committed_approve_increments_reviews_counter() -> None:
    obs, reader = _make_observer()
    obs.on_event(_review_committed("APPROVE"))
    data = _collect(reader)
    assert "pawc.workflow.reviews" in data
    assert _sum_counter(data["pawc.workflow.reviews"]) == 1
    attrs = _attrs(data["pawc.workflow.reviews"])
    assert any(a.get("outcome") == "approve" for a in attrs)


def test_review_committed_request_changes_records_outcome() -> None:
    obs, reader = _make_observer()
    obs.on_event(_review_committed("REQUEST_CHANGES"))
    data = _collect(reader)
    attrs = _attrs(data["pawc.workflow.reviews"])
    assert any(a.get("outcome") == "request_changes" for a in attrs)


def test_review_committed_records_duration() -> None:
    obs, reader = _make_observer()
    obs.on_event(_review_committed())
    data = _collect(reader)
    assert "pawc.workflow.review.duration.seconds" in data
    assert _histogram_sums(data["pawc.workflow.review.duration.seconds"])[0] == pytest.approx(2.0)


def test_phase_started_increments_phases_counter() -> None:
    obs, reader = _make_observer()
    obs.on_event(_phase_started("executor"))
    data = _collect(reader)
    assert "pawc.workflow.phases" in data
    assert _sum_counter(data["pawc.workflow.phases"]) == 1
    attrs = _attrs(data["pawc.workflow.phases"])
    assert any(a.get("phase_kind") == "executor" for a in attrs)


def test_phase_transitioned_increments_transitions_counter() -> None:
    obs, reader = _make_observer()
    obs.on_event(_phase_transitioned())
    data = _collect(reader)
    assert "pawc.workflow.transitions" in data
    assert _sum_counter(data["pawc.workflow.transitions"]) == 1
    attrs = _attrs(data["pawc.workflow.transitions"])
    assert any(a.get("from_phase") == "work" and a.get("to_phase") == "review" for a in attrs)


def test_run_resumed_increments_resumes_counter() -> None:
    obs, reader = _make_observer()
    obs.on_event(_run_resumed())
    data = _collect(reader)
    assert "pawc.workflow.resumes" in data
    assert _sum_counter(data["pawc.workflow.resumes"]) == 1
    attrs = _attrs(data["pawc.workflow.resumes"])
    assert any(a.get("phase_id") == "work" and a.get("phase_kind") == "executor" for a in attrs)


# ---------------------------------------------------------------------------
# Fractional-second timestamps
# ---------------------------------------------------------------------------


def test_fractional_second_timestamps_parse_correctly() -> None:
    obs, reader = _make_observer()
    obs.on_event(_iteration_committed(start=TS_FRAC_START, end=TS_FRAC_END))
    data = _collect(reader)
    assert _histogram_sums(data["pawc.workflow.iteration.duration.seconds"])[0] == pytest.approx(
        1.5
    )


# ---------------------------------------------------------------------------
# meter_name kwarg
# ---------------------------------------------------------------------------


def test_meter_name_kwarg_is_honoured() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    # Patch the global metrics module temporarily
    import opentelemetry.metrics as otel_metrics

    original_provider = otel_metrics.get_meter_provider()
    otel_metrics.set_meter_provider(provider)
    try:
        obs = OpenTelemetryWorkflowObserver(meter_name="my.custom.meter")
        obs.on_event(_run_started())
        data = _collect(reader)
        assert "pawc.workflow.runs" in data
    finally:
        otel_metrics.set_meter_provider(original_provider)


# ---------------------------------------------------------------------------
# AsyncOpenTelemetryWorkflowObserver
# ---------------------------------------------------------------------------


def test_async_observer_delegates_to_sync() -> None:
    obs, reader = _make_observer()
    async_obs = AsyncOpenTelemetryWorkflowObserver.__new__(AsyncOpenTelemetryWorkflowObserver)
    async_obs._delegate = obs

    asyncio.run(async_obs.on_event(_run_started()))
    data = _collect(reader)
    assert "pawc.workflow.runs" in data
    assert _sum_counter(data["pawc.workflow.runs"]) == 1


def test_async_observer_handles_all_event_types() -> None:
    obs, reader = _make_observer()
    async_obs = AsyncOpenTelemetryWorkflowObserver.__new__(AsyncOpenTelemetryWorkflowObserver)
    async_obs._delegate = obs

    events = [
        _run_started(),
        _run_resumed(),
        _phase_started(),
        _iteration_committed(),
        _review_committed(),
        _phase_transitioned(),
        _run_completed(),
        _run_failed(),
    ]

    async def _run() -> None:
        for event in events:
            await async_obs.on_event(event)

    asyncio.run(_run())
    data = _collect(reader)

    assert _sum_counter(data["pawc.workflow.runs"]) == 2  # RunStarted + RunCompleted
    assert _sum_counter(data["pawc.workflow.resumes"]) == 1
    assert _sum_counter(data["pawc.workflow.phases"]) == 1
    assert _sum_counter(data["pawc.workflow.iterations"]) == 1
    assert _sum_counter(data["pawc.workflow.reviews"]) == 1
    assert _sum_counter(data["pawc.workflow.transitions"]) == 1
    assert _sum_counter(data["pawc.workflow.run_failures"]) == 1
