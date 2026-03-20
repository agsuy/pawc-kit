"""Role invocation boundary protocols."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
    from pawc_kit.workflow.graph import PhaseGraph
    from pawc_kit.workflow.roles import ExecutionResult, ReviewResult


@runtime_checkable
class RoleInvoker(Protocol):
    """Sync role dispatch boundary.

    The engine calls ``invoke_executor`` / ``invoke_reviewer`` to dispatch a
    role call.  The invoker owns the lookup and call; the engine owns everything
    else (request construction, result validation, artifact persistence, state
    transitions, and event emission).

    ``validate`` is a pre-flight check called once before the run loop starts.
    ``LocalRoleInvoker`` uses it to verify every phase has a registered role of
    the correct kind.  Custom invokers may implement it as a no-op.
    """

    def invoke_executor(self, req: ExecutionRequest) -> ExecutionResult: ...
    def invoke_reviewer(self, req: ReviewRequest) -> ReviewResult: ...
    def validate(self, graph: PhaseGraph) -> None: ...


@runtime_checkable
class AsyncRoleInvoker(Protocol):
    """Async role dispatch boundary.

    Same contract as ``RoleInvoker`` but for async engines.
    ``validate`` is intentionally sync -- it inspects configuration, not I/O.
    """

    async def invoke_executor(self, req: ExecutionRequest) -> ExecutionResult: ...
    async def invoke_reviewer(self, req: ReviewRequest) -> ReviewResult: ...
    def validate(self, graph: PhaseGraph) -> None: ...


__all__ = [
    "AsyncRoleInvoker",
    "RoleInvoker",
]
