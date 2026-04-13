"""Standard-library logging observers for workflow lifecycle events."""

from __future__ import annotations

import logging
from dataclasses import asdict

from pawc_kit.contracts.events import (
    CompressionCompleted,
    IterationCommitted,
    PhaseStarted,
    PhaseTransitioned,
    ReviewCommitted,
    RunCompleted,
    RunFailed,
    RunResumed,
    RunStarted,
    ToolBudgetExhausted,
    ToolCallExecuted,
    ToolCallFailed,
    ToolCallFallback,
    WorkflowEvent,
)

_DEFAULT_LOGGER_NAME = "pawc_kit.workflow"


def _event_level(event: WorkflowEvent) -> int:
    if isinstance(event, RunFailed):
        return logging.ERROR
    if isinstance(event, (ToolCallFailed, ToolBudgetExhausted)):
        return logging.WARNING
    if isinstance(event, ToolCallFallback):
        return logging.WARNING
    if isinstance(event, RunCompleted):
        return logging.INFO if event.status == "completed" else logging.WARNING
    if isinstance(event, RunStarted):
        return logging.INFO
    if isinstance(event, RunResumed):
        return logging.INFO
    if isinstance(event, ReviewCommitted):
        return logging.DEBUG if event.decision == "APPROVE" else logging.WARNING
    return logging.DEBUG


def _event_message(event: WorkflowEvent) -> str:
    if isinstance(event, RunStarted):
        return "Run started"
    if isinstance(event, RunResumed):
        return "Run resumed"
    if isinstance(event, PhaseStarted):
        return "Phase started"
    if isinstance(event, IterationCommitted):
        return "Iteration committed"
    if isinstance(event, ReviewCommitted):
        return "Review committed"
    if isinstance(event, PhaseTransitioned):
        return "Phase transitioned"
    if isinstance(event, RunCompleted):
        return "Run completed" if event.status == "completed" else "Run abandoned"
    if isinstance(event, RunFailed):
        return "Run failed"
    if isinstance(event, ToolCallExecuted):
        status = "error" if event.is_error else "ok"
        return f"Tool call {status}: {event.tool_name} ({event.integration_id}) {event.duration_ms}ms"
    if isinstance(event, ToolCallFailed):
        return f"Tool call failed: {event.tool_name} ({event.error_type}): {event.error}"
    if isinstance(event, ToolCallFallback):
        return f"Tool fallback: {event.capability} {event.from_integration} -> {event.to_integration}"
    if isinstance(event, ToolBudgetExhausted):
        return f"Tool budget exhausted: {event.limit_type} ({event.current_value}/{event.limit_value})"
    if isinstance(event, CompressionCompleted):
        ratio = f"{event.original_chars}->{event.final_chars}"
        dropped = f"{event.sections_dropped}/{event.sections_total} dropped"
        mode = f"overflow={event.overflow}"
        batches = f" batches={event.quality_batches}" if event.quality_batches else ""
        return f"Compression: {event.filename} {ratio} ({dropped}, {mode}{batches})"
    return type(event).__name__


def _event_extra(event: WorkflowEvent) -> dict[str, object]:
    extra: dict[str, object] = {"pawc_event": type(event).__name__}
    for key, value in asdict(event).items():
        extra[f"pawc_{key}"] = value
    return extra


class LoggingWorkflowObserver:
    """Sync observer that logs committed workflow events via ``logging``."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        *,
        logger_name: str = _DEFAULT_LOGGER_NAME,
    ) -> None:
        self._logger = logger or logging.getLogger(logger_name)

    def on_event(self, event: WorkflowEvent) -> None:
        self._logger.log(
            _event_level(event),
            _event_message(event),
            extra=_event_extra(event),
        )


class AsyncLoggingWorkflowObserver:
    """Async observer that logs committed workflow events via ``logging``."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        *,
        logger_name: str = _DEFAULT_LOGGER_NAME,
    ) -> None:
        self._delegate = LoggingWorkflowObserver(logger, logger_name=logger_name)

    async def on_event(self, event: WorkflowEvent) -> None:
        self._delegate.on_event(event)


__all__ = ["AsyncLoggingWorkflowObserver", "LoggingWorkflowObserver"]
