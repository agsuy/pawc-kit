"""Workflow observer protocols."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pawc_kit.contracts.events import WorkflowEvent


@runtime_checkable
class WorkflowObserver(Protocol):
    """Sync observer notified after durable workflow commits."""

    def on_event(self, event: WorkflowEvent) -> None: ...


@runtime_checkable
class AsyncWorkflowObserver(Protocol):
    """Async observer notified after durable workflow commits."""

    async def on_event(self, event: WorkflowEvent) -> None: ...


__all__ = ["AsyncWorkflowObserver", "WorkflowObserver"]
