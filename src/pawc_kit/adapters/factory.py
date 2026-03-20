"""Observer factory: builds sync/async observers from ObservabilityConfig."""

from __future__ import annotations

from pawc_kit.contracts.config import ObservabilityConfig
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.ports.observers import AsyncWorkflowObserver, WorkflowObserver


def build_sync_observer(config: ObservabilityConfig) -> WorkflowObserver | None:
    """Return a sync observer as specified by *config*, or ``None`` for ``"none"``.

    Raises :class:`~pawc_kit.contracts.errors.ConfigurationError` if
    ``config.observer == "otel"`` but the ``otel`` extra is not installed.
    """
    if config.observer == "none":
        return None
    if config.observer == "logging":
        from pawc_kit.adapters.logging import LoggingWorkflowObserver

        return LoggingWorkflowObserver(logger_name=config.logger_name)
    if config.observer == "otel":
        try:
            from pawc_kit.adapters.otel import OpenTelemetryWorkflowObserver
        except ImportError as exc:
            raise ConfigurationError(
                "observability.observer is 'otel' but the 'otel' extra is not installed. "
                "Run: pip install 'pawc-kit[otel]'"
            ) from exc
        return OpenTelemetryWorkflowObserver(
            meter_name=config.meter_name,
            tracer_name=config.tracer_name,
        )
    raise ConfigurationError(  # pragma: no cover  -- exhaustive Literal guard
        f"Unknown observer type: {config.observer!r}"
    )


def build_async_observer(config: ObservabilityConfig) -> AsyncWorkflowObserver | None:
    """Return an async observer as specified by *config*, or ``None`` for ``"none"``.

    Raises :class:`~pawc_kit.contracts.errors.ConfigurationError` if
    ``config.observer == "otel"`` but the ``otel`` extra is not installed.
    """
    if config.observer == "none":
        return None
    if config.observer == "logging":
        from pawc_kit.adapters.logging import AsyncLoggingWorkflowObserver

        return AsyncLoggingWorkflowObserver(logger_name=config.logger_name)
    if config.observer == "otel":
        try:
            from pawc_kit.adapters.otel import AsyncOpenTelemetryWorkflowObserver
        except ImportError as exc:
            raise ConfigurationError(
                "observability.observer is 'otel' but the 'otel' extra is not installed. "
                "Run: pip install 'pawc-kit[otel]'"
            ) from exc
        return AsyncOpenTelemetryWorkflowObserver(
            meter_name=config.meter_name,
            tracer_name=config.tracer_name,
        )
    raise ConfigurationError(  # pragma: no cover  -- exhaustive Literal guard
        f"Unknown observer type: {config.observer!r}"
    )


__all__ = ["build_async_observer", "build_sync_observer"]
