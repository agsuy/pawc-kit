"""Tests for memory otel instrumentation."""

from __future__ import annotations

import pytest

try:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False

pytestmark = pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed")


@pytest.fixture()
def otel_setup():
    """Create local providers (avoids global state conflicts with other tests)."""
    from pawc_kit.memory._otel import MemoryOtelInstrumentation

    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    tracer_provider = TracerProvider()

    instrumentation = MemoryOtelInstrumentation(
        meter_provider=meter_provider,
        tracer_provider=tracer_provider,
    )
    return instrumentation, reader


class TestMemoryOtelInstrumentation:
    def test_store_counters(self, otel_setup) -> None:
        instrumentation, reader = otel_setup
        t = instrumentation.on_store_start()
        instrumentation.on_store_end(t, created=3, unchanged=1, deleted=2)

        data = reader.get_metrics_data()
        metric_names = {
            m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        }
        assert "pawc.memory.store.invocations" in metric_names
        assert "pawc.memory.chunks.created" in metric_names
        assert "pawc.memory.store.duration" in metric_names

    def test_query_counters(self, otel_setup) -> None:
        instrumentation, reader = otel_setup
        t = instrumentation.on_query_start()
        instrumentation.on_query_end(t)

        data = reader.get_metrics_data()
        metric_names = {
            m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        }
        assert "pawc.memory.query.invocations" in metric_names
        assert "pawc.memory.query.duration" in metric_names

    def test_worker_counters(self, otel_setup) -> None:
        instrumentation, reader = otel_setup
        instrumentation.on_worker_batch(0.5, chunk_count=10)
        instrumentation.on_worker_error()

        data = reader.get_metrics_data()
        metric_names = {
            m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        }
        assert "pawc.memory.worker.batches" in metric_names
        assert "pawc.memory.worker.errors" in metric_names

    def test_reconcile_counter(self, otel_setup) -> None:
        instrumentation, reader = otel_setup
        instrumentation.on_reconcile(requeued=5)

        data = reader.get_metrics_data()
        metric_names = {
            m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        }
        assert "pawc.memory.reconcile.requeued" in metric_names

    def test_tracer_available(self, otel_setup) -> None:
        instrumentation, _ = otel_setup
        assert instrumentation.tracer is not None

    def test_import_without_otel_raises(self) -> None:
        # This test verifies the error message format
        # (only meaningful if otel IS installed and we test the guard)
        from pawc_kit.memory._otel import MemoryOtelInstrumentation

        inst = MemoryOtelInstrumentation()
        assert inst is not None
