"""Behavioral tests for OpenTelemetryWorkflowObserver and AsyncOpenTelemetryWorkflowObserver."""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import StatusCode

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


class _CapturingSpanExporter(SpanExporter):
    """Collect finished spans for tests (replaces removed InMemorySpanExporter)."""

    def __init__(self) -> None:
        self.finished: list[ReadableSpan] = []

    def export(self, spans):  # type: ignore[no-untyped-def]
        self.finished.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


TS = "2026-01-01T00:00:00Z"
TS_START = "2026-01-01T00:00:00Z"
TS_END = "2026-01-01T00:00:02Z"
TS_FRAC_START = "2026-01-01T00:00:00.000000Z"
TS_FRAC_END = "2026-01-01T00:00:01.500000Z"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_observer() -> tuple[
    OpenTelemetryWorkflowObserver, InMemoryMetricReader, _CapturingSpanExporter
]:
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    span_exporter = _CapturingSpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    obs = OpenTelemetryWorkflowObserver.__new__(OpenTelemetryWorkflowObserver)
    # Wire the observer to use our test MeterProvider and TracerProvider (not globals).

    meter = meter_provider.get_meter("pawc_kit.workflow")
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
    obs._prompt_tokens = meter.create_counter("pawc.workflow.tokens.prompt")
    obs._completion_tokens = meter.create_counter("pawc.workflow.tokens.completion")
    obs._total_tokens = meter.create_counter("pawc.workflow.tokens.total")
    obs._tracer = tracer_provider.get_tracer("pawc_kit.workflow")
    obs._active_spans = {}
    return obs, reader, span_exporter


def _finished(exporter: _CapturingSpanExporter) -> list[ReadableSpan]:
    return list(exporter.finished)


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
    obs, reader, _ = _make_observer()
    obs.on_event(_run_started())
    data = _collect(reader)
    assert "pawc.workflow.runs" in data
    assert _sum_counter(data["pawc.workflow.runs"]) == 1
    attrs = _attrs(data["pawc.workflow.runs"])
    assert any(a.get("outcome") == "started" for a in attrs)
    assert any(a.get("phase_id") == "work" for a in attrs)


def test_run_completed_increments_runs_counter_and_records_duration() -> None:
    obs, reader, _ = _make_observer()
    obs.on_event(_run_completed("completed"))
    data = _collect(reader)
    assert _sum_counter(data["pawc.workflow.runs"]) == 1
    attrs = _attrs(data["pawc.workflow.runs"])
    assert any(a.get("outcome") == "completed" for a in attrs)
    assert "pawc.workflow.run.duration.seconds" in data
    assert _histogram_counts(data["pawc.workflow.run.duration.seconds"]) == [1]
    assert _histogram_sums(data["pawc.workflow.run.duration.seconds"])[0] == pytest.approx(2.0)


def test_run_failed_increments_failures_counter() -> None:
    obs, reader, _ = _make_observer()
    obs.on_event(_run_failed())
    data = _collect(reader)
    assert "pawc.workflow.run_failures" in data
    assert _sum_counter(data["pawc.workflow.run_failures"]) == 1
    attrs = _attrs(data["pawc.workflow.run_failures"])
    assert any(a.get("phase_id") == "work" for a in attrs)


def test_run_failed_unknown_phase_uses_fallback_attribute() -> None:
    obs, reader, _ = _make_observer()
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
    obs, reader, _ = _make_observer()
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
    obs, reader, _ = _make_observer()
    obs.on_event(_review_committed("APPROVE"))
    data = _collect(reader)
    assert "pawc.workflow.reviews" in data
    assert _sum_counter(data["pawc.workflow.reviews"]) == 1
    attrs = _attrs(data["pawc.workflow.reviews"])
    assert any(a.get("outcome") == "approve" for a in attrs)


def test_review_committed_request_changes_records_outcome() -> None:
    obs, reader, _ = _make_observer()
    obs.on_event(_review_committed("REQUEST_CHANGES"))
    data = _collect(reader)
    attrs = _attrs(data["pawc.workflow.reviews"])
    assert any(a.get("outcome") == "request_changes" for a in attrs)


def test_review_committed_records_duration() -> None:
    obs, reader, _ = _make_observer()
    obs.on_event(_review_committed())
    data = _collect(reader)
    assert "pawc.workflow.review.duration.seconds" in data
    assert _histogram_sums(data["pawc.workflow.review.duration.seconds"])[0] == pytest.approx(2.0)


def test_phase_started_increments_phases_counter() -> None:
    obs, reader, _ = _make_observer()
    obs.on_event(_phase_started("executor"))
    data = _collect(reader)
    assert "pawc.workflow.phases" in data
    assert _sum_counter(data["pawc.workflow.phases"]) == 1
    attrs = _attrs(data["pawc.workflow.phases"])
    assert any(a.get("phase_kind") == "executor" for a in attrs)


