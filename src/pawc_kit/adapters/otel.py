"""OpenTelemetry workflow observer adapter."""

from __future__ import annotations

from datetime import datetime

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

_DEFAULT_METER_NAME = "pawc_kit.workflow"


def _duration_seconds(started_at: str, ended_at: str) -> float:
    return (datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)).total_seconds()


class OpenTelemetryWorkflowObserver:
    """Sync OTEL observer that records metrics for all 8 workflow event types.

    Requires the ``otel`` extra::

        pip install 'pawc-kit[otel]'

    Args:
        meter_name: Name passed to ``opentelemetry.metrics.get_meter()``.
            Defaults to ``"pawc_kit.workflow"``.
    """

    def __init__(self, *, meter_name: str = _DEFAULT_METER_NAME) -> None:
        try:
            from opentelemetry import metrics
        except ImportError as exc:
            raise ImportError(
                "OpenTelemetry support requires the 'otel' extra: pip install 'pawc-kit[otel]'"
            ) from exc

        meter = metrics.get_meter(meter_name)
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

    def on_event(self, event: WorkflowEvent) -> None:
        if isinstance(event, RunStarted):
            self._runs.add(1, {"phase_id": event.phase_id, "outcome": "started"})
        elif isinstance(event, RunResumed):
            self._resumes.add(1, {"phase_id": event.phase_id, "phase_kind": event.phase_kind})
        elif isinstance(event, PhaseStarted):
            self._phases.add(1, {"phase_id": event.phase_id, "phase_kind": event.phase_kind})
        elif isinstance(event, IterationCommitted):
            self._iterations.add(1, {"phase_id": event.phase_id, "outcome": "committed"})
            self._iteration_duration.record(
                _duration_seconds(event.started_at, event.ended_at),
                {"phase_id": event.phase_id},
            )
        elif isinstance(event, ReviewCommitted):
            self._reviews.add(
                1,
                {"phase_id": event.phase_id, "outcome": event.decision.lower()},
            )
            self._review_duration.record(
                _duration_seconds(event.started_at, event.ended_at),
                {"phase_id": event.phase_id},
            )
        elif isinstance(event, PhaseTransitioned):
            self._transitions.add(
                1,
                {"from_phase": event.from_phase_id, "to_phase": event.to_phase_id},
            )
        elif isinstance(event, RunCompleted):
            self._runs.add(1, {"outcome": event.status})
            self._run_duration.record(
                _duration_seconds(event.started_at, event.completed_at),
                {"outcome": event.status},
            )
        elif isinstance(event, RunFailed):
            self._failures.add(1, {"phase_id": event.phase_id or "unknown"})


class AsyncOpenTelemetryWorkflowObserver:
    """Async OTEL observer that delegates to the sync observer.

    OTEL SDK calls are CPU-bound and do not benefit from ``await``; this class
    follows the same delegation pattern as
    :class:`~pawc_kit.adapters.logging.AsyncLoggingWorkflowObserver`.

    Args:
        meter_name: Forwarded to :class:`OpenTelemetryWorkflowObserver`.
    """

    def __init__(self, *, meter_name: str = _DEFAULT_METER_NAME) -> None:
        self._delegate = OpenTelemetryWorkflowObserver(meter_name=meter_name)

    async def on_event(self, event: WorkflowEvent) -> None:
        self._delegate.on_event(event)


__all__ = ["AsyncOpenTelemetryWorkflowObserver", "OpenTelemetryWorkflowObserver"]
