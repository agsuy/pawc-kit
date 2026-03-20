"""Workflow observer protocols.

The observer port is an emit-only notification boundary.  The engine calls
``on_event`` synchronously after each durable state commit, providing a fully
typed ``WorkflowEvent`` with session, phase, role, revision, and timestamp
data.  Event ordering is guaranteed by the engine's control flow.

Durable event storage, replay, filtering, pagination, and history queries are
consumer concerns — a server or adapter that needs those capabilities should
implement the observer to append events to its own store and build read
semantics on top.  This keeps ``pawc-kit`` free from storage-backend
assumptions while giving consumers everything they need on the write side.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pawc_kit.contracts.events import WorkflowEvent


@runtime_checkable
class WorkflowObserver(Protocol):
    """Sync observer notified after each durable workflow commit.

    Implementations receive every committed ``WorkflowEvent`` in engine order.
    Use this to drive side effects (logging, metrics, traces) or to append
    events to a durable store for later querying.  Read-side semantics (replay,
    filtering, pagination) belong to the store, not to this protocol.
    """

    def on_event(self, event: WorkflowEvent) -> None: ...


@runtime_checkable
class AsyncWorkflowObserver(Protocol):
    """Async observer notified after each durable workflow commit.

    Async counterpart of :class:`WorkflowObserver`.  Same ordering guarantees
    and design intent apply.
    """

    async def on_event(self, event: WorkflowEvent) -> None: ...


__all__ = ["AsyncWorkflowObserver", "WorkflowObserver"]