def test_phase_transitioned_increments_transitions_counter() -> None:
    obs, reader, _ = _make_observer()
    obs.on_event(_phase_transitioned())
    data = _collect(reader)
    assert "pawc.workflow.transitions" in data
    assert _sum_counter(data["pawc.workflow.transitions"]) == 1
    attrs = _attrs(data["pawc.workflow.transitions"])
    assert any(a.get("from_phase") == "work" and a.get("to_phase") == "review" for a in attrs)


def test_run_resumed_increments_resumes_counter() -> None:
    obs, reader, _ = _make_observer()
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
    obs, reader, _ = _make_observer()
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
    obs, reader, _ = _make_observer()
    async_obs = AsyncOpenTelemetryWorkflowObserver.__new__(AsyncOpenTelemetryWorkflowObserver)
    async_obs._delegate = obs

    asyncio.run(async_obs.on_event(_run_started()))
    data = _collect(reader)
    assert "pawc.workflow.runs" in data
    assert _sum_counter(data["pawc.workflow.runs"]) == 1


def test_async_observer_handles_all_event_types() -> None:
    obs, reader, _ = _make_observer()
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


# ---------------------------------------------------------------------------
# Tracing (spans)
# ---------------------------------------------------------------------------


def test_run_span_ended_on_run_completed_with_ok_status() -> None:
    obs, _, exporter = _make_observer()
    obs.on_event(_run_started())
    obs.on_event(_run_completed("completed"))
    spans = _finished(exporter)
    run_spans = [s for s in spans if s.name == "pawc.workflow.run"]
    assert len(run_spans) == 1
    assert run_spans[0].status.status_code == StatusCode.OK
    attrs = dict(run_spans[0].attributes)
    assert attrs.get("session_id") == "s1"
    assert attrs.get("skill_name") == "skill"
    assert attrs.get("resumed") is False
    assert attrs.get("run.status") == "completed"
    assert attrs.get("run.feedback_loops") == 0


def test_run_span_ended_on_run_failed_with_error_status() -> None:
    obs, _, exporter = _make_observer()
    obs.on_event(_run_started())
    obs.on_event(_run_failed())
    spans = _finished(exporter)
    run_spans = [s for s in spans if s.name == "pawc.workflow.run"]
    assert len(run_spans) == 1
    assert run_spans[0].status.status_code == StatusCode.ERROR
    attrs = dict(run_spans[0].attributes)
    assert attrs.get("error.type") == "ValueError"
    assert attrs.get("error.message") == "boom"


def test_phase_span_parent_is_run_span() -> None:
    obs, _, exporter = _make_observer()
    obs.on_event(_run_started())
    obs.on_event(_phase_started())
    obs.on_event(_phase_transitioned())
    obs.on_event(_run_completed())
    spans = _finished(exporter)
    run_span = next(s for s in spans if s.name == "pawc.workflow.run")
    phase_span = next(s for s in spans if s.name == "pawc.workflow.phase")
    assert phase_span.parent.span_id == run_span.context.span_id


def test_iteration_span_parent_is_phase_span_and_uses_event_timestamps() -> None:
    obs, _, exporter = _make_observer()
    obs.on_event(_run_started())
    obs.on_event(_phase_started())
    obs.on_event(_iteration_committed())
    obs.on_event(_run_completed())
    spans = _finished(exporter)
    phase_span = next(s for s in spans if s.name == "pawc.workflow.phase")
    iter_span = next(s for s in spans if s.name == "pawc.workflow.iteration")
    assert iter_span.parent.span_id == phase_span.context.span_id
    attrs = dict(iter_span.attributes)
    assert attrs.get("iteration") == 1
    assert attrs.get("confidence_score") == 85
    assert attrs.get("session_id") == "s1"
    assert iter_span.start_time is not None and iter_span.end_time is not None
    expected_duration_ns = 2 * 1_000_000_000
    assert iter_span.end_time - iter_span.start_time == expected_duration_ns


def test_review_span_parent_is_phase_span() -> None:
    obs, _, exporter = _make_observer()
    obs.on_event(_run_started())
    obs.on_event(_phase_started("review"))
    obs.on_event(_review_committed("APPROVE"))
    obs.on_event(_run_completed())
    spans = _finished(exporter)
    phase_span = next(s for s in spans if s.name == "pawc.workflow.phase")
    review_span = next(s for s in spans if s.name == "pawc.workflow.review")
    assert review_span.parent.span_id == phase_span.context.span_id
    attrs = dict(review_span.attributes)
    assert attrs.get("decision") == "APPROVE"
    assert attrs.get("review") == 1


def test_resumed_run_span_has_resumed_attribute() -> None:
    obs, _, exporter = _make_observer()
    obs.on_event(_run_resumed())
    obs.on_event(_run_completed())
    spans = _finished(exporter)
    run_spans = [s for s in spans if s.name == "pawc.workflow.run"]
    assert len(run_spans) == 1
    attrs = dict(run_spans[0].attributes)
    assert attrs.get("resumed") is True
    assert "skill_name" not in attrs


def test_missing_run_span_phase_started_still_exports_phase_span() -> None:
    obs, _, exporter = _make_observer()
    obs.on_event(_phase_started())
    obs.on_event(_phase_transitioned())
    spans = _finished(exporter)
    phase_spans = [s for s in spans if s.name == "pawc.workflow.phase"]
    assert len(phase_spans) == 1
    parent = phase_spans[0].parent
    assert parent is None or not parent.is_valid


