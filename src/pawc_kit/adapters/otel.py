"""OpenTelemetry workflow observer adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from opentelemetry.trace import Span

_DEFAULT_METER_NAME = "pawc_kit.workflow"
_DEFAULT_TRACER_NAME = "pawc_kit.workflow"


def _duration_seconds(started_at: str, ended_at: str) -> float:
    return (datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)).total_seconds()


def _iso_to_ns(iso: str) -> int:
    """Convert ISO 8601 timestamp to nanoseconds since Unix epoch."""
    normalized = iso.replace("Z", "+00:00") if iso.endswith("Z") else iso
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


@dataclass
class _ActiveSpans:
    run_span: Span | None = None
    phase_span: Span | None = None


class OpenTelemetryWorkflowObserver:
    """Sync OTEL observer that records metrics and traces for all 8 workflow event types.

    Requires the ``otel`` extra::

        pip install 'pawc-kit[otel]'

    **Threading:** Event delivery is assumed single-threaded per session (same assumption as
    filesystem state stores). The internal span registry is not synchronized.

    Args:
        meter_name: Name passed to ``opentelemetry.metrics.get_meter()``.
            Defaults to ``"pawc_kit.workflow"``.
        tracer_name: Name passed to ``opentelemetry.trace.get_tracer()``.
            Defaults to ``"pawc_kit.workflow"``.
    """

    def __init__(
        self,
        *,
        meter_name: str = _DEFAULT_METER_NAME,
        tracer_name: str = _DEFAULT_TRACER_NAME,
    ) -> None:
        try:
            from opentelemetry import metrics, trace
        except ImportError as exc:
            raise ImportError(
                "OpenTelemetry support requires the 'otel' extra: pip install 'pawc-kit[otel]'"
            ) from exc

        meter = metrics.get_meter(meter_name)
        self._tracer = trace.get_tracer(tracer_name)
        self._active_spans: dict[str, _ActiveSpans] = {}

        self._runs = meter.create_counter(
            "pawc.workflow.runs",
            description="Number of workflow runs (started and completed).",
        )
        self._failures = meter.create_counter(
            "pawc.workflow.run_failures",
            description="Number of workflow runs that ended in an error.",
        )
        self._iterations = meter.create_counter(
            "pawc.workflow.iterations",
            description="Number of executor iterations committed.",
        )
        self._reviews = meter.create_counter(
            "pawc.workflow.reviews",
            description="Number of review decisions committed.",
        )
        self._phases = meter.create_counter(
            "pawc.workflow.phases",
            description="Number of phase activations.",
        )
        self._transitions = meter.create_counter(
            "pawc.workflow.transitions",
            description="Number of phase-to-phase transitions.",
        )
        self._resumes = meter.create_counter(
            "pawc.workflow.resumes",
            description="Number of in-progress runs re-entered from persistent state.",
        )
        self._run_duration = meter.create_histogram(
            "pawc.workflow.run.duration.seconds",
            description="End-to-end duration of completed workflow runs in seconds.",
            unit="s",
        )
        self._iteration_duration = meter.create_histogram(
            "pawc.workflow.iteration.duration.seconds",
            description="Duration of executor iterations in seconds.",
            unit="s",
        )
        self._review_duration = meter.create_histogram(
            "pawc.workflow.review.duration.seconds",
            description="Duration of review executions in seconds.",
            unit="s",
        )
        self._prompt_tokens = meter.create_counter(
            "pawc.workflow.tokens.prompt",
            description="Prompt tokens consumed by LLM calls.",
        )
        self._completion_tokens = meter.create_counter(
            "pawc.workflow.tokens.completion",
            description="Completion tokens consumed by LLM calls.",
        )
        self._total_tokens = meter.create_counter(
            "pawc.workflow.tokens.total",
            description="Total tokens consumed by LLM calls.",
        )

    def _ensure_active(self, session_id: str) -> _ActiveSpans:
        if session_id not in self._active_spans:
            self._active_spans[session_id] = _ActiveSpans()
        return self._active_spans[session_id]

    def _end_phase_span(self, session_id: str) -> None:
        active = self._active_spans.get(session_id)
        if active is None or active.phase_span is None:
            return
        active.phase_span.end()
        active.phase_span = None

    def _trace_run_started(self, event: RunStarted | RunResumed, *, resumed: bool) -> None:
        sid = event.session_id
        active = self._ensure_active(sid)
        if active.run_span is not None:
            self._end_phase_span(sid)
            active.run_span.end()
            active.run_span = None

        run_span = self._tracer.start_span("pawc.workflow.run")
        run_span.set_attribute("session_id", sid)
        run_span.set_attribute("phase_id", event.phase_id)
        run_span.set_attribute("resumed", resumed)
        if isinstance(event, RunStarted):
            run_span.set_attribute("skill_name", event.skill_name)
        active.run_span = run_span

    def _trace_phase_started(self, event: PhaseStarted) -> None:
        from opentelemetry import trace

        sid = event.session_id
        active = self._ensure_active(sid)
        self._end_phase_span(sid)

        from opentelemetry.trace import INVALID_SPAN

        run_span = active.run_span
        ctx = (
            trace.set_span_in_context(run_span)
            if run_span is not None
            else trace.set_span_in_context(INVALID_SPAN)
        )
        phase_span = self._tracer.start_span(
            "pawc.workflow.phase",
            context=ctx,
        )
        phase_span.set_attribute("session_id", sid)
        phase_span.set_attribute("phase_id", event.phase_id)
        phase_span.set_attribute("role_id", event.role_id)
        phase_span.set_attribute("phase_kind", event.phase_kind)
        active.phase_span = phase_span

    def _trace_iteration_committed(self, event: IterationCommitted) -> None:
        from opentelemetry import trace

        sid = event.session_id
        active = self._active_spans.get(sid)
        from opentelemetry.trace import INVALID_SPAN

        phase_span = active.phase_span if active else None
        ctx = (
            trace.set_span_in_context(phase_span)
            if phase_span is not None
            else trace.set_span_in_context(INVALID_SPAN)
        )

        start_ns = _iso_to_ns(event.started_at)
        end_ns = _iso_to_ns(event.ended_at)
        span = self._tracer.start_span(
            "pawc.workflow.iteration",
            context=ctx,
            start_time=start_ns,
        )
        span.set_attribute("session_id", sid)
        span.set_attribute("phase_id", event.phase_id)
        span.set_attribute("iteration", event.iteration)
        span.set_attribute("confidence_score", event.confidence_score)
        if event.total_tokens is not None:
            span.set_attribute("prompt_tokens", event.prompt_tokens or 0)
            span.set_attribute("completion_tokens", event.completion_tokens or 0)
            span.set_attribute("total_tokens", event.total_tokens)
            if event.model:
                span.set_attribute("model", event.model)
        span.end(end_time=end_ns)

    def _trace_review_committed(self, event: ReviewCommitted) -> None:
        from opentelemetry import trace

        sid = event.session_id
        active = self._active_spans.get(sid)
        from opentelemetry.trace import INVALID_SPAN

        phase_span = active.phase_span if active else None
        ctx = (
            trace.set_span_in_context(phase_span)
            if phase_span is not None
            else trace.set_span_in_context(INVALID_SPAN)
        )

        start_ns = _iso_to_ns(event.started_at)
        end_ns = _iso_to_ns(event.ended_at)
        span = self._tracer.start_span(
            "pawc.workflow.review",
            context=ctx,
            start_time=start_ns,
        )
        span.set_attribute("session_id", sid)
        span.set_attribute("phase_id", event.phase_id)
        span.set_attribute("review", event.review)
        span.set_attribute("decision", event.decision)
        span.set_attribute("confidence_score", event.confidence_score)
        if event.total_tokens is not None:
            span.set_attribute("prompt_tokens", event.prompt_tokens or 0)
            span.set_attribute("completion_tokens", event.completion_tokens or 0)
            span.set_attribute("total_tokens", event.total_tokens)
            if event.model:
                span.set_attribute("model", event.model)
        span.end(end_time=end_ns)

    def _trace_phase_transitioned(self, event: PhaseTransitioned) -> None:
        self._end_phase_span(event.session_id)

    def _trace_run_completed(self, event: RunCompleted) -> None:
        from opentelemetry.trace import Status, StatusCode

        sid = event.session_id
        active = self._active_spans.get(sid)
        if active is None:
            return
        self._end_phase_span(sid)
        if active.run_span is not None:
            active.run_span.set_attribute("run.status", event.status)
            active.run_span.set_attribute("run.feedback_loops", event.feedback_loops)
            if event.total_tokens:
                active.run_span.set_attribute("run.total_prompt_tokens", event.total_prompt_tokens)
                active.run_span.set_attribute(
                    "run.total_completion_tokens", event.total_completion_tokens
                )
                active.run_span.set_attribute("run.total_tokens", event.total_tokens)
            active.run_span.set_status(Status(StatusCode.OK))
            active.run_span.end()
            active.run_span = None
        self._active_spans.pop(sid, None)

    def _trace_run_failed(self, event: RunFailed) -> None:
        from opentelemetry.trace import Status, StatusCode

        sid = event.session_id
        active = self._active_spans.get(sid)
        if active is None:
            return
        self._end_phase_span(sid)
        if active.run_span is not None:
            active.run_span.set_attribute("error.type", event.error_type)
            active.run_span.set_attribute("error.message", event.error_message)
            active.run_span.set_status(
                Status(StatusCode.ERROR, description=f"{event.error_type}: {event.error_message}")
            )
            active.run_span.end()
            active.run_span = None
        self._active_spans.pop(sid, None)

    def on_event(self, event: WorkflowEvent) -> None:
        if isinstance(event, RunStarted):
            self._runs.add(1, {"phase_id": event.phase_id, "outcome": "started"})
            self._trace_run_started(event, resumed=False)
        elif isinstance(event, RunResumed):
            self._resumes.add(1, {"phase_id": event.phase_id, "phase_kind": event.phase_kind})
            self._trace_run_started(event, resumed=True)
        elif isinstance(event, PhaseStarted):
            self._phases.add(1, {"phase_id": event.phase_id, "phase_kind": event.phase_kind})
            self._trace_phase_started(event)
        elif isinstance(event, IterationCommitted):
            self._iterations.add(1, {"phase_id": event.phase_id, "outcome": "committed"})
            self._iteration_duration.record(
                _duration_seconds(event.started_at, event.ended_at),
                {"phase_id": event.phase_id},
            )
            if event.total_tokens is not None:
                attrs = {"phase_id": event.phase_id, "model": event.model or "unknown"}
                self._prompt_tokens.add(event.prompt_tokens or 0, attrs)
                self._completion_tokens.add(event.completion_tokens or 0, attrs)
                self._total_tokens.add(event.total_tokens, attrs)
            self._trace_iteration_committed(event)
        elif isinstance(event, ReviewCommitted):
            self._reviews.add(
                1,
                {"phase_id": event.phase_id, "outcome": event.decision.lower()},
            )
            self._review_duration.record(
                _duration_seconds(event.started_at, event.ended_at),
                {"phase_id": event.phase_id},
            )
            if event.total_tokens is not None:
                attrs = {"phase_id": event.phase_id, "model": event.model or "unknown"}
                self._prompt_tokens.add(event.prompt_tokens or 0, attrs)
                self._completion_tokens.add(event.completion_tokens or 0, attrs)
                self._total_tokens.add(event.total_tokens, attrs)
            self._trace_review_committed(event)
        elif isinstance(event, PhaseTransitioned):
            self._transitions.add(
                1,
                {"from_phase": event.from_phase_id, "to_phase": event.to_phase_id},
            )
            self._trace_phase_transitioned(event)
        elif isinstance(event, RunCompleted):
            self._runs.add(1, {"outcome": event.status})
            self._run_duration.record(
                _duration_seconds(event.started_at, event.completed_at),
                {"outcome": event.status},
            )
            self._trace_run_completed(event)
        elif isinstance(event, RunFailed):
            self._failures.add(1, {"phase_id": event.phase_id or "unknown"})
            self._trace_run_failed(event)


class AsyncOpenTelemetryWorkflowObserver:
    """Async OTEL observer that delegates to the sync observer.

    OTEL SDK calls are CPU-bound and do not benefit from ``await``; this class
    follows the same delegation pattern as
    :class:`~pawc_kit.adapters.logging.AsyncLoggingWorkflowObserver`.

    Args:
        meter_name: Forwarded to :class:`OpenTelemetryWorkflowObserver`.
        tracer_name: Forwarded to :class:`OpenTelemetryWorkflowObserver`.
    """

    def __init__(
        self,
        *,
        meter_name: str = _DEFAULT_METER_NAME,
        tracer_name: str = _DEFAULT_TRACER_NAME,
    ) -> None:
        self._delegate = OpenTelemetryWorkflowObserver(
            meter_name=meter_name,
            tracer_name=tracer_name,
        )

    async def on_event(self, event: WorkflowEvent) -> None:
        self._delegate.on_event(event)


__all__ = ["AsyncOpenTelemetryWorkflowObserver", "OpenTelemetryWorkflowObserver"]
