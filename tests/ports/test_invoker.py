"""Tests for RoleInvoker / AsyncRoleInvoker protocols and LocalRoleInvoker adapter."""

from __future__ import annotations

import asyncio

import pytest

from conftest import MinimalReviewer, MinimalWorker, make_simple_graph
from pawc_kit._time import utc_now
from pawc_kit.adapters.local_invoker import AsyncLocalRoleInvoker, LocalRoleInvoker
from pawc_kit.contracts.errors import ConfigurationError, TransitionError
from pawc_kit.contracts.execution import ContextPayload, ExecutionRequest, ReviewRequest
from pawc_kit.contracts.state import SessionState
from pawc_kit.ports.invoker import AsyncRoleInvoker, RoleInvoker
from pawc_kit.workflow.graph import PhaseGraph
from pawc_kit.workflow.roles import (
    ExecutionResult,
    ReviewDecision,
    ReviewResult,
    WorkflowHistoryView,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_state() -> SessionState:
    return SessionState(
        session_id="s1",
        skill_name="skill",
        skill_version="1.0.0",
        started_at="2026-01-01T00:00:00Z",
        current_phase="work",
        status="in_progress",
    )


def _exec_req(graph: PhaseGraph) -> ExecutionRequest:
    phase = graph.get("work")
    return ExecutionRequest(
        session=_minimal_state(),
        phase=phase,
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        context=ContextPayload.empty(),
    )


def _review_req(graph: PhaseGraph) -> ReviewRequest:
    phase = graph.get("review")
    return ReviewRequest(
        session=_minimal_state(),
        phase=phase,
        history=WorkflowHistoryView(iterations=[], reviews=[]),
        context=ContextPayload.empty(),
    )


# ---------------------------------------------------------------------------
# Protocol conformance: isinstance checks
# ---------------------------------------------------------------------------


def test_local_invoker_satisfies_role_invoker_protocol() -> None:
    invoker = LocalRoleInvoker()
    assert isinstance(invoker, RoleInvoker)


def test_async_local_invoker_satisfies_async_role_invoker_protocol() -> None:
    invoker = AsyncLocalRoleInvoker()
    assert isinstance(invoker, AsyncRoleInvoker)


# ---------------------------------------------------------------------------
# LocalRoleInvoker: register_role and dispatch
# ---------------------------------------------------------------------------


def test_local_invoker_dispatches_to_executor() -> None:
    graph = make_simple_graph()
    invoker = LocalRoleInvoker()
    invoker.register_role("worker-role", MinimalWorker())
    invoker.register_role("reviewer-role", MinimalReviewer())

    result = invoker.invoke_executor(_exec_req(graph))
    assert isinstance(result, ExecutionResult)


def test_local_invoker_dispatches_to_reviewer() -> None:
    graph = make_simple_graph()
    invoker = LocalRoleInvoker()
    invoker.register_role("worker-role", MinimalWorker())
    invoker.register_role("reviewer-role", MinimalReviewer())

    result = invoker.invoke_reviewer(_review_req(graph))
    assert isinstance(result, ReviewResult)


def test_local_invoker_invoke_executor_wrong_kind_raises_transition_error() -> None:
    graph = make_simple_graph()
    invoker = LocalRoleInvoker()
    invoker.register_role("worker-role", MinimalReviewer())

    with pytest.raises(TransitionError):
        invoker.invoke_executor(_exec_req(graph))


def test_local_invoker_invoke_reviewer_wrong_kind_raises_transition_error() -> None:
    graph = make_simple_graph()
    invoker = LocalRoleInvoker()
    invoker.register_role("reviewer-role", MinimalWorker())

    with pytest.raises(TransitionError):
        invoker.invoke_reviewer(_review_req(graph))


# ---------------------------------------------------------------------------
# LocalRoleInvoker: validate
# ---------------------------------------------------------------------------


def test_local_invoker_validate_passes_when_all_roles_bound() -> None:
    graph = make_simple_graph()
    invoker = LocalRoleInvoker()
    invoker.register_role("worker-role", MinimalWorker())
    invoker.register_role("reviewer-role", MinimalReviewer())
    invoker.validate(graph)


def test_local_invoker_validate_raises_on_missing_role() -> None:
    graph = make_simple_graph()
    invoker = LocalRoleInvoker()
    invoker.register_role("worker-role", MinimalWorker())

    with pytest.raises(ConfigurationError, match="unregistered"):
        invoker.validate(graph)


def test_local_invoker_validate_raises_on_wrong_kind_executor() -> None:
    graph = make_simple_graph()
    invoker = LocalRoleInvoker()
    invoker.register_role("worker-role", MinimalReviewer())
    invoker.register_role("reviewer-role", MinimalReviewer())

    with pytest.raises(ConfigurationError, match="non-executor"):
        invoker.validate(graph)


def test_local_invoker_validate_raises_on_wrong_kind_reviewer() -> None:
    graph = make_simple_graph()
    invoker = LocalRoleInvoker()
    invoker.register_role("worker-role", MinimalWorker())
    invoker.register_role("reviewer-role", MinimalWorker())

    with pytest.raises(ConfigurationError, match="non-reviewer"):
        invoker.validate(graph)


# ---------------------------------------------------------------------------
# AsyncLocalRoleInvoker: dispatch and validate
# ---------------------------------------------------------------------------


class AsyncWorker:
    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="done",
        )


class AsyncReviewerDouble:
    async def review(self, req: ReviewRequest) -> ReviewResult:
        return ReviewResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            decision=ReviewDecision(
                decision="APPROVE",
                confidence_score=90,
                counts_verified=True,
                summary="ok",
            ),
        )


def test_async_local_invoker_dispatches_to_executor() -> None:
    graph = make_simple_graph()
    invoker = AsyncLocalRoleInvoker()
    invoker.register_role("worker-role", AsyncWorker())
    invoker.register_role("reviewer-role", AsyncReviewerDouble())

    result = asyncio.run(invoker.invoke_executor(_exec_req(graph)))
    assert isinstance(result, ExecutionResult)


def test_async_local_invoker_dispatches_to_reviewer() -> None:
    graph = make_simple_graph()
    invoker = AsyncLocalRoleInvoker()
    invoker.register_role("worker-role", AsyncWorker())
    invoker.register_role("reviewer-role", AsyncReviewerDouble())

    result = asyncio.run(invoker.invoke_reviewer(_review_req(graph)))
    assert isinstance(result, ReviewResult)


def test_async_local_invoker_validate_passes() -> None:
    graph = make_simple_graph()
    invoker = AsyncLocalRoleInvoker()
    invoker.register_role("worker-role", AsyncWorker())
    invoker.register_role("reviewer-role", AsyncReviewerDouble())
    invoker.validate(graph)


def test_async_local_invoker_validate_raises_on_missing_role() -> None:
    graph = make_simple_graph()
    invoker = AsyncLocalRoleInvoker()
    invoker.register_role("worker-role", AsyncWorker())

    with pytest.raises(ConfigurationError, match="unregistered"):
        invoker.validate(graph)