def test_concurrent_sessions_independent_traces() -> None:
    obs, _, exporter = _make_observer()

    def flow(sid: str) -> None:
        obs.on_event(
            RunStarted(
                session_id=sid,
                skill_name="sk",
                phase_id="work",
                role_id="w",
                revision=0,
                occurred_at=TS,
            )
        )
        obs.on_event(
            RunCompleted(
                session_id=sid,
                status="completed",
                feedback_loops=0,
                revision=1,
                started_at=TS_START,
                completed_at=TS_END,
            )
        )

    flow("s-a")
    flow("s-b")
    spans = _finished(exporter)
    runs = [s for s in spans if s.name == "pawc.workflow.run"]
    assert len(runs) == 2
    by_session = {dict(s.attributes).get("session_id"): s for s in runs}
    assert set(by_session) == {"s-a", "s-b"}


def test_duplicate_run_started_ends_previous_run_span() -> None:
    obs, _, exporter = _make_observer()
    obs.on_event(_run_started())
    obs.on_event(
        RunStarted(
            session_id="s1",
            skill_name="skill2",
            phase_id="work",
            role_id="worker",
            revision=1,
            occurred_at=TS,
        )
    )
    obs.on_event(_run_completed())
    spans = _finished(exporter)
    runs = [s for s in spans if s.name == "pawc.workflow.run"]
    assert len(runs) == 2
    ended_without_status = [s for s in runs if s.status.status_code == StatusCode.UNSET]
    assert len(ended_without_status) == 1
    ok_runs = [
        s for s in spans if s.name == "pawc.workflow.run" and s.status.status_code == StatusCode.OK
    ]
    assert len(ok_runs) == 1


# ---------------------------------------------------------------------------
# Token usage metrics and span attributes
# ---------------------------------------------------------------------------


def test_iteration_committed_with_tokens_records_counters() -> None:
    obs, reader, _ = _make_observer()
    obs.on_event(_run_started())
    obs.on_event(_phase_started())
    obs.on_event(
        IterationCommitted(
            session_id="s1",
            phase_id="work",
            role_id="worker",
            iteration=1,
            confidence_score=90,
            feedback_loops=0,
            revision=1,
            started_at=TS_START,
            ended_at=TS_END,
            chosen_next=None,
            handoff_context_ref=None,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            model="gpt-4o-2024-08-06",
            model_requested="gpt-4o",
        )
    )
    metrics = _collect(reader)
    assert "pawc.workflow.tokens.prompt" in metrics
    assert _sum_counter(metrics["pawc.workflow.tokens.prompt"]) == 100
    assert _sum_counter(metrics["pawc.workflow.tokens.completion"]) == 50
    assert _sum_counter(metrics["pawc.workflow.tokens.total"]) == 150


def test_review_committed_with_tokens_records_counters() -> None:
    obs, reader, _ = _make_observer()
    obs.on_event(_run_started())
    obs.on_event(_phase_started())
    obs.on_event(
        ReviewCommitted(
            session_id="s1",
            phase_id="review",
            role_id="reviewer",
            review=1,
            decision="APPROVE",
            confidence_score=88,
            feedback_loops=0,
            revision=1,
            started_at=TS_START,
            ended_at=TS_END,
            target_phase=None,
            chosen_next=None,
            findings_ref=None,
            prompt_tokens=80,
            completion_tokens=30,
            total_tokens=110,
            model="gpt-4o-2024-08-06",
        )
    )
    metrics = _collect(reader)
    assert _sum_counter(metrics["pawc.workflow.tokens.total"]) == 110


def test_run_completed_span_includes_total_token_attributes() -> None:
    obs, _, exporter = _make_observer()
    obs.on_event(_run_started())
    obs.on_event(
        RunCompleted(
            session_id="s1",
            status="completed",
            feedback_loops=0,
            revision=1,
            started_at=TS_START,
            completed_at=TS_END,
            total_prompt_tokens=180,
            total_completion_tokens=80,
            total_tokens=260,
        )
    )
    spans = _finished(exporter)
    run_spans = [s for s in spans if s.name == "pawc.workflow.run"]
    assert len(run_spans) == 1
    attrs = dict(run_spans[0].attributes)
    assert attrs["run.total_prompt_tokens"] == 180
    assert attrs["run.total_completion_tokens"] == 80
    assert attrs["run.total_tokens"] == 260


def test_iteration_without_tokens_does_not_record_counters() -> None:
    obs, reader, _ = _make_observer()
    obs.on_event(_run_started())
    obs.on_event(_phase_started())
    obs.on_event(
        IterationCommitted(
            session_id="s1",
            phase_id="work",
            role_id="worker",
            iteration=1,
            confidence_score=90,
            feedback_loops=0,
            revision=1,
            started_at=TS_START,
            ended_at=TS_END,
            chosen_next=None,
            handoff_context_ref=None,
        )
    )
    metrics = _collect(reader)
    assert "pawc.workflow.tokens.total" not in metrics
