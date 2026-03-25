"""In-process role invoker adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pawc_kit.contracts.errors import ConfigurationError, TransitionError
from pawc_kit.workflow.roles import (
    AsyncExecutor,
    AsyncReviewer,
    ExecutionResult,
    Executor,
    Reviewer,
    ReviewResult,
)

if TYPE_CHECKING:
    from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
    from pawc_kit.workflow.graph import PhaseGraph


def _validate_bindings(
    bindings: dict[str, object],
    graph: "PhaseGraph",
    executor_type: type,
    reviewer_type: type,
) -> None:
    """Raise ``ConfigurationError`` if any phase lacks a valid binding."""
    for phase_id in graph.phase_ids:
        phase = graph.get(phase_id)
        role = bindings.get(phase.role_id)
        if role is None:
            raise ConfigurationError(
                f"Phase {phase.phase_id!r} references unregistered "
                f"role_id {phase.role_id!r}"
            )
        if phase.kind == "executor" and not isinstance(role, executor_type):
            raise ConfigurationError(
                f"role_id {phase.role_id!r} is bound to a "
                "non-executor implementation"
            )
        if phase.kind == "review" and not isinstance(role, reviewer_type):
            raise ConfigurationError(
                f"role_id {phase.role_id!r} is bound to a "
                "non-reviewer implementation"
            )


class LocalRoleInvoker:
    """Default in-process invoker for sync engines.

    Wraps a dict of role bindings.  Use ``register_role`` to populate after
    construction, then pass the invoker to ``WorkflowEngine`` via the
    ``invoker`` kwarg.

    ``validate`` performs the same pre-flight checks previously done by
    ``WorkflowEngine._validate_role_bindings``.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, Executor | Reviewer] = {}

    def register_role(self, role_id: str, role: Executor | Reviewer) -> None:
        """Bind a role implementation to a ``role_id``."""
        self._bindings[role_id] = role

    def validate(self, graph: PhaseGraph) -> None:
        _validate_bindings(self._bindings, graph, Executor, Reviewer)

    def invoke_executor(self, req: ExecutionRequest) -> ExecutionResult:
        """Dispatch to a registered ``Executor``."""
        role = self._bindings.get(req.phase.role_id)
        if not isinstance(role, Executor):
            raise TransitionError(
                f"No Executor registered for role_id {req.phase.role_id!r}"
                f" in phase {req.phase.phase_id!r}"
            )
        return role.execute(req)

    def invoke_reviewer(self, req: ReviewRequest) -> ReviewResult:
        """Dispatch to a registered ``Reviewer``."""
        role = self._bindings.get(req.phase.role_id)
        if not isinstance(role, Reviewer):
            raise TransitionError(
                f"No Reviewer registered for role_id {req.phase.role_id!r}"
                f" in phase {req.phase.phase_id!r}"
            )
        return role.review(req)


class AsyncLocalRoleInvoker:
    """Default in-process invoker for async engines.

    Same contract as ``LocalRoleInvoker`` but dispatches to ``AsyncExecutor``
    and ``AsyncReviewer`` instances.  ``validate`` is sync (config inspection,
    no I/O).
    """

    def __init__(self) -> None:
        self._bindings: dict[str, AsyncExecutor | AsyncReviewer] = {}

    def register_role(self, role_id: str, role: AsyncExecutor | AsyncReviewer) -> None:
        """Bind a role implementation to a ``role_id``."""
        self._bindings[role_id] = role

    def validate(self, graph: PhaseGraph) -> None:
        _validate_bindings(
            self._bindings, graph, AsyncExecutor, AsyncReviewer
        )

    async def invoke_executor(self, req: ExecutionRequest) -> ExecutionResult:
        """Dispatch to a registered ``AsyncExecutor``."""
        role = self._bindings.get(req.phase.role_id)
        if not isinstance(role, AsyncExecutor):
            raise TransitionError(
                f"No AsyncExecutor registered for role_id {req.phase.role_id!r}"
                f" in phase {req.phase.phase_id!r}"
            )
        return await role.execute(req)

    async def invoke_reviewer(self, req: ReviewRequest) -> ReviewResult:
        """Dispatch to a registered ``AsyncReviewer``."""
        role = self._bindings.get(req.phase.role_id)
        if not isinstance(role, AsyncReviewer):
            raise TransitionError(
                f"No AsyncReviewer registered for role_id {req.phase.role_id!r}"
                f" in phase {req.phase.phase_id!r}"
            )
        return await role.review(req)


__all__ = [
    "AsyncLocalRoleInvoker",
    "LocalRoleInvoker",
]
