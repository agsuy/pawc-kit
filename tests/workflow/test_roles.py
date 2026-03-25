"""Tests for workflow role protocols and context dataclasses."""

from __future__ import annotations

from pawc_kit._time import utc_now
from pawc_kit.contracts.execution import ExecutionRequest, ReviewRequest
from pawc_kit.ports.artifacts import ArtifactReader
from pawc_kit.workflow.roles import (
    AsyncExecutor,
    AsyncReviewer,
    ExecutionResult,
    Executor,
    ReviewDecision,
    Reviewer,
    ReviewResult,
    WorkflowHistoryView,
)


class _NullReader:
    def load_artifact(self, ref: object) -> bytes:
        return b""


# ---------------------------------------------------------------------------
# WorkflowHistoryView
# ---------------------------------------------------------------------------


def test_workflow_history_view_defaults() -> None:
    hv = WorkflowHistoryView(iterations=[], reviews=[])
    assert hv.previous_decision is None


# ---------------------------------------------------------------------------
# Executor/Reviewer protocol conformance
# ---------------------------------------------------------------------------


class _ConcreteExecutor:
    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="done",
        )


class _ConcreteReviewer:
    def review(self, req: ReviewRequest) -> ReviewResult:
        return ReviewResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            decision=ReviewDecision(
                decision="APPROVE", confidence_score=88, counts_verified=True, summary="ok"
            ),
        )


class _AsyncConcreteExecutor:
    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            confidence_score=90,
            summary="done",
        )


class _AsyncConcreteReviewer:
    async def review(self, req: ReviewRequest) -> ReviewResult:
        return ReviewResult(
            role_id=req.phase.role_id,
            ended_at=utc_now(),
            decision=ReviewDecision(
                decision="APPROVE", confidence_score=88, counts_verified=True, summary="ok"
            ),
        )


def test_concrete_executor_satisfies_protocol() -> None:
    assert isinstance(_ConcreteExecutor(), Executor)


def test_concrete_reviewer_satisfies_protocol() -> None:
    assert isinstance(_ConcreteReviewer(), Reviewer)


def test_async_executor_satisfies_protocol() -> None:
    assert isinstance(_AsyncConcreteExecutor(), AsyncExecutor)


def test_async_reviewer_satisfies_protocol() -> None:
    assert isinstance(_AsyncConcreteReviewer(), AsyncReviewer)


def test_plain_object_does_not_satisfy_executor() -> None:
    assert not isinstance(object(), Executor)


def test_artifact_reader_null_satisfies_protocol() -> None:
    assert isinstance(_NullReader(), ArtifactReader)


# ---------------------------------------------------------------------------
# ReviewDecision
# ---------------------------------------------------------------------------


def test_review_decision_approve() -> None:
    rd = ReviewDecision(
        decision="APPROVE", confidence_score=95, counts_verified=True, summary="good"
    )
    assert rd.decision == "APPROVE"
    assert rd.target_phase is None


def test_review_decision_request_changes_with_target() -> None:
    rd = ReviewDecision(
        decision="REQUEST_CHANGES",
        confidence_score=50,
        counts_verified=False,
        summary="needs work",
        target_phase="work",
    )
    assert rd.target_phase == "work"


# ---------------------------------------------------------------------------
# ExecutionResult
# ---------------------------------------------------------------------------


def test_execution_result_minimal() -> None:
    result = ExecutionResult(
        role_id="worker",
        ended_at=utc_now(),
        confidence_score=80,
        summary="done",
    )
    assert result.artifacts == []
    assert result.handoff is None
    assert result.chosen_next is None
