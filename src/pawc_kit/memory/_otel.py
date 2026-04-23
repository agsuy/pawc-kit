"""OpenTelemetry instrumentation for the memory module.

Requires the ``otel`` extra: ``pip install 'pawc-kit[otel]'``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter, Histogram
    from opentelemetry.trace import Tracer

_DEFAULT_METER_NAME = "pawc_kit.memory"
_DEFAULT_TRACER_NAME = "pawc_kit.memory"


class MemoryOtelInstrumentation:
    """Emits spans, counters, and histograms for memory operations.

    Parameters
    ----------
    meter_name:
        Name passed to ``opentelemetry.metrics.get_meter()``.
    tracer_name:
        Name passed to ``opentelemetry.trace.get_tracer()``.
    """

    def __init__(
        self,
        *,
        meter_name: str = _DEFAULT_METER_NAME,
        tracer_name: str = _DEFAULT_TRACER_NAME,
        meter_provider: Any = None,
        tracer_provider: Any = None,
    ) -> None:
        try:
            from opentelemetry import metrics, trace
        except ImportError as exc:
            raise ImportError(
                "OpenTelemetry support requires the 'otel' extra: "
                "pip install 'pawc-kit[otel]'"
            ) from exc

        if meter_provider is not None:
            meter = meter_provider.get_meter(meter_name)
        else:
            meter = metrics.get_meter(meter_name)

        if tracer_provider is not None:
            self._tracer: Tracer = tracer_provider.get_tracer(tracer_name)
        else:
            self._tracer = trace.get_tracer(tracer_name)

        # Counters
        self._store_invocations: Counter = meter.create_counter(
            "pawc.memory.store.invocations",
            description="Number of store() calls.",
        )
        self._chunks_created: Counter = meter.create_counter(
            "pawc.memory.chunks.created",
            description="Number of new chunks created.",
        )
        self._chunks_unchanged: Counter = meter.create_counter(
            "pawc.memory.chunks.unchanged",
            description="Number of chunks skipped (unchanged content).",
        )
        self._chunks_deleted: Counter = meter.create_counter(
            "pawc.memory.chunks.deleted",
            description="Number of chunks deleted.",
        )
        self._query_invocations: Counter = meter.create_counter(
            "pawc.memory.query.invocations",
            description="Number of query() calls.",
        )
        self._worker_batches: Counter = meter.create_counter(
            "pawc.memory.worker.batches",
            description="Number of worker batches processed.",
        )
        self._worker_errors: Counter = meter.create_counter(
            "pawc.memory.worker.errors",
            description="Number of worker batch errors.",
        )
        self._reconcile_requeued: Counter = meter.create_counter(
            "pawc.memory.reconcile.requeued",
            description="Number of chunks re-queued by reconcile.",
        )

        # Histograms
        self._store_duration: Histogram = meter.create_histogram(
            "pawc.memory.store.duration",
            description="Duration of store() calls in seconds.",
            unit="s",
        )
        self._query_duration: Histogram = meter.create_histogram(
            "pawc.memory.query.duration",
            description="Duration of query() calls in seconds.",
            unit="s",
        )
        self._worker_batch_duration: Histogram = meter.create_histogram(
            "pawc.memory.worker.batch_duration",
            description="Duration of worker batch processing in seconds.",
            unit="s",
        )

    # -- Instrumentation hooks -----------------------------------------------

    def on_store_start(self) -> float:
        """Call at the start of store(). Returns start time."""
        self._store_invocations.add(1)
        return time.monotonic()

    def on_store_end(
        self,
        start_time: float,
        *,
        created: int,
        unchanged: int,
        deleted: int,
    ) -> None:
        """Call at the end of store()."""
        duration = time.monotonic() - start_time
        self._store_duration.record(duration)
        self._chunks_created.add(created)
        self._chunks_unchanged.add(unchanged)
        self._chunks_deleted.add(deleted)

    def on_query_start(self) -> float:
        """Call at the start of query(). Returns start time."""
        self._query_invocations.add(1)
        return time.monotonic()

    def on_query_end(self, start_time: float) -> None:
        """Call at the end of query()."""
        duration = time.monotonic() - start_time
        self._query_duration.record(duration)

    def on_worker_batch(self, duration: float, *, chunk_count: int) -> None:
        """Call after a worker batch completes."""
        self._worker_batches.add(1)
        self._worker_batch_duration.record(duration)

    def on_worker_error(self) -> None:
        """Call when a worker batch fails."""
        self._worker_errors.add(1)

    def on_reconcile(self, *, requeued: int) -> None:
        """Call after reconcile()."""
        self._reconcile_requeued.add(requeued)

    @property
    def tracer(self) -> Tracer:
        """The OpenTelemetry tracer for creating spans."""
        return self._tracer


__all__ = [
    "MemoryOtelInstrumentation",
]
